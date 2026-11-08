/** Sprint 1: Identity core, sign-in security, standard endpoints. Recorded on the demo instance. */
import { assertNoSecret, click, origin, signInSlow, signOutEverywhere } from './common.mjs'
import { recordVideo } from './lib.mjs'

const bad = async (page, email) => {
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill('not-the-password')
  await click(page.getByRole('button', { name: 'Sign in', exact: true }))
  await page.getByRole('alert').waitFor()
  await page.waitForTimeout(2500)
}

const scenes = [
  { chapter: 'Chapter 1 - Sign in', title: 'One sign-in page',
    narration: 'This is Sprint one of Aadi Identity, the common sign in for all Aadi products. It is a separate service on its own address, so every product can use the same login. There is nothing product specific here. A person enters an email and a password, and Identity decides which companies and products they can open.',
    actions: 'Open the Identity sign-in page.', expected: 'Sign-in form.',
    run: async (page) => { await signOutEverywhere(page); await page.goto(origin + '/login', { waitUntil: 'domcontentloaded' }); await page.waitForTimeout(3000) } },
  { chapter: 'Chapter 1 - Sign in', title: 'Wrong password',
    narration: 'First, a wrong password. The page says only that the email or password is incorrect. A person who types an email that does not exist gets exactly the same message and takes the same time, so nobody can use this page to find out who has an account. After five wrong attempts the account is locked for fifteen minutes, and the lock does not change the message either.',
    actions: 'Enter a wrong password, then an unknown email.', expected: 'Same error both times.',
    run: async (page) => { await bad(page, 'asha@demo.example.com'); await bad(page, 'nobody-here@example.com'); await page.waitForTimeout(1500) } },
  { chapter: 'Chapter 1 - Sign in', title: 'Forgot password',
    narration: 'Forgot password works the same way. Whatever email is entered, the page says that if the address has an account, a link is on its way. The link works once and expires after one hour. Using it also signs the person out of every product, so a stolen session cannot survive a reset.',
    actions: 'Open Forgot password and submit an email.', expected: 'Neutral confirmation.',
    run: async (page) => { await click(page.getByRole('link', { name: 'Forgot password?' })); await page.getByLabel('Email').fill('anyone@example.com'); await click(page.getByRole('button', { name: 'Send link' })); await page.getByText('If that email has an account').waitFor(); await page.waitForTimeout(4000) } },
  { chapter: 'Chapter 2 - Signed in', title: 'Sign in and the product hub',
    narration: 'Now a real sign in as Asha Rao. She belongs to two companies, so after signing in she lands on the product hub. It lists every product she may open, grouped by company. The next sprint covers this screen in detail. Her password is typed into a masked field and is never shown.',
    actions: 'Sign in as Asha Rao.', expected: 'Product hub.',
    run: async (page) => { await signInSlow(page, 'asha@demo.example.com'); await page.getByRole('heading', { name: /Where to/ }).waitFor(); await page.waitForTimeout(6000) } },
  { chapter: 'Chapter 3 - Standards', title: 'OpenID Connect discovery',
    narration: 'Products do not need custom code to talk to Identity. It follows the OpenID Connect standard. This discovery document lists the sign in, token and user info addresses, and says that the proof key for code exchange is required and that tokens are signed with R S two five six. Any product that speaks the standard can read this page and configure itself.',
    actions: 'Open the OpenID discovery document.', expected: 'JSON document.',
    run: async (page) => { await page.goto(origin + '/.well-known/openid-configuration', { waitUntil: 'domcontentloaded' }); await page.waitForTimeout(9000) } },
  { chapter: 'Chapter 3 - Standards', title: 'Signing keys',
    narration: 'Tokens are signed with a private key that never leaves Identity. This page publishes only the public half. Products download it and check every token themselves, without calling Identity on each request. Keys can be rotated, and the old key stays listed until its tokens have expired.',
    actions: 'Open the JWKS public keys.', expected: 'Public key set.',
    run: async (page) => { await page.goto(origin + '/.well-known/jwks.json', { waitUntil: 'domcontentloaded' }); await page.waitForTimeout(8000) } },
  { chapter: 'Chapter 4 - What is next', title: 'Summary',
    narration: 'To summarize Sprint one: a standalone sign in service with safe passwords, lockout, protection against forged forms, one time reset links, and the standard endpoints products will use. Existing Project Tracker passwords carry over through an import tool and are upgraded on first sign in. Sprint two shows how Identity decides where each person goes.',
    actions: 'Closing summary.', expected: 'Sign-in page.',
    run: async (page) => { await signOutEverywhere(page); await page.waitForTimeout(9000) } },
]

await recordVideo({ name: '01-identity-sprint-1-core-and-security', scenes, check: assertNoSecret })
