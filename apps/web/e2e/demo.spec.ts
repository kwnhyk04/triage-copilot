import { expect, test } from '@playwright/test';

import { EVAL_RUNS, WORKER_OUT_OF_MEMORY } from '../src/demo/index.ts';
import { E2E } from './shared.ts';

const LABEL = 'Illustrative sample, not a real incident';
test.use({ storageState: { cookies: [], origins: [] } });

test('serves the saved reports without the gateway or a session', async ({ page, request }) => {
  const requests = async () =>
    ((await (await request.get(`${E2E.gatewayUrl}/__e2e/state`)).json()) as { requests: number })
      .requests;
  const before = await requests();
  await page.goto('/demo');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Live incidents');
  await expect(page.getByText(LABEL)).toHaveCount(3);
  await page.getByRole('link', { name: WORKER_OUT_OF_MEMORY.title }).click();
  await expect(page).toHaveURL(`${E2E.webUrl}/demo/investigations/${WORKER_OUT_OF_MEMORY.id}`);
  await expect(page.getByText(LABEL)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Approve plan' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Reject plan' })).toBeDisabled();
  await page.goto('/demo?source=sim');
  await expect(page.getByRole('link')).toContainText([EVAL_RUNS[0]?.title ?? '']);
  await expect(page.getByText(LABEL)).toHaveCount(0);
  expect(await requests()).toBe(before);
});

test('keeps a chosen theme in its cookie, renders it on the server, and System forgets it', async ({
  page,
}) => {
  const html = page.locator('html');
  const cookie = async () =>
    (await page.context().cookies()).find((c) => c.name === 'tc-theme')?.value;
  await page.goto('/demo');
  await expect(html).not.toHaveAttribute('data-theme');
  await page.getByRole('radio', { name: 'Dark' }).check();
  await expect(html).toHaveAttribute('data-theme', 'dark');
  expect(await cookie()).toBe('dark');
  await page.reload();
  await expect(html).toHaveAttribute('data-theme', 'dark');
  await expect(page.getByRole('radio', { name: 'Dark' })).toBeChecked();
  await page.getByRole('radio', { name: 'System' }).check();
  await expect(html).not.toHaveAttribute('data-theme');
  expect(await cookie()).toBeUndefined();
});

test('folds the links into a menu on a phone, and steps the theme with one button', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/demo');
  const evalRuns = page.getByRole('link', { name: 'Eval runs' });
  await expect(evalRuns).toBeHidden();
  await page.getByLabel('Menu').click();
  await evalRuns.click();
  await expect(page).toHaveURL(`${E2E.webUrl}/demo?source=sim`);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Eval runs');
  await expect(evalRuns).toBeHidden();
  await page.getByRole('button', { name: 'Theme: System. Switch to Light' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});
