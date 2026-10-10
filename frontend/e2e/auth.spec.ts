import { expect, test } from './test';
import { ADMIN, PASSWORD, signInAsAdmin, signInByForm } from './admin';

/* Signing in through the real form. The specs share a server, so the fresh-instance branch is not
 * tested; a second setup being refused is. */

test('a signed-out visitor is sent to sign in rather than to a screen that will not load', async ({
	page
}) => {
	await page.context().clearCookies();
	await page.goto('/browse');

	// Either screen, depending on whether an account exists yet; never the empty grid.
	await expect(page).toHaveURL(/\/(login|setup)$/);
	await expect(page.getByRole('button', { name: /Sign in|Create account/ })).toBeVisible();
});

test('the sign-in screen is not wrapped in the application', async ({ page }) => {
	// No shell around the form. A combobox, not a searchbox: it owns a listbox of suggestions.
	await page.context().clearCookies();
	await page.goto('/login');

	await expect(page.getByRole('navigation', { name: 'Main' })).toHaveCount(0);
	await expect(page.getByRole('combobox', { name: 'Search' })).toHaveCount(0);
});

test('signing in through the form lands on the grid', async ({ page }) => {
	await signInAsAdmin(page);
	await page.context().clearCookies();

	await page.goto('/login');
	// Pressed again while busy: every spec signs in as this account.
	await signInByForm(page, ADMIN, PASSWORD);
	await expect(page).toHaveURL('/browse');
	await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible();
});

test('a wrong password says so without saying which half was wrong', async ({ page }) => {
	await signInAsAdmin(page);
	await page.context().clearCookies();

	await page.goto('/login');
	await page.getByLabel('Username').fill(ADMIN);
	await page.getByLabel('Password', { exact: true }).fill('Not-The-Passphrase-9');
	await page.getByRole('button', { name: 'Sign in' }).click();

	const alert = page.getByRole('alert');
	await expect(alert).toBeVisible();
	// It must not reveal which usernames exist.
	await expect(alert).not.toContainText(/no such|unknown user|does not exist/i);
	await expect(page).toHaveURL('/login');
});

test('setup cannot be reached once the instance has its admin', async ({ page }) => {
	await signInAsAdmin(page);
	await page.context().clearCookies();

	await page.goto('/setup');

	await expect(page).toHaveURL('/login');
});

test('signing in is what makes the grid reachable', async ({ page }) => {
	await signInAsAdmin(page);

	await page.goto('/browse');

	await expect(page).toHaveURL('/browse');
	await expect(page.getByRole('heading', { name: 'Browse' })).toBeVisible();
});
