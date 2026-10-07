import { expect, test } from './test';
import { signInAsAdmin, ADMIN, PASSWORD } from './admin';

/*
 * The account, as the first section of Settings > Personal.
 *
 * It sits in one group with Appearance and Privacy, the other things that are YOURS rather than
 * the library's. These check the rail does not offer it, Settings does, and the section leads its
 * group, above Appearance, where a person looks for it. They do not re-test the auth endpoints, which have their
 * own tests server-side.
 */

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
});

test('the rail does not carry it, and neither does the top bar', async ({ page }) => {
	await page.goto('/browse');
	// The rail hydrates progressively, so wait for a row that IS there before reading the absence of
	// one that is not. Otherwise this passes against a rail that has not drawn yet.
	await expect(page.locator('nav.rail a.item[href="/settings"]')).toBeVisible();

	await expect(page.locator('nav.rail a.item[href="/profile"]')).toHaveCount(0);
	// No account control on the top bar either.
	await expect(page.locator('.account-trigger')).toHaveCount(0);
});

test('Recently viewed is the row directly under Settings', async ({ page }) => {
	await page.goto('/browse');
	await expect(page.locator('nav.rail a.item[href="/settings"]')).toBeVisible();

	const hrefs = await page
		.locator('nav.rail a.item')
		.evaluateAll((links) => links.map((link) => link.getAttribute('href')));
	expect(hrefs.indexOf('/recent')).toBe(hrefs.indexOf('/settings') + 1);
});

test('it is the first section of Personal, above Appearance', async ({ page }) => {
	await page.goto('/settings/profile');

	/* The group's own list, named by its heading: first in it is the claim, and a section added
	   between Profile and Appearance (Get to know Sift is) does not move Profile from the top. */
	const sections = page
		.getByRole('navigation', { name: 'Settings sections' })
		.getByRole('list', { name: 'Personal' })
		.locator('.item');
	/* Wait for a row before reading the list. `evaluateAll` does not wait for anything: handed a
	   list that has not drawn yet it returns an empty array, and every `findIndex` on it answers -1,
	   which reads exactly like the section being missing. */
	await expect(sections.first()).toBeVisible();
	const labels = await sections.evaluateAll((rows) => rows.map((row) => row.textContent?.trim()));
	const profile = labels.findIndex((label) => label?.includes('Profile'));
	const appearance = labels.findIndex((label) => label?.includes('Appearance'));

	expect(profile, 'Profile is not the first section of Personal').toBe(0);
	expect(appearance, 'Appearance is not below Profile in Personal').toBeGreaterThan(profile);
});

test('it names who is signed in and offers a way out', async ({ page }) => {
	await page.goto('/settings/profile');

	await expect(page.getByRole('heading', { name: 'Profile', level: 1 })).toBeVisible();
	await expect(page.getByText(ADMIN, { exact: true })).toBeVisible();
	// What the account is, said as what it may do rather than as a bare role name.
	await expect(page.getByText(/^An admin\./)).toBeVisible();
	await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible();
});

test('a guest gets it too, because a guest has a password and a session', async ({ page }) => {
	// Users is admin-only and filtered out of the same group; this one is not, because every
	// signed-in user has a name to change and a session to end.
	await page.goto('/settings/profile');
	await expect(page.getByRole('heading', { name: 'Profile', level: 1 })).toBeVisible();
});

test('changing the password checks the current one', async ({ page }) => {
	// Not driving a real change here: that would sign the shared admin out of every other spec's
	// session. What is checked is that the form is wired to the endpoint and surfaces its refusal:
	// a wrong current password is answered against the field it belongs to, not as a toast.
	await page.goto('/settings/profile');

	await page.getByLabel('Current password').fill('definitely-not-the-password-9');
	await page.getByLabel('New password', { exact: true }).fill('A-brand-new-One-9');
	await page.getByLabel('New password again').fill('A-brand-new-One-9');
	await page.getByRole('button', { name: 'Save password' }).click();

	await expect(page.getByText("That isn't your current password.")).toBeVisible();
	// Still signed in and still here: a refused change changed nothing.
	await expect(page).toHaveURL(/\/settings\/profile$/);
});

test('and it refuses when the two new passwords disagree, before asking the server', async ({
	page
}) => {
	const calls: string[] = [];
	await page.route('**/api/auth/password', (route) => {
		calls.push(route.request().url());
		return route.fulfill({ status: 204, body: '' });
	});

	await page.goto('/settings/profile');
	await page.getByLabel('Current password').fill(PASSWORD);
	await page.getByLabel('New password', { exact: true }).fill('A-brand-new-One-9');
	await page.getByLabel('New password again').fill('a-different-typo-9');
	await page.getByRole('button', { name: 'Save password' }).click();

	await expect(page.getByText("The two new passwords don't match.")).toBeVisible();
	expect(calls, 'the mismatch was sent to the server instead of caught here').toEqual([]);
});
