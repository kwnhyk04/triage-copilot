import { expect, type Page, test } from '@playwright/test';

import { WORKER_OUT_OF_MEMORY } from '../src/demo/index.ts';
import { E2E } from './shared.ts';

const REPORT = `/investigations/${WORKER_OUT_OF_MEMORY.id}`;
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
});

test('says a wrong password is wrong, then returns to the page asked for', async ({ page }) => {
  await page.goto(REPORT);
  await signIn(page, 'not the password');
  await expect(message(page)).toHaveText('Wrong password.');
  await signIn(page);
  await expect(page).toHaveURL(`${E2E.webUrl}${REPORT}`);
  const [cookie] = (await page.context().cookies()).filter(
    (c) => c.name === '__Host-triage_session',
  );
  expect(cookie).toMatchObject({ httpOnly: true, secure: true, sameSite: 'Lax', path: '/' });
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
    [303, '"cache"', '/login'],
  );
  expect((await page.context().cookies()).map((c) => c.name)).not.toContain(
    '__Host-triage_session',
  );
  await page.goto('/investigations');
  await expect(page).toHaveURL(/\/login\?next=/);
});

test('sends a POST without a session to sign-in before any action runs', async ({ request }) => {
  const answer = await request.post(REPORT, { form: { verdict: 'approve' }, maxRedirects: 0 });
  expect(answer.status()).toBe(303);
  expect(answer.headers().location).toContain(`/login?next=${encodeURIComponent(REPORT)}`);
});
