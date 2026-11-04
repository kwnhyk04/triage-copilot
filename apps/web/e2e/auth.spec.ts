import { expect, type Page, test } from '@playwright/test';

import { WORKER_OUT_OF_MEMORY } from '../src/demo/index.ts';
import { SESSION_COOKIE, signSession } from '../src/lib/session.ts';
import { E2E } from './shared.ts';

const REPORT = `/investigations/${WORKER_OUT_OF_MEMORY.id}`;
const HEADLINE = 'An AI on-call that investigates first, and lets a human decide';
test.use({ storageState: { cookies: [], origins: [] } });
// The form's own message; Next's route announcer is an alert too.
const message = (page: Page) => page.locator('#sign-in-error');

// Waits for each attempt's answer: React resets the form after an action, so a password typed
// while the last attempt is pending would be cleared, and `required` would stop that submit.
const signIn = async (page: Page, password = E2E.password) => {
  await page.getByLabel('Password').fill(password);
  await Promise.all([
    page.waitForResponse(
      (answer) =>
        answer.request().method() === 'POST' && new URL(answer.url()).pathname === '/login',
    ),
    page.getByRole('button', { name: 'Sign in' }).click(),
  ]);
};

test('sends every protected page to sign-in, and keeps the landing, demo and probes open', async ({
  page,
  request,
}) => {
  for (const path of ['/investigations', '/investigations?source=sim', REPORT, '/nowhere']) {
    await page.goto(path);
    const url = new URL(page.url());
    expect([path, url.pathname, url.searchParams.get('next')]).toEqual([path, '/login', path]);
  }
  for (const path of ['/', '/demo', `/demo/investigations/${WORKER_OUT_OF_MEMORY.id}`]) {
    expect([path, (await page.goto(path))?.url()]).toEqual([path, `${E2E.webUrl}${path}`]);
  }
  expect((await request.get('/healthz')).status()).toBe(200);
  expect((await request.get('/readyz')).status()).toBe(200);
  // The tab and home-screen icons load before anyone signs in.
  for (const [path, type] of [
    ['/icon.svg', 'image/svg+xml'],
    ['/apple-icon.png', 'image/png'],
  ] as const) {
    const answer = await request.get(path, { maxRedirects: 0 });
    expect([path, answer.status(), answer.headers()['content-type']]).toEqual([path, 200, type]);
  }
});

test('says a wrong password is wrong, returns to the page asked for, and signs out from the header', async ({
  page,
}) => {
  await page.goto(REPORT);
  await signIn(page, 'not the password');
  await expect(message(page)).toHaveText('Wrong password.');
  await signIn(page);
  await expect(page).toHaveURL(`${E2E.webUrl}${REPORT}`);
  const sessions = async () =>
    (await page.context().cookies()).filter((c) => c.name === '__Host-triage_session');
  const [cookie] = await sessions();
  expect(cookie).toMatchObject({ httpOnly: true, secure: true, sameSite: 'Lax', path: '/' });
  // Sign out takes no extra sign-in attempt, so the limiter's count stays as it was.
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page).toHaveURL(`${E2E.webUrl}/?signed-out=1`);
  await expect(page.getByRole('status')).toHaveText("You're signed out.");
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(HEADLINE);
  expect(await sessions()).toEqual([]);
});

test('shows the signed-out note only after signing out', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText("You're signed out.")).toHaveCount(0);
});

test('links the sign-in page back to the landing', async ({ page }) => {
  await page.goto('/login');
  await page.getByRole('main').getByRole('link', { name: 'Back to home' }).click();
  await expect(page).toHaveURL(`${E2E.webUrl}/`);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(HEADLINE);
});

test('signs in from a next that is not a page to Live incidents', async ({ page }) => {
  for (const next of ['/logout', '/api/investigations', '/healthz', '/nowhere']) {
    await page.context().clearCookies();
    await page.goto(`/login?next=${encodeURIComponent(next)}`);
    await signIn(page);
    await expect(page).toHaveURL(`${E2E.webUrl}/investigations`);
  }
});

test('stops the 6th attempt within 15 minutes', async ({ page }) => {
  await page.goto(`${E2E.isolatedUrl}/login`);
  for (let i = 0; i < 5; i++) {
    await signIn(page, `wrong ${String(i)}`);
    await expect(message(page)).toHaveText('Wrong password.');
  }
  await signIn(page);
  await expect(message(page)).toHaveText('Too many attempts. Try again in 15 minutes.');
});

test('signs out with 303, Clear-Site-Data and no cookie left', async ({ page }) => {
  await page.goto('/login');
  await signIn(page);
  await expect(page).toHaveURL(`${E2E.webUrl}/investigations`);
  const answer = await page.request.post('/logout', {
    headers: { origin: E2E.webUrl },
    maxRedirects: 0,
  });
  expect([answer.status(), answer.headers()['clear-site-data'], answer.headers().location]).toEqual(
    [303, '"cache"', '/?signed-out=1'],
  );
  expect((await page.context().cookies()).map((c) => c.name)).not.toContain(
    '__Host-triage_session',
  );
  await page.goto('/investigations');
  await expect(page).toHaveURL(/\/login\?next=/);
});

// The servers' session secret, which playwright.config.ts hands its workers, so a test can make a
// session the server signed that has expired.
const SECRET = process.env.E2E_SESSION_SECRET ?? '';
const expired = () => signSession(SECRET, Date.now() - 9 * 60 * 60 * 1000);
// Host-only, as the server sets it: a domain without a leading dot, as a saved session restores.
const session = (value: string) => ({
  name: SESSION_COOKIE,
  value,
  domain: '127.0.0.1',
  path: '/',
  httpOnly: true,
  secure: true,
  sameSite: 'Lax' as const,
});
// The site's header, the page's banner landmark; a closed phone menu hides its copies.
const bar = (page: Page) => page.getByRole('banner');

test.describe('signed in', () => {
  test.use({ storageState: E2E.session });

  test('goes from the logo to Live incidents, never to the sign-in form', async ({ page }) => {
    await page.goto(REPORT);
    await expect(bar(page).getByRole('button', { name: 'Sign out' })).toBeVisible();
    await bar(page).getByRole('link', { name: 'Triage Copilot' }).click();
    await expect(page).toHaveURL(`${E2E.webUrl}/investigations`);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Live incidents');
    await expect(page.getByLabel('Password')).toHaveCount(0);
  });

  test('keeps the landing, its button on Live incidents, and skips sign-in', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveURL(`${E2E.webUrl}/`);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(HEADLINE);
    await page.getByRole('main').getByRole('link', { name: 'Open Live incidents' }).click();
    await expect(page).toHaveURL(`${E2E.webUrl}/investigations`);
    for (const [path, landed] of [
      ['/login', '/investigations'],
      [`/login?next=${encodeURIComponent(REPORT)}`, REPORT],
      ['/login?next=//evil.example/x', '/investigations'],
      ['/login?next=/logout', '/investigations'],
      ['/login?next=/api/investigations', '/investigations'],
      ['/login?next=/readyz', '/investigations'],
    ] as const) {
      await page.goto(path);
      const url = new URL(page.url());
      expect([path, url.pathname + url.search]).toEqual([path, landed]);
      await expect(page.getByLabel('Password')).toHaveCount(0);
    }
  });
});

test('shows the sign-in form, and Sign in in the header, only when signed out', async ({
  page,
}) => {
  await page.goto('/login');
  await expect(page.getByLabel('Password')).toBeVisible();
  await expect(bar(page).getByRole('link', { name: 'Sign in' })).toBeVisible();
  await expect(bar(page).getByRole('button', { name: 'Sign out' })).toHaveCount(0);
  await bar(page).getByRole('link', { name: 'Triage Copilot' }).click();
  await expect(page).toHaveURL(`${E2E.webUrl}/`);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(HEADLINE);
});

test('treats an expired session as signed out: Sign in in the header, and the form', async ({
  page,
  context,
}) => {
  await context.addCookies([session(expired())]);
  for (const path of ['/', '/demo', '/login']) {
    await page.goto(path);
    expect([path, new URL(page.url()).pathname]).toEqual([path, path]);
    await expect(bar(page).getByRole('link', { name: 'Sign in' })).toBeVisible();
    await expect(bar(page).getByRole('button', { name: 'Sign out' })).toHaveCount(0);
  }
  await expect(page.getByLabel('Password')).toBeVisible();
});

test('says Sign in on the next page once the session has expired, without a reload', async ({
  page,
  context,
}) => {
  await context.addCookies([session(signSession(SECRET))]);
  await page.goto('/demo');
  await expect(bar(page).getByRole('button', { name: 'Sign out' })).toBeVisible();
  await context.addCookies([session(expired())]);
  await bar(page).getByRole('link', { name: 'Eval runs' }).click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Eval runs');
  await expect(bar(page).getByRole('link', { name: 'Sign in' })).toBeVisible();
  await expect(bar(page).getByRole('button', { name: 'Sign out' })).toHaveCount(0);
});

test('sends a POST without a session to sign-in before any action runs', async ({ request }) => {
  const answer = await request.post(REPORT, { form: { verdict: 'approve' }, maxRedirects: 0 });
  expect(answer.status()).toBe(303);
  expect(answer.headers().location).toContain(`/login?next=${encodeURIComponent(REPORT)}`);
});
