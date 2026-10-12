import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/*
 * A swap's receipt, opened from the line History keeps for it: the heavy number, the groups and how
 * each was filed, then Undo this swap, which asks once and then says what it removed. An Undo that
 * met files another program held open says so and is pressed again for those.
 *
 * The e2e server has one library and no second Sift to swap with, so the line and the receipt are
 * the server's shapes answered here; the removal itself is the server's own tests'.
 */

const NOW = Math.floor(Date.now() / 1000);

function words(text: string) {
	return { text, kind: null, id: null, href: null, gone: false, rest: [], lead: '' };
}

const LINE = {
	id: 'e2e-swap-line',
	at: NOW - 60,
	actor: { kind: 'sift', id: null, name: null },
	verb: 'swap_ended',
	subjects: [{ kind: 'swap', id: 'e2e-session', name: null, href: null, gone: false }],
	object: null,
	count: null,
	receipt: null,
	pieces: [words('The swap with device DESK-2F9A ended: 12 files received')],
	detail: [],
	more: '',
	folded: 1,
	first: 'e2e-swap-line',
	standing: 0,
	report: null,
	still: null
};

const RECEIPT = {
	session_id: 'e2e-session',
	role: 'guest',
	two_way: false,
	device: 'DESK-2F9A',
	device_name: null,
	started_at: NOW - 700,
	ended_at: NOW - 60,
	seconds: 640,
	reason: 'done',
	files: 12,
	bytes: 1_200_000_000,
	back_files: 0,
	back_bytes: 0,
	rate: 1_900_000,
	back_rate: null,
	moving: 630,
	back_moving: null,
	waited: 180,
	tunnel: null,
	cut_offs: 0,
	cut_seconds: 0,
	not_kept: 0,
	folder: { id: 'e2e-folder', path: 'Swaps/Swap-E2E', own: true },
	groups: [
		{
			kind: 'person',
			id: null,
			name: 'Bryn Calloway',
			way: 'landed',
			files: 12,
			bytes: 1_200_000_000,
			filed: 'created',
			faces: 3,
			usernames: 0
		}
	],
	skipped: [],
	refused: 0,
	held: 2,
	undo: null,
	can_undo: true
};

const IN_USE = {
	...RECEIPT,
	can_undo: true,
	undo: { at: NOW, files: 9, from_disk: true, people: 1, sites: 0, in_use: 2, kept: 1 }
};

const UNDONE = {
	...RECEIPT,
	can_undo: false,
	undo: { at: NOW, files: 11, from_disk: true, people: 1, sites: 0, in_use: 0, kept: 1 }
};

test('a swap ended, its receipt read from History, and Undo pressed', async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 1000 });
	let undos = 0;
	await page.route(
		(url) => url.pathname === '/api/ledger',
		(route) =>
			route.request().method() === 'GET'
				? route.fulfill({ json: { items: [LINE], total: 1, offset: 0 } })
				: route.fallback()
	);
	await page.route('**/api/swap/receipts/**', (route) => {
		if (route.request().method() === 'POST') {
			undos += 1;
			return route.fulfill({ json: undos > 1 ? UNDONE : IN_USE });
		}
		return route.fulfill({ json: [RECEIPT, IN_USE, UNDONE][Math.min(undos, 2)] });
	});

	await page.goto('/settings/tasks?show=history#activity.history');
	await page.getByRole('button', { name: 'Receipt', exact: true }).click();
	const card = page.locator('.receipt');
	await expect(card.locator('.figure')).toHaveText('12');
	await expect(card).toContainText('files landed, 1.2 GB, in 11 min');
	await expect(card).toContainText('Joined 3 min before the first file.');
	await expect(card).toContainText('Bryn Calloway');
	await expect(card).toContainText('created, 3 faces learned');
	await expect(card).toContainText('2 already here');

	await card.getByRole('button', { name: 'Undo this swap' }).click();
	const dialog = page.getByRole('alertdialog');
	await expect(dialog).toContainText('They are deleted from disk in Swaps/Swap-E2E.');
	expect(undos).toBe(0);
	await dialog.getByRole('button', { name: 'Undo this swap' }).click();

	await expect(card).toContainText(
		'Undone: 9 files removed from the library and deleted from disk. 1 kept: already in the library. 2 still in use: Undo again later.'
	);
	expect(undos).toBe(1);

	// Again, for the two that were in use: the question names only those.
	await card.getByRole('button', { name: 'Undo this swap' }).click();
	await expect(dialog).toContainText('2 files are removed from the library');
	await dialog.getByRole('button', { name: 'Undo this swap' }).click();
	await expect(card).toContainText(
		'Undone: 11 files removed from the library and deleted from disk. 1 kept: already in the library. Removed with them: 1 person it created.'
	);
	expect(undos).toBe(2);
	await expect(card.getByRole('button', { name: 'Undo this swap' })).toHaveCount(0);
});
