import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

/*
 * What a guest is shown, checked as a rule rather than screen by screen.
 *
 * The rule is one sentence: **do not offer a guest a control the server will refuse them.** It is
 * easy to state and easy to break, because it breaks by omission: somebody adds a Rename to a
 * wall and the guard is the thing they did not think about. That failure is invisible to every
 * other test: the control renders, the page looks right, and the only way to find out is to be a
 * guest and press it.
 *
 * So this signs in as a real guest and walks the screens, asserting that none of the words below is
 * anywhere on them, including inside a right-click menu.
 *
 * **The walls are filled by intercepting the list endpoints, and that is the load-bearing part.**
 * A guest is only shown a collection, a person or a tag that has something in it they can see, so a
 * guest with nothing shared sees empty walls, and an empty wall offers no controls, so the check
 * passes without checking anything, even with the guards deliberately deleted. Filling the walls
 * is what makes the assertion real.
 * What is under test here is the interface; the server's scoping has tests of its own.
 */

const GUEST = `e2e-surface-${Date.now()}`;
const GUEST_PASSWORD = 'A-Guest-Passphrase-9';

/** Screens a guest can reach. */
const REACHABLE = [
	'/browse',
	'/collections',
	'/people',
	'/sites',
	'/tags',
	'/favorites',
	'/settings/profile'
];

/** Walls with a right-click menu on whatever they are made of. */
const WALLS = ['/collections', '/people', '/sites', '/tags'];

/**
 * Controls that only an admin's routes accept.
 *
 * Matched against whole visible labels. "Share" and "Restrict" are here because the sharing panel
 * is an admin route; the rest change or destroy something a guest may look at and nothing more.
 *
 * Hide and Unhide are deliberately NOT here: hiding is personal, so what a guest hides is gone from
 * their own screens and stays ordinary for everybody else, and offering it promises nothing the
 * server will refuse. Renaming stays on the list because on these walls it means renaming the tag,
 * person or site everybody shares. A guest renames their own account from their profile, which is
 * a different control with a different name.
 */
const FORBIDDEN = [
	/^Delete/i,
	/^Remove$/i,
	/^Rename/i,
	/^Edit\b/i,
	/^Share\b/i,
	/^Restrict\b/i,
	/^Add (guest|collection|tag|folder)/i,
	/^Create/i,
	/^Use as the cover/i,
	/^Block$/i,
	/^Reset password/i
];

let made = false;

/** Make the guest once for this run. Asking twice is a refusal that leaves the setup half-done. */
async function ensureGuest(page: Page): Promise<void> {
	if (made) return;
	await signInAsAdmin(page);
	await page.goto('/settings/users');
	if ((await page.getByText(GUEST).count()) === 0) {
		await page.getByLabel('Username').fill(GUEST);
		await page.getByLabel('First password', { exact: true }).fill(GUEST_PASSWORD);
		await page.getByLabel('First password again').fill(GUEST_PASSWORD);
		await page.getByRole('button', { name: 'Create guest' }).click();
	}
	await expect(page.getByText(GUEST)).toBeVisible();
	made = true;
}

/** Rows on every wall, so there is something for a stray control to hang off. */
async function fillTheWalls(page: Page): Promise<void> {
	const json = (body: unknown) => ({
		status: 200,
		contentType: 'application/json',
		body: JSON.stringify(body)
	});
	// One shape covering four walls. The fields each screen does not read are simply ignored, and a
	// row that draws is all this needs: the assertion is about the controls beside it.
	const entity = (id: string, name: string) => ({
		id,
		name,
		cover_asset_id: null,
		favorite: false,
		rating: null,
		vault: false,
		asset_count: 3,
		item_count: 3,
		account_count: 1,
		alias_count: 0,
		// A site's card says how many people it has files of; without the count it throws.
		people_count: 0,
		color: 'slate',
		kind: 'site'
	});

	// All four walls are paged, so every one of these endpoints answers with a page rather than a
	// bare list. Handed the wrong shape a wall reads no rows at all: an empty wall, which offers
	// no controls, which is exactly the shape of check that passes without checking anything. So
	// the shapes have to be the real ones.
	const paged = (row: ReturnType<typeof entity>) => ({
		items: [row],
		total: 1,
		limit: 50,
		offset: 0
	});

	for (const [path, body] of [
		['**/api/collections*', paged(entity('c1', 'A shared collection'))],
		['**/api/tags*', paged(entity('t1', 'a-shared-tag'))],
		['**/api/people*', paged(entity('p1', 'A Shared Person'))],
		['**/api/sites*', paged(entity('s1', 'A Shared Site'))]
	] as const) {
		await page.route(path, (route) =>
			route.request().method() === 'GET' ? route.fulfill(json(body)) : route.continue()
		);
	}
}

async function signInAsGuest(page: Page): Promise<void> {
	await page.goto('/login');
	await page.getByLabel('Username').fill(GUEST);
	await page.getByLabel('Password', { exact: true }).fill(GUEST_PASSWORD);
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page).toHaveURL(/\/browse/);
	await fillTheWalls(page);
}

/** Every label a person could press on this screen right now. */
async function pressableLabels(page: Page): Promise<string[]> {
	return page.evaluate(() => {
		const roles = 'button, a, [role="menuitem"], [role="button"]';
		return Array.from(document.querySelectorAll(roles))
			.filter((el) => (el as HTMLElement).offsetParent !== null || el.closest('[role="menu"]'))
			.map((el) => (el.textContent ?? '').trim() || (el.getAttribute('aria-label') ?? '').trim())
			.filter((text) => text.length > 0);
	});
}

/**
 * Controls that read like an admin's on the walls and are the guest's own on one screen.
 *
 * Profile is about the account signed in, and two of its controls share a first word with a wall's
 * verb: Rename renames THIS account (the one name that is the account's own to decide, which is
 * why everybody has it), and Create PIN makes the credential Hidden needs, which the last test in
 * this file requires a guest to be offered. Both are routes the server accepts from a guest. Named
 * by screen and by whole label, so a Rename that turns up anywhere else still counts.
 */
const THEIRS: Record<string, RegExp[]> = {
	'/settings/profile': [/^Rename$/i, /^(Create|Save) PIN$/i]
};

function offending(labels: string[], where = ''): string[] {
	const allowed = THEIRS[where] ?? [];
	return labels.filter(
		(label) =>
			FORBIDDEN.some((rule) => rule.test(label)) && !allowed.some((rule) => rule.test(label))
	);
}

test('a guest is offered nothing the server would refuse them', async ({ page, browser }) => {
	await ensureGuest(page);

	const theirs = await browser.newContext();
	const guest = await theirs.newPage();
	try {
		await signInAsGuest(guest);

		for (const where of REACHABLE) {
			await guest.goto(where);
			await expect(guest.locator('h1').first()).toBeVisible();

			const found = offending(await pressableLabels(guest), where);
			expect(found, `${where} offers a guest: ${found.join(', ')}`).toEqual([]);
		}
	} finally {
		await theirs.close();
	}
});

test('and nothing appears in a right-click menu either', async ({ page, browser }) => {
	/* The menus are where it hides. A wall can look clean and still carry Rename and Delete a
	 * right-click away.
	 */
	await ensureGuest(page);

	const theirs = await browser.newContext();
	const guest = await theirs.newPage();
	try {
		await signInAsGuest(guest);

		for (const where of WALLS) {
			await guest.goto(where);
			await expect(guest.locator('h1').first()).toBeVisible();

			const card = guest.locator('.card, .chip, li .cover').first();
			await expect(card, `${where} should have a row to right-click`).toBeVisible();
			await card.click({ button: 'right' });

			const menu = guest.locator('[role="menu"]');
			await expect(menu, `${where} should open a menu`).toBeVisible();

			const found = offending(await pressableLabels(guest));
			expect(found, `${where} offers a guest, in a menu: ${found.join(', ')}`).toEqual([]);
			await guest.keyboard.press('Escape');
		}
	} finally {
		await theirs.close();
	}
});

/*
 * The other direction.
 *
 * Everything above asks whether a forbidden label is ABSENT, and a check shaped like that passes on
 * a screen with nothing on it at all, including a menu missing Hide for guests.
 *
 * So this asserts the presence. A guest has the whole of Hidden (their own PIN, their own hidden
 * things, their own lock), and the way in is a menu item on the walls and a PIN they can set.
 */
test('a guest IS offered Hide, and can set the PIN that makes it work', async ({
	page,
	browser
}) => {
	await ensureGuest(page);

	const theirs = await browser.newContext();
	const guest = await theirs.newPage();
	try {
		await signInAsGuest(guest);

		for (const where of WALLS) {
			await guest.goto(where);
			await expect(guest.locator('h1').first()).toBeVisible();

			const card = guest.locator('.card, .chip, li .cover').first();
			await expect(card, `${where} should have a row to right-click`).toBeVisible();
			await card.click({ button: 'right' });
			await expect(guest.locator('[role="menu"]')).toBeVisible();

			// Normalised before matching: a menu item's label is the icon's glyph and then the word,
			// so an exact match against "Hide" misses the item that is plainly there.
			const labels = (await pressableLabels(guest)).map((label) =>
				label.replace(/\s+/gu, ' ').trim()
			);
			expect(
				labels.some((label) => /(?:^|\s)(?:Hide|Unhide)$/i.test(label)),
				`${where} offers a guest no way to hide anything: ${labels.join(', ')}`
			).toBe(true);
			await guest.keyboard.press('Escape');
		}

		/* And the PIN, without which every one of those items is refused by the server.
		 *
		 * On Profile, not Privacy: the PIN is a credential of YOURS, so it sits beside the
		 * password; what Privacy keeps is everything about what Hidden hides and when it shuts,
		 * which is about the feature rather than about you. `settings-ui/Profile.svelte` carries
		 * both halves of that argument.
		 */
		await guest.goto('/settings/profile');
		await expect(guest.getByRole('heading', { name: 'PIN', exact: true })).toBeVisible();
		await expect(guest.getByLabel('Your password')).toBeVisible();
	} finally {
		await theirs.close();
	}
});
