import { expect, test } from '@playwright/test';
import { ADMIN, PASSWORD, signInAsAdmin } from './admin';

/* Getting in, in a real browser.
 *
 * The parts worth testing here are the ones no unit test can see: that the form really sets a
 * session cookie the rest of the app is then trusted with, that a signed-out person is sent
 * somewhere useful rather than to a screen whose every request is refused, and that the sign-in
 * screen is not wrapped in an application shell the visitor is not inside yet.
 *
 * One thing cannot be tested here and it is worth saying why. The specs share a single server, so
 * by the time any of them runs an admin may already exist. The fresh-instance branch is
 * therefore reached at most once, by whichever file happens to run first, and a test that depended
 * on being that file would pass or fail on scheduling. What is tested instead is the guard that
 * matters on an instance that already has an admin: that setup refuses to run a second time.
 */

test('a signed-out visitor is sent to sign in rather than to a screen that will not load', async ({
	page
}) => {
	await page.context().clearCookies();
	await page.goto('/browse');

	// Either sign-in screen is a correct destination, and which one depends on whether this
	// instance has an account yet: the specs share a server, so that is decided by whichever file
	// ran first. What matters is that they did not stay on a screen with nothing on it: every
	// request that screen makes is refused, so it would render an empty grid and no explanation.
	await expect(page).toHaveURL(/\/(login|setup)$/);
	await expect(page.getByRole('button', { name: /Sign in|Create account/ })).toBeVisible();
});

test('the sign-in screen is not wrapped in the application', async ({ page }) => {
	// A rail and a search box around a login form suggest somebody is already inside, and every one
	// of those controls would answer 401 if they touched it.
	//
	// The box is a combobox rather than a searchbox: it has a listbox of suggestions under it, and
	// ARIA gives that role to the input that owns one. `searchbox` would match nothing here whatever
	// the page contained, which is a test that cannot fail.
	await page.context().clearCookies();
	await page.goto('/login');

	await expect(page.getByRole('navigation', { name: 'Main' })).toHaveCount(0);
	await expect(page.getByRole('combobox', { name: 'Search' })).toHaveCount(0);
});

test('signing in through the form lands on the grid', async ({ page }) => {
	// An admin is created through the API first so this test does not depend on being the file
	// that ran first; the sign-in itself goes through the real form.
	await signInAsAdmin(page);
	await page.context().clearCookies();

	await page.goto('/login');
	await page.getByLabel('Username').fill(ADMIN);
	await page.getByLabel('Password', { exact: true }).fill(PASSWORD);

	// Pressed again while busy: every spec signs in as this account, one sign-in at a time.
	await expect(async () => {
		await page.getByRole('button', { name: 'Sign in' }).click();
		await expect(page).toHaveURL('/browse', { timeout: 4000 });
	}).toPass({ timeout: 30_000 });
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
	// It must not distinguish "no such account" from "wrong password": one of those is a way to
	// find out which usernames exist.
	await expect(alert).not.toContainText(/no such|unknown user|does not exist/i);
	await expect(page).toHaveURL('/login');
});

test('setup cannot be reached once the instance has its admin', async ({ page }) => {
	// Otherwise whoever reached this screen first could claim somebody else's instance. The server
	// refuses a second setup regardless; this is the screen agreeing with it.
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
