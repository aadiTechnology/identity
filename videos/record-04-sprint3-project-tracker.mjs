/**
 * Sprint 3: Project Tracker signs in through Aadi Identity. Recorded on the live demo: Project Tracker (AUTH_MODE=both) and the demo Identity.
 * Run with DEMO_PASSWORD=<Identity demo password>. TRACKER_URL overrides the Project Tracker address (default: the live demo).
 */
import { click, password } from './common.mjs'
import { recordVideo } from './lib.mjs'

const PT = process.env.TRACKER_URL ?? 'https://aadiprojecttracker.aaditechnology.com'

const aadiSignIn = async (page, email) => {
  await click(page.getByTestId('sso-signin'))
  await page.getByLabel('Email').waitFor()
  await page.getByLabel('Email').click()
  await page.getByLabel('Email').pressSequentially(email, { delay: 40 })
  const field = page.getByLabel('Password')
  await field.fill(password)
  if ((await field.getAttribute('type')) !== 'password') throw new Error('The password field is not masked')
  await click(page.getByRole('button', { name: 'Sign in', exact: true }))
}
const signOut = async (page) => { await click(page.getByTestId('signout')); await page.getByTestId('sso-signin').waitFor() }
const dashboard = (page) => page.getByRole('heading', { name: 'Overview Dashboard' }).waitFor()

const scenes = [
  { chapter: 'Chapter 1 - The new option', title: 'Sign-in with Aadi',
    narration: 'Sprint three connects Project Tracker to Aadi Identity. This is Project Tracker with the new setting switched to both. The sign in page now offers Continue with Aadi sign in above the familiar form. Nothing was taken away: the old password form is still here, so the change can be rolled out company by company and switched back at any time.',
    actions: 'Open the Project Tracker sign-in page.', expected: 'Aadi button above the old form.',
    run: async (page) => { await page.goto(PT + '/', { waitUntil: 'domcontentloaded' }); await page.getByTestId('sso-signin').waitFor(); await page.waitForTimeout(2500) } },
  { chapter: 'Chapter 2 - Signing in', title: 'One sign-in, back in the product',
    narration: 'We choose Continue with Aadi sign in. The browser goes to Identity, where Priya Rao enters her email and password once. Her Aadi account uses the same email as her Project Tracker user, which is how the two are matched. Identity checks her, and sends her back to Project Tracker with the company already chosen.',
    actions: 'Sign in with Aadi as Priya Rao.', expected: 'Lands on the Overview Dashboard.',
    run: async (page) => { await aadiSignIn(page, 'priya.rao@aadi.demo.test'); await dashboard(page); await page.waitForTimeout(6000) } },
  { chapter: 'Chapter 2 - Signing in', title: 'Everything is still there',
    narration: 'This is the same Project Tracker as before. Priya is the C E O, so she sees projects, budgets, contract values and margins across the whole organization. Identity only said who she is and which company she is in. Her role and what she may see still come from Project Tracker itself. The header has a new Switch product link for people who have more than one Aadi product.',
    actions: 'Open Projects.', expected: 'Projects with money columns.',
    run: async (page) => { await click(page.getByRole('navigation').getByRole('link', { name: 'Projects', exact: true })); await page.waitForLoadState('networkidle'); await page.waitForTimeout(7000) } },
  { chapter: 'Chapter 3 - Roles stay in the product', title: 'An employee signs in the same way',
    narration: 'Now Meera Nair, a software engineer. She signs out, which also ends her Identity session, so Identity asks for her password again. Same button, same flow. But Project Tracker gives her the employee view: no project list, no budgets, no money. The sign in is shared, the permissions are not.',
    actions: 'Sign out, sign in as Meera Nair.', expected: 'Employee view.',
    run: async (page) => { await signOut(page); await aadiSignIn(page, 'meera.nair@aadi.demo.test'); await page.getByRole('navigation').first().waitFor(); await page.waitForTimeout(8000) } },
  { chapter: 'Chapter 4 - Safety', title: 'No account, no access',
    narration: 'Vera Visitor has an Aadi account and belongs to the company, but nobody added her to Project Tracker. She signs in successfully at Identity, and Project Tracker politely refuses: it has no matching user for her. A person is never given a role automatically. An administrator has to add her first.',
    actions: 'Sign in as Vera Visitor.', expected: 'Refusal message.',
    run: async (page) => { await signOut(page); await aadiSignIn(page, 'visitor@aadi.demo.test'); await page.getByRole('alert').waitFor(); await page.waitForTimeout(8000) } },
  { chapter: 'Chapter 5 - Rollout', title: 'Switches and what is next',
    narration: 'The setting has three values. Local is the default and changes nothing. Both shows the Aadi button next to the old form, for a pilot. Platform allows Aadi sign in only and switches the password endpoints off. Sessions that start from Aadi last sixty minutes instead of eight hours, so a removed person loses access sooner. Next is Sprint four: events from Identity, so that suspending someone takes effect in Project Tracker immediately.',
    actions: 'Closing summary.', expected: 'Sign-in page.',
    run: async (page) => { await page.waitForTimeout(16000) } },
]

await recordVideo({
  name: '04-identity-sprint-3-project-tracker-sign-in', scenes,
  // the demo password may be listed on Project Tracker's own demo sign-in page; it must never show once someone is signed in
  check: async (page) => { if ((await page.getByTestId('signout').count()) > 0 && (await page.locator('body').innerText()).includes(password)) throw new Error('The demo password is visible on screen') },
})
