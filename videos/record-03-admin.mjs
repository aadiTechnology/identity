/** Admin and subscriptions: a subscription change changes what people see. Leaves the demo data as it found it. */
import { assertNoSecret, click, origin, signIn, signOutEverywhere } from './common.mjs'
import { recordVideo } from './lib.mjs'

const row = (page, name) => page.locator('.row', { hasText: name })

const scenes = [
  { chapter: 'Chapter 1 - Administration', title: 'Platform admin',
    narration: 'This part shows the administration page, which the platform team uses to manage companies and subscriptions. We sign in as the platform administrator. The page lists every company, how many people it has, and one button per product. A highlighted button means the company has an active subscription to that product.',
    actions: 'Sign in as platform administrator and open Admin.', expected: 'Admin page.',
    run: async (page) => { await signOutEverywhere(page); await signIn(page, 'admin@demo.example.com'); await page.goto(origin + '/admin', { waitUntil: 'domcontentloaded' }); await page.getByRole('heading', { name: 'Platform admin' }).waitFor(); await row(page, 'Greenline Foods').waitFor(); await page.waitForTimeout(8000) } },
  { chapter: 'Chapter 2 - A subscription changes the experience', title: 'Add a product',
    narration: 'Greenline Foods has only Project Tracker, so its people go straight into it. We switch on Documents for this company. One click, and the subscription is active. The change is recorded in the activity list at the bottom.',
    actions: 'Turn on Documents for Greenline Foods.', expected: 'Documents highlighted.',
    run: async (page) => { await click(row(page, 'Greenline Foods').getByRole('button', { name: 'Documents' })); await page.getByText('subscription.changed').first().waitFor(); await page.waitForTimeout(6000) } },
  { chapter: 'Chapter 2 - A subscription changes the experience', title: 'Rahul now sees a chooser',
    narration: 'Now we sign in again as Rahul. Before, he went straight into Project Tracker. His company now has two products, so he gets the chooser. Nothing was changed on his account. The company bought another product, and the sign in adapted by itself.',
    actions: 'Sign in as Rahul.', expected: 'Chooser with two products.',
    run: async (page) => { await signOutEverywhere(page); await signIn(page, 'rahul@demo.example.com'); await page.getByRole('heading', { name: /Where to/ }).waitFor(); await page.waitForTimeout(8000) } },
  { chapter: 'Chapter 2 - A subscription changes the experience', title: 'Cancel it again',
    narration: 'We go back to the administrator and switch Documents off again. Rahul is again taken straight into Project Tracker. Cancelling also stops existing tokens from being refreshed, so access ends within the token lifetime of fifteen minutes, not at the end of the session.',
    actions: 'Turn off Documents, sign in as Rahul.', expected: 'Back to a direct landing.',
    run: async (page) => {
      await signOutEverywhere(page); await signIn(page, 'admin@demo.example.com'); await page.goto(origin + '/admin', { waitUntil: 'domcontentloaded' })
      await row(page, 'Greenline Foods').waitFor(); await click(row(page, 'Greenline Foods').getByRole('button', { name: 'Documents' })); await page.waitForTimeout(3000)
      await signOutEverywhere(page); await signIn(page, 'rahul@demo.example.com'); await page.getByRole('heading', { name: 'Welcome to Project Tracker' }).waitFor(); await page.waitForTimeout(7000) } },
  { chapter: 'Chapter 3 - What is next', title: 'Next steps',
    narration: 'Still to come: connecting Project Tracker itself, so that its own sign in page is replaced by this one, with a switch back for safety. Then Documents, then the other products, one at a time. The import tool and deployment files are already in place. That completes the first three sprints of the Identity platform.',
    actions: 'Closing summary.', expected: 'Sign-in page.',
    run: async (page) => { await signOutEverywhere(page); await page.goto(origin + '/login', { waitUntil: 'domcontentloaded' }); await page.waitForTimeout(10000) } },
]

await recordVideo({ name: '03-identity-admin-and-subscriptions', scenes, check: assertNoSecret })
