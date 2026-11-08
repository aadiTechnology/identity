/**
 * Sprint 4: company self-service and change events. Recorded on the live demo (Project Tracker + demo Identity).
 * Run with DEMO_PASSWORD=<Identity demo password> IDENTITY_URL=<demo Identity>. Leaves the demo data as it found it.
 */
import { click, origin, password } from './common.mjs'
import { recordVideo } from './lib.mjs'

const PT = process.env.TRACKER_URL ?? 'https://aadiprojecttracker.aaditechnology.com'
const ID = origin
const row = (page, name) => page.locator('.row', { hasText: name })

async function aadiSignIn(page, email) {
  await click(page.getByTestId('sso-signin'))
  await page.getByLabel('Email').waitFor()
  await page.getByLabel('Email').click()
  await page.getByLabel('Email').pressSequentially(email, { delay: 40 })
  const field = page.getByLabel('Password')
  await field.fill(password)
  if ((await field.getAttribute('type')) !== 'password') throw new Error('The password field is not masked')
  await click(page.getByRole('button', { name: 'Sign in', exact: true }))
}
async function identityAs(page, email, next) {
  await page.goto(ID + '/logout', { waitUntil: 'domcontentloaded' })
  await page.goto(`${ID}/login?next=${encodeURIComponent(next)}`, { waitUntil: 'domcontentloaded' })
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(password)
  await click(page.getByRole('button', { name: 'Sign in', exact: true }))
}
const peopleReady = (page) => row(page, 'Meera Nair').waitFor()

const scenes = [
  { chapter: 'Chapter 1 - Someone is working', title: 'Meera is signed in',
    narration: 'Sprint four makes Identity and the products stay in step. We start with Meera Nair, who signed in to Project Tracker through Aadi a moment ago and is working on her tasks. Keep her in mind: in a minute her access will be taken away, and we will see how fast Project Tracker reacts.',
    actions: 'Sign in as Meera through Aadi.', expected: 'Her employee dashboard.',
    run: async (page) => { await page.goto(PT + '/', { waitUntil: 'domcontentloaded' }); await page.evaluate(() => sessionStorage.clear()); await page.reload(); await page.getByTestId('sso-signin').waitFor(); await aadiSignIn(page, 'meera.nair@aadi.demo.test'); await page.getByRole('heading', { name: 'Overview Dashboard' }).waitFor(); await page.waitForTimeout(5000) } },
  { chapter: 'Chapter 2 - The company administrator', title: 'People page',
    narration: 'Priya Rao is the administrator of this company. She opens the new People page in Identity. It lists everyone in her company, and shows which Aadi products the company has. Company administrators manage their own people here, without asking the platform team.',
    actions: 'Sign in to Identity as Priya Rao and open People.', expected: 'People list.',
    run: async (page) => { await identityAs(page, 'priya.rao@aadi.demo.test', '/company'); await peopleReady(page); await page.waitForTimeout(6000) } },
  { chapter: 'Chapter 2 - The company administrator', title: 'Invite a person',
    narration: 'She invites a new joiner with a name and an email address. Identity sends an invitation, and the person appears in the list as invited. Notice what does not happen: Project Tracker does not create a user or give any role. It is told that a person was added, and an administrator there decides what they can do.',
    actions: 'Invite a new person.', expected: 'Invited in the list.',
    run: async (page) => { await page.locator('#ie').fill('nikhil.joiner@example.org'); await page.locator('#in').fill('Nikhil Joiner'); await click(page.getByRole('button', { name: 'Send invitation' })); await row(page, 'Nikhil Joiner').waitFor(); await page.waitForTimeout(5000) } },
  { chapter: 'Chapter 3 - Access ends quickly', title: 'Suspend Meera',
    narration: 'Now Meera is leaving the project. Priya chooses Suspend next to her name. The page marks her as suspended straight away. Behind the scenes Identity records the change and sends a signed message to Project Tracker, and it ends any tokens Meera held for this company.',
    actions: 'Suspend Meera Nair.', expected: 'Suspended tag.',
    run: async (page) => { await click(row(page, 'Meera Nair').getByRole('button', { name: 'Suspend' })); await row(page, 'Meera Nair').getByText('Suspended').waitFor(); await page.waitForTimeout(12000) } },
  { chapter: 'Chapter 3 - Access ends quickly', title: 'Project Tracker reacts',
    narration: 'We go back to Project Tracker, where Meera still has her session open. A few seconds ago she could work. Now Project Tracker no longer accepts her: she is sent to the sign in page. Before this sprint she would have stayed signed in for up to an hour. Project Tracker checks her membership on every request, so the suspension takes effect at once.',
    actions: 'Reload Project Tracker as Meera.', expected: 'Sign-in page.',
    run: async (page) => { await page.goto(PT + '/', { waitUntil: 'domcontentloaded' }); await page.getByTestId('sso-signin').waitFor(); await page.waitForTimeout(7000) } },
  { chapter: 'Chapter 4 - Bringing her back', title: 'Reactivate',
    narration: 'The project is extended, so Priya reactivates Meera. Another message goes to Project Tracker, which restores her membership. Her role and history were never deleted, so she is exactly where she was.',
    actions: 'Reactivate Meera, then sign in again.', expected: 'Meera signs in again.',
    run: async (page) => {
      await identityAs(page, 'priya.rao@aadi.demo.test', '/company'); await peopleReady(page)
      await click(row(page, 'Meera Nair').getByRole('button', { name: 'Reactivate' })); await row(page, 'Meera Nair').getByRole('button', { name: 'Suspend' }).waitFor(); await page.waitForTimeout(9000)
      await page.goto(ID + '/logout', { waitUntil: 'domcontentloaded' }); await page.goto(PT + '/', { waitUntil: 'domcontentloaded' }); await page.getByTestId('sso-signin').waitFor()
      await aadiSignIn(page, 'meera.nair@aadi.demo.test'); await page.getByRole('heading', { name: 'Overview Dashboard' }).waitFor(); await page.waitForTimeout(4000) } },
  { chapter: 'Chapter 5 - Safety nets', title: 'If a message is lost',
    narration: 'What if a message gets lost? Identity keeps every event in an outbox and retries with growing delays, and each event carries a unique number so a repeat is harmless. As a final safety net, Project Tracker can pull a full snapshot from Identity once a day and compare. That daily check only ever restricts access. It never restores it, so a suspension made inside Project Tracker is never undone by a nightly job. Next is Sprint five: hardening, backups and the production cut over.',
    actions: 'Remove the invited person and finish.', expected: 'Invited person removed.',
    run: async (page) => {
      await identityAs(page, 'priya.rao@aadi.demo.test', '/company'); await peopleReady(page)
      await click(row(page, 'Nikhil Joiner').getByRole('button', { name: 'Remove' })); await row(page, 'Nikhil Joiner').waitFor({ state: 'detached' }); await page.waitForTimeout(12000) } },
]

await recordVideo({
  name: '05-identity-sprint-4-people-and-sync', scenes,
  check: async (page) => { if ((await page.getByTestId('signout').count()) > 0 && (await page.locator('body').innerText()).includes(password)) throw new Error('The demo password is visible on screen') },
})
