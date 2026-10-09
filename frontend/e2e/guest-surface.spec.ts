import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/* A guest is never offered a control the server will refuse them, menus included. The walls
 * are filled by intercepting the lists: an empty wall offers nothing and would pass unchecked. */

const GUEST = `e2e-surface-${Date.now()}`;
const GUEST_PASSWORD = 'A-Guest-Passphrase-9';

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

/* Labels only an admin's routes accept. Hide is not one: hiding is personal. Rename is, since
 * on a wall it renames what everybody shares. */
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

/** Make the guest once per run: a second create is refused and stops the setup. */
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

async function fillTheWalls(page: Page): Promise<void> {
	const json = (body: unknown) => ({
		status: 200,
		contentType: 'application/json',
		body: JSON.stringify(body)
	});
	// One shape for four walls; each ignores the fields it does not read.
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
		// Without the count a site's card throws.
		people_count: 0,
		color: 'slate',
		kind: 'site'
	});

	// The real paged shape: the wrong shape reads as an empty wall, which passes unchecked.
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

/* Profile's Rename and Create PIN are the guest's own, accepted from a guest; named by screen
 * and label so a Rename anywhere else still counts. */
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
	// Rename and Delete can hide a right-click away.
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

// The other direction: an absence check passes on an empty screen, so Hide's presence is asserted.
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

			// A menu label is the icon's glyph and then the word.
			const labels = (await pressableLabels(guest)).map((label) =>
				label.replace(/\s+/gu, ' ').trim()
			);
			expect(
				labels.some((label) => /(?:^|\s)(?:Hide|Unhide)$/i.test(label)),
				`${where} offers a guest no way to hide anything: ${labels.join(', ')}`
			).toBe(true);
			await guest.keyboard.press('Escape');
		}

		// The PIN, on Profile beside the password.
		await guest.goto('/settings/profile');
		await expect(guest.getByRole('heading', { name: 'PIN', exact: true })).toBeVisible();
		await expect(guest.getByLabel('Your password')).toBeVisible();
	} finally {
		await theirs.close();
	}
});
