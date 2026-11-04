import { expect, test } from '@playwright/test';

import { EVAL_RUNS, WORKER_OUT_OF_MEMORY } from '../src/demo/index.ts';
import { E2E } from './shared.ts';

const LABEL = 'Illustrative sample, not a real incident';
test.use({ storageState: { cookies: [], origins: [] } });

// On the isolated server, whose gateway never answers: what it serves needs none. A count of the
// fake gateway's requests would also count another worker's tests.
test('serves the saved reports without the gateway or a session', async ({ page }) => {
  await page.goto(`${E2E.isolatedUrl}/demo`);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Live incidents');
  await expect(page.getByText(LABEL)).toHaveCount(3);
  await page.getByRole('link', { name: WORKER_OUT_OF_MEMORY.title }).click();
  await expect(page).toHaveURL(`${E2E.isolatedUrl}/demo/investigations/${WORKER_OUT_OF_MEMORY.id}`);
  await expect(page.getByText(LABEL)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Approve plan' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Reject plan' })).toBeDisabled();
  await page.goto(`${E2E.isolatedUrl}/demo?source=sim`);
  await expect(page.getByRole('link')).toContainText([EVAL_RUNS[0]?.title ?? '']);
  await expect(page.getByText(LABEL)).toHaveCount(0);
});

test("numbers the demo's lists too: one page each, so Previous and Next are disabled", async ({
  page,
}) => {
  await page.goto('/demo?source=sim');
  await expect(page.locator('.pager-status')).toHaveText('Page 1 of 1');
  for (const name of ['Previous page', 'Next page']) {
    await expect(page.getByRole('button', { name })).toBeDisabled();
  }
  await page.getByLabel('Results per page').selectOption('10');
  await expect(page).toHaveURL(`${E2E.webUrl}/demo?source=sim&page=1&size=10`);
});

test.describe("the navbar's underlines", () => {
  type Page = import('@playwright/test').Page;
  const nav = (page: Page) => page.getByRole('banner').locator('nav.top-nav');
  /** Whether the mark lies along the link's bottom edge, as wide as the link. */
  const under = async (page: Page, mark: string, name: string) => {
    const link = await nav(page).getByRole('link', { name }).boundingBox();
    const line = await nav(page).locator(mark).boundingBox();
    if (link === null || line === null) return false;
    return (
      Math.abs(line.x - link.x) < 1 &&
      Math.abs(line.width - link.width) < 1 &&
      line.height === 2 &&
      line.y >= link.y + link.height
    );
  };
  const style = (page: Page, mark: string, property: string) =>
    nav(page)
      .locator(mark)
      .evaluate((el, name) => getComputedStyle(el).getPropertyValue(name), property);

  test('keeps a gap between the items, so two underlines never join', async ({ page }) => {
    await page.goto('/demo');
    const [live, runs] = await Promise.all(
      ['Live incidents', 'Eval runs'].map((name) =>
        nav(page).getByRole('link', { name }).boundingBox(),
      ),
    );
    expect((runs?.x ?? 0) - ((live?.x ?? 0) + (live?.width ?? 0))).toBeGreaterThanOrEqual(4);
  });

  test('marks the list shown, and Live incidents on a report', async ({ page }) => {
    await page.goto('/demo?source=sim');
    await expect.poll(() => under(page, '.nav-mark', 'Eval runs')).toBe(true);
    await page.goto(`/demo/investigations/${WORKER_OUT_OF_MEMORY.id}`);
    await expect.poll(() => under(page, '.nav-mark', 'Live incidents')).toBe(true);
  });

  test('slides a hover underline and tint between the items, and hides them after', async ({
    page,
  }) => {
    await page.goto('/demo');
    expect(await style(page, '.nav-line', 'opacity')).toBe('0');
    await nav(page).getByRole('link', { name: 'Live incidents' }).hover();
    await nav(page).getByRole('link', { name: 'Eval runs' }).hover();
    expect(await style(page, '.nav-line', 'transition-duration')).toContain('0.3s');
    await expect.poll(() => under(page, '.nav-line', 'Eval runs')).toBe(true);
    expect(await style(page, '.nav-glow', 'opacity')).toBe('1');
    await page.mouse.move(5, 400);
    await expect.poll(() => style(page, '.nav-line', 'opacity')).toBe('0');
  });

  test('gives keyboard focus the hover underline and a focus ring', async ({ page }) => {
    await page.goto('/demo');
    await nav(page).getByRole('link', { name: 'Live incidents' }).focus();
    await page.keyboard.press('Tab');
    const evalRuns = nav(page).getByRole('link', { name: 'Eval runs' });
    await expect(evalRuns).toBeFocused();
    await expect.poll(() => under(page, '.nav-line', 'Eval runs')).toBe(true);
    expect(await evalRuns.evaluate((el) => getComputedStyle(el).boxShadow)).not.toBe('none');
  });

  test('moves the underlines without sliding under reduced motion', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto('/demo');
    for (const mark of ['.nav-line', '.nav-mark', '.nav-glow']) {
      expect(await style(page, mark, 'transition-duration')).toBe('0s');
    }
  });
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

test.describe('the landing hero', () => {
  const RUN = EVAL_RUNS.find((run) => run.id === '59787641-d31c-47d7-a1de-f06c7af65275');

  test('opens the demo from its one button, and the saved report from its card', async ({
    page,
  }) => {
    await page.goto('/');
    const main = page.getByRole('main');
    await expect(main.locator('.button')).toHaveCount(1);
    await main.getByRole('link', { name: 'Open the demo' }).click();
    await expect(page).toHaveURL(`${E2E.webUrl}/demo`);
    await page.goto('/');
    await main.getByRole('link', { name: RUN?.title ?? '' }).click();
    await expect(page).toHaveURL(`${E2E.webUrl}/demo/investigations/${RUN?.id ?? ''}`);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(RUN?.title ?? '');
  });

  test('asks no other site for anything', async ({ page }) => {
    const asked: string[] = [];
    page.on('request', (request) => asked.push(request.url()));
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    expect(asked.filter((url) => !url.startsWith(E2E.webUrl))).toEqual([]);
  });

  test('sets the card above the horizon, so its whole frame can be clicked', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto('/');
    const [card, horizon] = await Promise.all(
      ['.hero-preview', '.hero-horizon'].map((selector) => page.locator(selector).boundingBox()),
    );
    // The curve's top passes behind the card's lower half.
    expect(horizon?.y ?? 0).toBeLessThan((card?.y ?? 0) + (card?.height ?? 0));
    const covered = await page.evaluate(
      ([x, y]) => {
        const hit = document.elementFromPoint(x ?? 0, y ?? 0);
        return hit?.closest('.hero-preview') !== null;
      },
      [(card?.x ?? 0) + 24, (card?.y ?? 0) + (card?.height ?? 0) - 12],
    );
    expect(covered).toBe(true);
  });

  test('runs the headline and subtitle across a wide screen, and wraps them on a phone', async ({
    page,
  }) => {
    // Lines of text in an element: its height over its line height.
    const lines = (selector: string) =>
      page.locator(selector).evaluate((element) => {
        const { lineHeight, paddingBottom } = getComputedStyle(element);
        const text = element.getBoundingClientRect().height - parseFloat(paddingBottom);
        return Math.round(text / parseFloat(lineHeight));
      });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    for (const [width, height, headline, subtitle] of [
      [1100, 900, 2, 2],
      [1440, 900, 2, 2],
      [2048, 1030, 2, 2],
      [2560, 1300, 2, 2],
      [390, 844, 3, 4],
    ] as const) {
      await page.setViewportSize({ width, height });
      await page.goto('/');
      expect([width, await lines('.hero h1'), await lines('.hero-sub')]).toEqual([
        width,
        headline,
        subtitle,
      ]);
    }
  });

  test('moves lightly on a wide screen, and stays still with reduced motion or on a phone', async ({
    page,
  }) => {
    // The hero's parts and their animations; none means the part never moves.
    const moving = () =>
      page.evaluate(() =>
        ['.hero h1', '.hero-sub', '.hero-cta', '.hero-preview', '.hero-horizon'].map((selector) => {
          const part = document.querySelector(selector);
          return part === null ? 'missing' : getComputedStyle(part).animationName;
        }),
      );
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/');
    expect(await moving()).not.toContain('none');
    expect(await moving()).not.toContain('missing');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto('/');
    expect(new Set(await moving())).toEqual(new Set(['none']));
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');
    expect(new Set(await moving())).toEqual(new Set(['none']));
  });
});
