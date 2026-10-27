import { expect, test as setup } from '@playwright/test';

import { E2E } from './shared.ts';

// Signs in once and saves the cookie for the signed-in tests.
setup('sign in', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('Password').fill(E2E.password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).toHaveURL(`${E2E.webUrl}/investigations`);
  await page.context().storageState({ path: E2E.session });
});
