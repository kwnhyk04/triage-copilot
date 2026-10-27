import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { ANSWERS_AFTER_RENAME, EVAL_RUNS, WORKER_OUT_OF_MEMORY } from '../src/demo/index.ts';

const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const PAGES = [
  '/',
  '/login',
  '/demo',
  '/demo?source=sim',
  `/demo/investigations/${WORKER_OUT_OF_MEMORY.id}`,
  `/demo/investigations/${EVAL_RUNS[0]?.id ?? ''}`,
  '/investigations',
  '/investigations?source=sim',
  `/investigations/${WORKER_OUT_OF_MEMORY.id}`,
  `/investigations/${ANSWERS_AFTER_RENAME.id}`,
  '/no-such-page',
];
const SIZES = { desktop: { width: 1280, height: 800 }, phone: { width: 390, height: 844 } };

for (const scheme of ['light', 'dark'] as const) {
  for (const [size, viewport] of Object.entries(SIZES)) {
    test(`every page passes axe (WCAG 2.2 A and AA), ${scheme}, ${size}`, async ({ page }) => {
      test.setTimeout(120_000);
      await page.setViewportSize(viewport);
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
      for (const path of PAGES) {
        await page.goto(path);
        // AxeBuilder is built inline: the react-hooks lint flags a fixture's use().
        const { violations } = await new AxeBuilder({ page }).withTags(TAGS).analyze();
        const found = violations.map(
          (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
        );
        expect.soft([path, found]).toEqual([path, []]);
        if (size === 'phone') {
          const sideways = await page.evaluate(
            () => document.documentElement.scrollWidth > window.innerWidth,
          );
          expect
            .soft([path, 'scrolls sideways', sideways])
            .toEqual([path, 'scrolls sideways', false]);
        }
      }
    });
  }
}
