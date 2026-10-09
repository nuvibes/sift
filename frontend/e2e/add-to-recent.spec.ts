import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';
import { removeCollections, seedCollection, writeInterfaceState } from './seed';

const STEM = `e2e-pick-${Date.now()}`;
const LETTERS = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I'];
const RECENT = ['F', 'C', 'H', 'A', 'E', 'B'];

const ids = new Map<string, string>();
const nameOf = (letter: string) => `${STEM}-${letter}`;

const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

const ASSETS = [
	{
		id: 'a1',
		media_type: 'image',
		width: 1000,
		height: 1000,
		duration_ms: null,
		rating: null,
		favorite: false,
		concealed: false
	}
];

test.beforeAll(async ({ browser }) => {
	const page = await browser.newPage();
	await signInAsAdmin(page);
	for (const letter of LETTERS) ids.set(letter, await seedCollection(page, nameOf(letter)));
	const record = RECENT.map((letter) => ({ id: ids.get(letter)!, name: nameOf(letter), used: 1 }));
	await writeInterfaceState(page, { 'frequent.collection': JSON.stringify(record) });
	await page.close();
});

test.afterAll(async ({ browser }) => {
	const page = await browser.newPage();
	await signInAsAdmin(page);
	await writeInterfaceState(page, { 'frequent.collection': '[]' });
	await removeCollections(page, [...ids.values()]);
	await page.close();
});

async function openGrid(page: Page) {
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({ json: { items: ASSETS, total: ASSETS.length, limit: 50, offset: 0 } })
	);
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);
	await page.route('**/api/assets/*/preview', (route) => route.fulfill({ status: 404 }));
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();
}

test('the flyout draws the last five picks first, marked, then every collection A to Z', async ({
	page
}) => {
	await signInAsAdmin(page);
	await openGrid(page);

	await page.locator('.tile').first().click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Add to' }).hover();
	await page.getByRole('menuitem', { name: 'Collection', exact: true }).hover();
	const narrow = page.getByLabel('Filter the collections list');
	await expect(narrow).toBeVisible();

	const list = page.locator('.pick .list').filter({ hasText: STEM }).first();
	await expect(list.locator('.item').filter({ hasText: nameOf('I') })).toBeVisible();

	/* Every row this spec made, in drawn order, with whether it wears the mark. */
	const rows = await list.locator('.item').evaluateAll(
		(items, stem) =>
			items
				.map((item) => ({
					name: (item.querySelector('.name')?.textContent ?? '').replace(/\s+/g, ' ').trim(),
					marked: item.querySelector('.recent') !== null
				}))
				.filter((row) => row.name.startsWith(stem)),
		STEM
	);

	const drawn = rows.map((row) => row.name.slice(STEM.length + 1));
	const head = RECENT.slice(0, 5);
	const tail = LETTERS.filter((letter) => !head.includes(letter));
	expect(drawn, 'the five newest picks, newest first, then the rest A to Z').toEqual([
		...head,
		...tail
	]);
	expect(
		rows.map((row) => row.marked),
		'the recency mark is on the five recent rows and on no other'
	).toEqual([...head.map(() => true), ...tail.map(() => false)]);
	await expect(list.getByRole('img', { name: 'Chosen recently' })).toHaveCount(5);
});
