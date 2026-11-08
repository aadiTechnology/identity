/** Sprint 2: the redirect rule (none, one, many), chooser, switcher. Recorded on the demo instance. */
import { assertNoSecret, click, signIn, signOutEverywhere } from './common.mjs'
import { recordVideo } from './lib.mjs'

const welcome = (page, product) => page.getByRole('heading', { name: `Welcome to ${product}` }).waitFor()

const scenes = [
  { chapter: 'Chapter 1 - One product', title: 'Straight into the product',
    narration: 'Sprint two is the sign in experience. The rule is simple. If a person has exactly one product, they never see a chooser. We sign in as Rahul Deshmukh, whose company, Greenline Foods, subscribes only to Project Tracker. The moment he signs in he is inside Project Tracker. This page stands in for the real product. It shows what the product received: the person, the company, and a signed token it can verify.',
    actions: 'Sign in as Rahul Deshmukh.', expected: 'Lands in Project Tracker.',
    run: async (page) => { await signOutEverywhere(page); await signIn(page, 'rahul@demo.example.com'); await welcome(page, 'Project Tracker'); await page.waitForTimeout(9000) } },
  { chapter: 'Chapter 1 - One product', title: 'Another single-product company',
    narration: 'Meera Iyer works at Frostway Dairy, which bought only Documents. She goes straight into Documents. The same sign in serves both people, and each one sees only what their company has bought.',
    actions: 'Sign in as Meera Iyer.', expected: 'Lands in Documents.',
    run: async (page) => { await signOutEverywhere(page); await signIn(page, 'meera@demo.example.com'); await welcome(page, 'Documents'); await page.waitForTimeout(7000) } },
  { chapter: 'Chapter 2 - Several products', title: 'The chooser',
    narration: 'Asha Rao belongs to two companies. Greenline Foods has Project Tracker. Sunrise Logistics has CRM, Documents and Project Tracker. Because she has more than one choice, she sees the chooser, with her products grouped by company. This is the only time the chooser appears.',
    actions: 'Sign in as Asha Rao.', expected: 'Product chooser.',
    run: async (page) => { await signOutEverywhere(page); await signIn(page, 'asha@demo.example.com'); await page.getByRole('heading', { name: /Where to/ }).waitFor(); await page.waitForTimeout(8000) } },
  { chapter: 'Chapter 2 - Several products', title: 'Open a product',
    narration: 'She chooses CRM at Sunrise Logistics. Identity sends her to the product with the company already selected, so the product knows which company she means.',
    actions: 'Choose CRM at Sunrise Logistics.', expected: 'Lands in CRM.',
    run: async (page) => { await click(page.getByRole('link', { name: /CRM/ })); await welcome(page, 'CRM'); await page.waitForTimeout(7000) } },
  { chapter: 'Chapter 2 - Several products', title: 'Switch without signing in again',
    narration: 'Every product has a Switch menu in its header, fed by Identity. Asha opens it and picks Project Tracker at Greenline Foods. There is no second password prompt, because she is already signed in. The product receives a fresh token for the other company.',
    actions: 'Use the Switch menu.', expected: 'Lands in Project Tracker at Greenline Foods.',
    run: async (page) => { await click(page.locator('summary')); await page.waitForTimeout(2500); await click(page.getByRole('link', { name: 'Project Tracker · Greenline Foods' })); await welcome(page, 'Project Tracker'); await page.waitForTimeout(8000) } },
  { chapter: 'Chapter 3 - No product', title: 'No active subscription',
    narration: 'Neha Kapoor is signed up, but her company has no active subscription. She is not sent anywhere. She sees a plain explanation and is told to ask her company administrator. The same applies when a subscription expires or is cancelled.',
    actions: 'Sign in as Neha Kapoor.', expected: 'No-subscription page.',
    run: async (page) => { await signOutEverywhere(page); await signIn(page, 'nobody@demo.example.com'); await page.getByRole('heading', { name: 'No active subscription' }).waitFor(); await page.waitForTimeout(8000) } },
  { chapter: 'Chapter 4 - Sign out', title: 'Sign out everywhere',
    narration: 'Signing out from a product ends the Identity session and returns to a registered address only, so nobody can use the sign out link to send a person to a lookalike site. Behind the scenes the standard sign in uses a one time code that is tied to a secret only the product holds. A copied code is worthless, and reusing one cancels everything it created.',
    actions: 'Sign in as Rahul and sign out of all products.', expected: 'Signed out page.',
    run: async (page) => { await signOutEverywhere(page); await signIn(page, 'rahul@demo.example.com'); await welcome(page, 'Project Tracker'); await page.waitForTimeout(2500); await click(page.getByRole('button', { name: 'Sign out of all products' })); await page.getByRole('heading', { name: 'Signed out' }).waitFor(); await page.waitForTimeout(6000) } },
]

await recordVideo({ name: '02-identity-sprint-2-sign-in-experience', scenes, check: assertNoSecret })
