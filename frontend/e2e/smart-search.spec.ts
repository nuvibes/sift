import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

/*
 * The two admin panes that ask the server what the library still owes it, in a real browser.
 *
 * `fetchBuildSheet` has exactly two callers and both are in `settings-ui/Importing.svelte`
 * (`onMount(() => void load())`), while Smart Search (`settings-ui/Semantic.svelte`, registered
 * under the id `semantic`) only ever POSTS to start one. So the sheet is covered where it lives,
 * the Smart Search pane is covered as the pane it is, and the one claim they share is the one that
 * matters: both are an admin's, and a caller who is not one is refused by the SERVER rather than
 * merely not being offered the link.
 *
 * Why a browser rather than a unit test: what is checked here is that the pane at that address
 * mounts at all, and that opening it is what makes the request. Both of those are the wiring
 * between the section registry, the route and the component, and none of the three knows about the
 * other two.
 */

/** Nothing but the panes themselves, so a first-run flow cannot sit over the top of them. */
async function openSettings(page: Page): Promise<void> {
	await signInAsAdmin(page);
}

test('Smart Search is a pane of its own, and says the models are not included', async ({
	page
}) => {
	await openSettings(page);
	await page.goto('/settings/semantic');

	await expect(page.getByRole('heading', { name: 'Smart Search' }).first()).toBeVisible();

	/*
	 * The sentence is the feature, in the same way the faces consent gate's is: what somebody reads
	 * before they turn on a thing that reaches the internet and writes several hundred megabytes to
	 * their disk. A pane that drew the switch and not this would pass every unit test in the tree.
	 *
	 * Guarded by the switch being off on a fresh install, which is also why this is the one
	 * assertion here that does not depend on the machine: a box with no support for it says so
	 * instead, and that branch draws neither.
	 *
	 * The switch itself stands with the other recognition switches under `Settings > Importing`;
	 * this pane names it and its press goes there.
	 */
	const unsupported = await page.locator('.unavailable').count();
	if (unsupported === 0) {
		await expect(page.getByRole('link', { name: 'Change in Importing' })).toBeVisible();
		await expect(page.getByRole('link', { name: 'Change in Importing' })).toHaveAttribute(
			'href',
			/\/settings\/importing#semantic\./
		);
	}
});

test('the Importing pane asks what a Build would do, on open, before anything is pressed', async ({
	page
}) => {
	/*
	 * The sheet is a QUESTION and not a start: one row per product with its count and its price,
	 * and nothing queued by asking. So the request belongs on mount, and its absence there is the
	 * failure worth catching: a pane that waited for a press would show an empty sheet and read
	 * as a library with nothing left to do.
	 */
	await openSettings(page);

	const asked: string[] = [];
	await page.route('**/api/importing/build', async (route) => {
		if (route.request().method() !== 'GET') return route.fallback();
		asked.push(route.request().method());
		await route.fulfill({ json: { products: [], folders: [], quiet_hours: null } });
	});

	await page.goto('/settings/importing');
	/* Named by one of its own groups rather than by the section's label: the pane draws the four
	   stages of an import as headed blocks and the label above them belongs to the settings shell,
	   so waiting on the label would say the shell had drawn and nothing about this pane. */
	await expect(page.getByRole('heading', { name: 'Scan' }).first()).toBeVisible();

	await expect.poll(() => asked.length, { timeout: 10_000 }).toBeGreaterThan(0);
});

test.describe('who is turned away', () => {
	test('somebody signed out is refused the build sheet and the Smart Search status', async ({
		request
	}) => {
		/* The requests a stolen guess would make, with no page and nothing to render. The server
		   answering them is the only thing that has ever kept anybody out; what the panes do with
		   the link is a courtesy on top of it. */
		expect((await request.get('/api/importing/build')).status()).toBe(401);
	});

	test('and a real guest is refused them too, which is the case the link cannot cover', async ({
		page,
		browser
	}) => {
		/*
		 * A GUEST rather than a signed-out caller, because they are different refusals and only one
		 * of them is the claim. Signed out is answered by the session check that fronts everything;
		 * a guest is signed in perfectly legitimately and is turned away by the rule that says this
		 * particular route is an admin's. A route that had lost that rule would still answer 401 to
		 * the test above and hand a guest the whole sheet.
		 */
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
