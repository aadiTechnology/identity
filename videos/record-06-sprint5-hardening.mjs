/**
 * Sprint 5: hardening and operations. Recorded on the live demo Identity and the live demo Project Tracker.
 * Run with DEMO_PASSWORD=<Identity demo password> IDENTITY_URL=<demo Identity>. Rotating the demo key is harmless; it is what the runbook describes.
 */
import { click, origin, password } from './common.mjs'
import { recordVideo } from './lib.mjs'

const PT = process.env.TRACKER_URL ?? 'https://aadiprojecttracker.aaditechnology.com'
const ID = origin

async function identityAdmin(page) {
  await page.goto(ID + '/logout', { waitUntil: 'domcontentloaded' })
  await page.goto(`${ID}/login?next=/admin`, { waitUntil: 'domcontentloaded' })
  await page.getByLabel('Email').fill('admin@demo.example.com')
  await page.getByLabel('Password').fill(password)
  await click(page.getByRole('button', { name: 'Sign in', exact: true }))
  await page.getByRole('heading', { name: 'Health' }).waitFor()
  await page.getByText('Database:').waitFor()
}

const scenes = [
  { chapter: 'Chapter 1 - Is it healthy?', title: 'The health panel',
    narration: 'Sprint five is about running Identity safely. The platform administrator page now has a Health panel. It shows that the database answers, how many people, companies and live sign ins there are, how many change messages are waiting to reach products and how many have failed, and how old the signing key is. A failed count above zero is the signal to act. An outside monitor can also watch the ready address, which checks the database and the signing key.',
    actions: 'Open the admin page and its Health panel.', expected: 'Health panel with counts.',
    run: async (page) => { await identityAdmin(page); await page.locator('#health').scrollIntoViewIfNeeded(); await page.waitForTimeout(9000) } },
  { chapter: 'Chapter 2 - Abuse', title: 'Limits on password reset',
    narration: 'Next, the limits. Anyone can open the forgot password page, so it needed protection. A single person can request three reset mails in ten minutes. Further requests look exactly the same on screen but send nothing, so nobody learns which addresses exist, and nobody can flood a mailbox. And one address cannot spray requests across many accounts: after ten, the page asks the visitor to wait. The same kind of limit now protects the one time link page and the token endpoint that products call, where guessing client secrets is slowed down.',
    actions: 'Request reset links repeatedly until the page asks to wait.', expected: 'Too many requests page.',
    run: async (page) => {
      await page.goto(ID + '/logout', { waitUntil: 'domcontentloaded' })
      for (let i = 0; i < 12; i += 1) {
        await page.goto(ID + '/forgot', { waitUntil: 'domcontentloaded' })
        await page.getByLabel('Email').fill(`visitor${i}@example.org`)
        await click(page.getByRole('button', { name: 'Send link' }))
        await page.waitForLoadState('domcontentloaded')
        if (await page.getByRole('heading', { name: 'Too many requests' }).count()) break
      }
      await page.getByRole('heading', { name: 'Too many requests' }).waitFor()
      await page.waitForTimeout(6000) } },
  { chapter: 'Chapter 3 - Keys', title: 'Rotating the signing key',
    narration: 'Now a drill the runbook describes: rotating the key that signs every token. The administrator presses Rotate signing key and confirms. A new key starts signing at once. The old key stays published until the tokens it signed have expired, so nobody is signed out. The panel now shows a fresh key and two published keys.',
    actions: 'Rotate the signing key.', expected: 'New key id, two published.',
    run: async (page) => {
      await identityAdmin(page)
      const before = await page.locator('#health .mono').innerText()
      await click(page.locator('#rotate')); await page.waitForTimeout(1200); await click(page.locator('#rotate'))
      await page.waitForFunction((b) => document.querySelector('#health .mono')?.textContent !== b, before, { timeout: 15000 })
      await page.locator('#health').scrollIntoViewIfNeeded(); await page.waitForTimeout(8000) } },
  { chapter: 'Chapter 3 - Keys', title: 'Project Tracker follows',
    narration: 'The proof is a normal sign in. Meera Nair signs in to Project Tracker through Aadi. Her token is signed by the new key, which Project Tracker has never seen. It notices the unknown key, fetches the updated list from Identity, and lets her in. No restart and no change on the Project Tracker side.',
    actions: 'Sign in to Project Tracker as Meera.', expected: 'Her dashboard.',
    run: async (page) => {
      await page.goto(ID + '/logout', { waitUntil: 'domcontentloaded' })
      await page.goto(PT + '/', { waitUntil: 'domcontentloaded' }); await page.evaluate(() => sessionStorage.clear()); await page.reload()
      await click(page.getByTestId('sso-signin'))
      await page.getByLabel('Email').waitFor(); await page.getByLabel('Email').pressSequentially('meera.nair@aadi.demo.test', { delay: 40 })
      await page.getByLabel('Password').fill(password); await click(page.getByRole('button', { name: 'Sign in', exact: true }))
      await page.getByRole('heading', { name: 'Overview Dashboard' }).waitFor(); await page.waitForTimeout(6000) } },
  { chapter: 'Chapter 4 - What was checked', title: 'Tests, load, backups',
    narration: 'Behind the screens: the forty one Identity tests now pass on the same database the live system uses, PostgreSQL, not only on a test database. The upgrade from the previous version was rehearsed with data in place. A load run with thirty people signing in at once managed ninety eight requests a second with no errors on one small process. A backup was restored into a scratch database and every table matched. A scan of the libraries found known weaknesses in two of them in Identity and two in Project Tracker. All four were upgraded, and Project Tracker passes its tests with the new versions.',
    actions: 'Closing summary over the admin page.', expected: 'Admin page.',
    run: async (page) => { await identityAdmin(page); await page.waitForTimeout(24000) } },
  { chapter: 'Chapter 5 - Before real customers', title: 'What is still open',
    narration: 'Three documents now sit in the repository: a production runbook, a security review, and a guide for putting the next product on Identity. The review is honest about what is not done. Administrators have no second sign in step yet, the limits and the delivery worker assume a single process, and there has been no outside penetration test. Backups work, but the nightly schedule and an off server copy still need your decision. Next is connecting Documents, using the guide.',
    actions: 'Closing summary.', expected: 'Admin page.',
    run: async (page) => { await page.waitForTimeout(22000) } },
]

await recordVideo({
  name: '06-identity-sprint-5-hardening-and-operations', scenes,
  check: async (page) => { if ((await page.getByTestId('signout').count()) > 0 && (await page.locator('body').innerText()).includes(password)) throw new Error('The demo password is visible on screen') },
})
