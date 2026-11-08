/** Helpers shared by the Identity sprint recorders. The demo password is typed only into masked fields. */
import { click, loadPassword, origin } from './lib.mjs'

export const password = loadPassword()
export { click, origin }

export async function signIn(page, email) {
  await page.goto(origin + '/login', { waitUntil: 'domcontentloaded' })
  await page.getByLabel('Email').fill(email)
  const field = page.getByLabel('Password')
  await field.fill(password)
  if ((await field.getAttribute('type')) !== 'password') throw new Error('The password field is not masked')
  await click(page.getByRole('button', { name: 'Sign in', exact: true }))
  await page.waitForLoadState('domcontentloaded')
}

/** Sign in typing slowly so the viewer can follow. */
export async function signInSlow(page, email) {
  await page.goto(origin + '/login', { waitUntil: 'domcontentloaded' })
  await page.getByLabel('Email').click()
  await page.getByLabel('Email').pressSequentially(email, { delay: 45 })
  await page.getByLabel('Password').fill(password)
  await click(page.getByRole('button', { name: 'Sign in', exact: true }))
  await page.waitForLoadState('domcontentloaded')
}

export async function signOutEverywhere(page) {
  await page.goto(origin + '/logout', { waitUntil: 'domcontentloaded' })
}

export async function assertNoSecret(page) {
  // the password is only ever typed into a masked field; it must never appear as page text
  if ((await page.locator('body').innerText()).includes(password)) throw new Error('The demo password is visible on screen')
}
