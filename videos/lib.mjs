/**
 * Shared recording pipeline for the Aadi Identity sprint videos, reused from the DMS pipeline (videos/lib.mjs in the DMS project). It is the CRM series pipeline from
 * AadiProduct/dev-environment/videos/recordings/record-05-crm-rbac.mjs (Playwright viewport capture at
 * 1600x900, Microsoft Zira Desktop narration, FFmpeg H.264/AAC, SRT and VTT) in one reusable place.
 * A recorder script supplies the scenes; this file does the speech, timing, mixing and reports.
 * The demo password is read only to be typed into masked fields.
 */
import { spawn } from 'node:child_process'
import crypto from 'node:crypto'
import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'
import { performance } from 'node:perf_hooks'
import { fileURLToPath } from 'node:url'

const require = createRequire(
  'C:/Users/lenovo/AadiTech/Product/AadiProduct/MultiTenant/Aadi-MultiTenant-Platform/frontend/package.json',
)
const { chromium } = require('playwright')

export const here = path.dirname(fileURLToPath(import.meta.url))
export const repo = path.resolve(here, '..')
export const origin = process.env.IDENTITY_URL ?? 'https://id-demo.aadiprojecttracker.aaditechnology.com'
const workDir = path.join(here, '.work')
const frameDir = path.join(here, 'verification')
const speechScript = path.join(workDir, 'speak.ps1')

export function loadPassword() {
  return process.env.DEMO_PASSWORD ?? 'Demo-Password-1'
}

function run(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { windowsHide: true })
    let stderr = ''
    child.stderr.on('data', (chunk) => (stderr += chunk.toString()))
    child.on('error', reject)
    child.on('close', (code) =>
      code === 0 ? resolve(stderr) : reject(new Error(`${command} exited ${code}: ${stderr.slice(0, 800)}`)),
    )
  })
}

function ensureSpeechScript() {
  fs.mkdirSync(workDir, { recursive: true })
  fs.writeFileSync(
    speechScript,
    `
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$text = [System.IO.File]::ReadAllText($env:SPEAK_TEXT, [System.Text.Encoding]::UTF8)
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.Rate = 0
$synth.SelectVoice('Microsoft Zira Desktop')
$synth.SetOutputToWaveFile($env:SPEAK_WAV)
$synth.Speak($text)
$synth.Dispose()
`.trim(),
    'utf8',
  )
}

async function synthesize(narration) {
  const id = crypto.createHash('sha256').update(narration).digest('hex').slice(0, 16)
  const wav = path.join(workDir, `${id}.wav`)
  if (!fs.existsSync(wav) || fs.statSync(wav).size < 1000) {
    const textFile = path.join(workDir, `${id}.txt`)
    fs.writeFileSync(textFile, narration, 'utf8')
    await new Promise((resolve, reject) => {
      const child = spawn('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', speechScript], {
        windowsHide: true,
        env: { ...process.env, SPEAK_TEXT: textFile, SPEAK_WAV: wav },
      })
      let stderr = ''
      child.stderr.on('data', (chunk) => (stderr += chunk.toString()))
      child.on('error', reject)
      child.on('close', (code) => (code === 0 ? resolve() : reject(new Error(`Speech failed (${code}): ${stderr.slice(0, 400)}`))))
    })
  }
  return wav
}

async function probeDuration(file) {
  const child = spawn('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', file], { windowsHide: true })
  let stdout = ''
  child.stdout.on('data', (chunk) => (stdout += chunk.toString()))
  const code = await new Promise((resolve) => child.on('close', resolve))
  const seconds = Number(stdout.trim())
  if (code !== 0 || !Number.isFinite(seconds) || seconds <= 0) throw new Error(`ffprobe failed for ${path.basename(file)}`)
  return seconds
}

/** Move the pointer to an element, pause briefly, then click it (so the recording shows where). */
export async function click(locator) {
  await locator.waitFor({ state: 'visible' })
  await locator.scrollIntoViewIfNeeded()
  const box = await locator.boundingBox()
  if (box) {
    await locator.page().mouse.move(box.x + Math.min(28, box.width / 2), box.y + Math.min(18, box.height / 2))
    await locator.page().waitForTimeout(250)
  }
  await locator.click()
}

function stamp(seconds) {
  const whole = Math.max(0, seconds)
  const pad = (value, size = 2) => String(value).padStart(size, '0')
  return `${pad(Math.floor(whole / 3600))}:${pad(Math.floor((whole % 3600) / 60))}:${pad(Math.floor(whole % 60))},${pad(Math.round((whole - Math.floor(whole)) * 1000), 3)}`
}

function writeSubtitles(name, cues) {
  const srt = []
  const vtt = ['WEBVTT', '']
  cues.forEach((cue, index) => {
    const start = cue.start
    const end = cue.start + cue.seconds
    srt.push(String(index + 1), `${stamp(start)} --> ${stamp(end)}`, cue.narration, '')
    vtt.push(`${stamp(start).replace(',', '.')} --> ${stamp(end).replace(',', '.')}`, cue.narration, '')
  })
  fs.writeFileSync(path.join(here, `${name}.srt`), srt.join('\n'), 'utf8')
  fs.writeFileSync(path.join(here, `${name}.vtt`), vtt.join('\n'), 'utf8')
}

// Same column order as ScriptToVideo slides.csv, so the narration can be reused with that tool.
function writeCsv(name, scenes) {
  const q = (value) => `"${String(value).replace(/"/g, '""')}"`
  const rows = ['Slide,Module,action,expected_result,verification_status,image,screenshot,voice_text,subtitle_text,visual_instruction']
  scenes.forEach((scene, index) => {
    const n = String(index + 1).padStart(2, '0')
    rows.push(
      [index + 1, q(scene.chapter.replace(/^Chapter \d+ — /, '')), q(scene.actions), q(scene.expected), 'VERIFIED', `${n}.png`, '', q(scene.narration), q(scene.title), q(scene.actions)].join(','),
    )
  })
  fs.writeFileSync(path.join(here, `${name}.csv`), rows.join('\n') + '\n', 'utf8')
}

async function mux(webm, cues, destination, mobile = false) {
  const videoSeconds = await probeDuration(webm)
  const last = cues[cues.length - 1]
  const audioEnd = last.start + last.seconds + 0.4
  const total = Math.max(videoSeconds, audioEnd)
  const extra = Math.max(0, audioEnd - videoSeconds)
  const filter = []
  const inputs = ['-y', '-i', webm]
  cues.forEach((cue, index) => {
    inputs.push('-i', cue.wav)
    const delay = Math.round(Math.max(0, cue.start) * 1000)
    filter.push(`[${index + 1}:a]adelay=${delay}|${delay},apad=pad_dur=0.2,aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[a${index}]`)
  })
  filter.push(`${cues.map((_, index) => `[a${index}]`).join('')}amix=inputs=${cues.length}:duration=longest:dropout_transition=0:normalize=0,atrim=0:${total.toFixed(3)},asetpts=PTS-STARTPTS[aout]`)
  // a phone is recorded at 390 x 844 and enlarged to twice that size so it is readable when played back
  filter.push(`[0:v]${extra > 0.05 ? `tpad=stop_mode=clone:stop_duration=${extra.toFixed(3)}` : 'null'}${mobile ? ',scale=780:1688:flags=lanczos' : ''},format=yuv420p[vout]`)
  await run('ffmpeg', [...inputs, '-filter_complex', filter.join(';'), '-map', '[vout]', '-map', '[aout]', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', destination])
  return probeDuration(destination)
}

async function extractFrames(name, file, cues) {
  fs.mkdirSync(frameDir, { recursive: true })
  for (let index = 0; index < cues.length; index += 1) {
    const at = Math.max(0.4, cues[index].start + Math.min(cues[index].seconds * 0.7, 6))
    await run('ffmpeg', ['-y', '-ss', at.toFixed(2), '-i', file, '-frames:v', '1', '-q:v', '3', path.join(frameDir, `${name.slice(0, 2)}-${String(index + 1).padStart(2, '0')}.jpg`)])
  }
}

async function launchBrowser() {
  let lastError = new Error('No browser could be launched.')
  for (const options of [{ channel: 'chrome' }, { channel: 'msedge' }, {}]) {
    try {
      return await chromium.launch({ headless: true, slowMo: 60, ...options })
    } catch (error) {
      lastError = error instanceof Error ? error : new Error(String(error))
    }
  }
  throw lastError
}

/**
 * Record `scenes` (each: chapter, title, narration, actions, expected, run(page)) into
 * videos/<name>.mp4 with narration, .srt, .vtt, a narration .csv and one verification frame per scene.
 * `check(page)` runs after every scene (used to make sure no secret is on screen).
 */
export async function recordVideo({ name, scenes, password, check, mobile = false, fixedTime = null }) {
  const FAST = !!process.env.FAST
  ensureSpeechScript()
  const prepared = []
  for (const scene of scenes) {
    if (FAST) { prepared.push({ ...scene, wav: null, seconds: 0.1 }); continue }
    const wav = await synthesize(scene.narration)
    prepared.push({ ...scene, wav, seconds: await probeDuration(wav) })
  }
  const browser = await launchBrowser()
  const context = await browser.newContext({
    // mobile = a 390 x 844 touch phone, enlarged to twice the size when the video is made so the picture stays readable.
    viewport: mobile ? { width: 390, height: 844 } : { width: 1600, height: 900 },
    ...(mobile ? { isMobile: true, hasTouch: true, deviceScaleFactor: 2 } : {}),
    locale: 'en-US',
    timezoneId: 'Asia/Kolkata',
    colorScheme: 'light',
    recordVideo: { dir: workDir, size: mobile ? { width: 390, height: 844 } : { width: 1600, height: 900 } },
  })
  // fixedTime pins the browser's clock so dates that default to "today" fall inside the demo period (October 2026)
  if (fixedTime) await context.clock.setFixedTime(new Date(fixedTime))
  const page = await context.newPage()
  page.setDefaultTimeout(30000)
  page.setDefaultNavigationTimeout(30000)
  const t0 = performance.now()
  const cues = []
  const video = page.video()
  try {
    for (let index = 0; index < prepared.length; index += 1) {
      const scene = prepared[index]
      console.log(`scene ${index + 1}/${prepared.length} ${scene.title}`)
      const start = (performance.now() - t0) / 1000
      cues.push({ start, seconds: scene.seconds, narration: scene.narration, wav: scene.wav })
      await scene.run(page)
      if (check) await check(page)
      const elapsed = (performance.now() - t0) / 1000 - start
      await page.waitForTimeout((FAST ? 0.3 : Math.max(scene.seconds + 0.45 - elapsed, 2.5)) * 1000)
    }
    await page.waitForTimeout(600)
  } catch (error) {
    fs.mkdirSync(frameDir, { recursive: true })
    await page.screenshot({ path: path.join(frameDir, 'failure.jpg') }).catch(() => {})
    throw error
  } finally {
    await context.close()
    await browser.close()
  }
  if (FAST) { console.log('FAST dry run finished: all scenes ran'); return }
  const webm = await video.path()
  const destination = path.join(here, `${name}.mp4`)
  const duration = await mux(webm, cues, destination, mobile)
  fs.rmSync(webm, { force: true })
  writeSubtitles(name, cues)
  writeCsv(name, scenes)
  await extractFrames(name, destination, cues)
  console.log(JSON.stringify({ file: destination, duration: Number(duration.toFixed(1)), scenes: cues.length }, null, 2))
}

export function redactor(password) {
  return (error) => {
    const message = error instanceof Error ? (error.stack ?? error.message) : String(error)
    return message.split(password).join('[redacted]')
  }
}
