import { expect, test } from '@playwright/test';

import { EVAL_RUNS, LIVE_SAMPLES, WORKER_OUT_OF_MEMORY } from '../src/demo/index.ts';
import { E2E } from './shared.ts';

const REPORT = `/investigations/${WORKER_OUT_OF_MEMORY.id}`;
const NOTE = 'Reviewed by a second model. Read every step yourself.';
interface GatewayState {
  requests: number;
  reads: number;
  decisions: { plan_id: string; verdict: string; approver: string; reason?: string }[];
}
const leaks: string[] = [];

test.beforeEach(async ({ page, request }) => {
  await request.post(`${E2E.gatewayUrl}/__e2e/reset`);
  leaks.length = 0;
  // The browser talks to the web server only, never to the gateway.
  page.on('request', (sent) => {
    if (sent.url().startsWith(E2E.gatewayUrl)) leaks.push(sent.url());
  });
});
test.afterEach(() => {
  expect(leaks).toEqual([]);
});

const state = async (request: import('@playwright/test').APIRequestContext) =>
  (await (await request.get(`${E2E.gatewayUrl}/__e2e/state`)).json()) as GatewayState;

test('opens on live incidents, which never list an eval run, and the reverse', async ({ page }) => {
  const [run] = EVAL_RUNS;
  await page.goto('/investigations');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Live incidents');
  for (const sample of LIVE_SAMPLES) await expect(page.getByText(sample.title)).toBeVisible();
  await expect(page.getByText(run?.title ?? '')).toHaveCount(0);
  await page.getByRole('link', { name: 'Eval runs' }).click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Eval runs');
  await expect(page.getByText(run?.title ?? '').first()).toBeVisible();
  await expect(page.getByText(WORKER_OUT_OF_MEMORY.title)).toHaveCount(0);
});

test('shows the verdict, cause, evidence, models and fallback notes', async ({ page }) => {
  await page.goto(REPORT);
  for (const text of [
    'Real incident',
    'resource exhaustion in worker',
    'worker    exited (137) 2 minutes ago, restarting',
    'claude-opus-5-5',
    'Triage: gemini-3.5-flash-lite answered; claude-haiku-5-5 missed 1 request',
  ]) {
    await expect(page.getByText(text, { exact: false }).first()).toBeVisible();
  }
});

test('records an approval by the operator', async ({ page, request }) => {
  await page.goto(REPORT);
  await page.getByRole('button', { name: 'Approve plan' }).click();
  await expect(page.getByText(/^Approved at .* by operator/)).toBeVisible();
  const plan = WORKER_OUT_OF_MEMORY.plan.id;
  expect((await state(request)).decisions).toEqual([
    { plan_id: plan, verdict: 'approve', approver: 'operator' },
  ]);
});

test('records a rejection with its reason', async ({ page, request }) => {
  await page.goto(REPORT);
  await page.getByLabel('Reason (optional)').fill('The queue is the problem.');
  await page.getByRole('button', { name: 'Reject plan' }).click();
  await expect(page.getByText(/^Rejected at .*: The queue is the problem\.$/)).toBeVisible();
  expect((await state(request)).decisions).toMatchObject([
    { verdict: 'reject', approver: 'operator', reason: 'The queue is the problem.' },
  ]);
});

test("shows a flagged plan's warnings above its buttons, with the second model's note", async ({
  page,
}) => {
  await page.goto(REPORT);
  const plan = page.getByRole('region', { name: 'Plan', exact: true });
  const warnings = plan.getByRole('heading', { name: 'Read these warnings before deciding' });
  await expect(warnings).toBeVisible();
  await expect(plan.getByText('Fallback step')).toBeVisible();
  await expect(plan.getByText(NOTE)).toBeVisible();
  const approve = plan.getByRole('button', { name: 'Approve plan' });
  await expect(approve).toBeVisible();
  const [above, below] = [await warnings.boundingBox(), await approve.boundingBox()];
  expect((above?.y ?? Infinity) < (below?.y ?? -Infinity)).toBe(true);
});

test('shows why a plan was refused, with no way to approve or reject it', async ({
  page,
  request,
}) => {
  await page.goto(`/investigations/${E2E.refusedId}`);
  const plan = page.getByRole('region', { name: 'Plan', exact: true });
  await expect(plan.getByRole('heading', { name: 'Refused by the review' })).toBeVisible();
  for (const text of [
    'Forbidden step',
    'A change made as a test to redis, whose health check passes',
    NOTE,
    'A refused plan takes no decision.',
  ]) {
    await expect(plan.getByText(text, { exact: false })).toBeVisible();
  }
  await expect(page.getByRole('button', { name: /^(Approve|Reject) plan$/ })).toHaveCount(0);
  expect((await state(request)).decisions).toEqual([]);
  // The list reads it from plan_review: Refused, where its pending status would ask for a person.
  await page.goto('/investigations');
  const row = page.getByRole('row', { name: /QueueBacklog on worker/ });
  await expect(row.getByText('Refused', { exact: true })).toBeVisible();
  await expect(row.getByText('Decision needed')).toHaveCount(0);
});

test('follows a running investigation without a reload, pauses, and stops at the report', async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  await page.goto(`/investigations/${E2E.runningId}`);
  const steps = page.locator('ol.timeline > li');
  await expect(page.getByRole('status')).toContainText('Running, 1 step so far');
  await expect(steps).toHaveCount(2, { timeout: 15_000 });
  await page.getByRole('button', { name: 'Pause updates' }).click();
  const paused = (await state(request)).reads;
  await page.waitForTimeout(7_000);
  expect((await state(request)).reads).toBe(paused);
  await page.getByRole('button', { name: 'Resume updates' }).click();
  await expect(page.getByText('Not a real incident')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('status')).toHaveCount(0);
  const landed = (await state(request)).reads;
  await page.waitForTimeout(7_000);
  expect((await state(request)).reads).toBe(landed);
});

test('shows a real incident still planning, then its plan as soon as it lands', async ({
  page,
}) => {
  test.setTimeout(60_000);
  await page.goto(`/investigations/${E2E.planningId}`);
  const waiting = page.getByText('Still planning a fix.', { exact: false });
  await expect(page.getByRole('status')).toContainText('Report ready, still planning');
  await expect(waiting).toBeVisible();
  await expect(page.getByText('No plan was made')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Approve plan' })).toBeVisible({
    timeout: 20_000,
  });
  await expect(waiting).toHaveCount(0);
  await expect(page.getByRole('status')).toHaveCount(0);
});

test('sends a reader to the demo when the gateway does not answer', async ({ page }) => {
  await page.goto(`${E2E.isolatedUrl}/investigations`);
  await expect(page).toHaveURL(`${E2E.isolatedUrl}/demo?offline=1`);
  await expect(page.getByText('The live system is not answering.')).toBeVisible();
});
