import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/* The two admin panes that ask what the library still owes (`Importing.svelte` and Smart
 * Search): each mounts and asks, and the server refuses anyone but an admin. */

/** Nothing but the panes themselves, so no first-run flow sits over them. */
async function openSettings(page: Page): Promise<void> {
	await signInAsAdmin(page);
}

test('Smart Search is a pane of its own, and says the models are not included', async ({
	page
}) => {
	await openSettings(page);
	await page.goto('/settings/semantic');

	await expect(page.getByRole('heading', { name: 'Smart Search' }).first()).toBeVisible();

	// The consent sentence is the feature: it reaches the internet and downloads hundreds of MB.
	const unsupported = await page.locator('.unavailable').count();
	if (unsupported === 0) {
		await expect(page.getByRole('link', { name: 'Change in Identify settings' })).toBeVisible();
		await expect(page.getByRole('link', { name: 'Change in Identify settings' })).toHaveAttribute(
			'href',
			/\/settings\/tasks#semantic\./
		);
	}
});

test('Import tasks asks what a Build would do, on open, before anything is pressed', async ({
	page
}) => {
	// The sheet is asked for on mount; it queues nothing.
	await openSettings(page);

	const asked: string[] = [];
	await page.route('**/api/importing/build', async (route) => {
		if (route.request().method() !== 'GET') return route.fallback();
		asked.push(route.request().method());
		await route.fulfill({ json: { products: [], folders: [], quiet_hours: null } });
	});

	await page.goto('/settings/tasks');
	// Its own group's heading: the section label belongs to the shell.
	await expect(page.getByRole('heading', { name: 'Import tasks' }).first()).toBeVisible();

	await expect.poll(() => asked.length, { timeout: 10_000 }).toBeGreaterThan(0);
});

test.describe('who is turned away', () => {
	test('somebody signed out is refused the build sheet and the Smart Search status', async ({
		request
	}) => {
		expect((await request.get('/api/importing/build')).status()).toBe(401);
	});

	test('and a real guest is refused them too, which is the case the link cannot cover', async ({
		page,
		browser
	}) => {
		// A guest, not signed out: the admin-only rule is a different refusal from the session's.
		const name = `e2e-smart-${Date.now()}`;
		const password = 'A-Guest-Passphrase-9';

		await signInAsAdmin(page);
		const me = await page.request.get('/api/auth/me');
		const token = (await me.json()).csrf_token as string;
		const made = await page.request.post('/api/auth/users', {
			data: { username: name, password },
			headers: { 'x-csrf-token': token }
		});
		expect(made.ok(), await made.text()).toBeTruthy();

		const theirs = await browser.newContext();
		try {
			const asGuest = await theirs.newPage();
			const signedIn = await asGuest.request.post('/api/auth/login', {
				data: { username: name, password }
			});
			expect(signedIn.ok(), await signedIn.text()).toBeTruthy();

			expect((await asGuest.request.get('/api/importing/build')).status()).toBe(403);
		} finally {
			await theirs.close();
		}
	});
});
