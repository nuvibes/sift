/* The board in a real browser: every card one shape, and its one button only opens the pile.
 *
 * A card on Organize names its pile, says what the pile is for, counts it, shows a few stills and
 * opens the pile's page with one button, Review. Nothing on the board decides anything: a question
 * about one named person or file is asked on the page, never on the card. Every part of that is
 * covered by unit tests; this drives the press end to end and measures the cards laid out.
 *
 * So the fixture is a MOCKED board, which is what every spec in this suite does with data it
 * needs to be exact about (see `asset-modal.spec.ts` and `compress.spec.ts`). What is real here
 * is the client: the cards, their sizes as the browser lays them out, and where a press goes. The
 * shapes below are the server's own `QueueView`, with the `purpose` it sends.
 */
import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/** One pile, as the server sends it. */
function pile(over: Record<string, unknown>) {
	return {
		name: 'folders',
		title: 'Folders to review',
		decision: 'Say whether a folder is the person its name suggests.',
		purpose: 'Folders whose names look like a person or a Site, for you to confirm.',
		icon: 'folder',
		count: 2,
		verb: 'folders to name',
		verb_one: 'folder to name',
		band: 'decision',
		group: null,
		group_title: null,
		pending: true,
		preview: [] as { kind: string; id: string; href: string | null }[],
		...over
	};
}

/** Three piles: one with stills, one without, one in another band. */
const BOARD = {
	queues: [
		pile({
			preview: Array.from({ length: 8 }, (_unused, at) => ({
				kind: 'asset',
				id: `a${at}`,
				href: `/asset/a${at}`
			})),
			// An older server's first question. The card must not ask it.
			first: {
				id: 'claim-1',
				kind: 'claim',
				question: 'Is Elina Sorrel the person in this folder?',
				detail: 'Downloads/Elina Sorrel',
				preview: null,
				choices: [
					{
						label: 'Yes, file them',
						send: { method: 'POST', path: '/suggestions/claim-1/confirm' }
					}
				]
			}
		}),
		pile({
			name: 'shoots',
			title: 'Shoots',
			purpose: 'Photos that look like one shoot, for you to create a Photo Set from.',
			icon: 'photo_library',
			count: 5,
			verb: 'shoots to review',
			verb_one: 'shoot to review'
		}),
		pile({
			name: 'filenames',
			title: 'Enriched from filenames',
			purpose: 'Files Sift added to a username because of their names.',
			icon: 'description',
			count: 9,
			band: 'log',
			verb: 'files enriched from names',
			verb_one: 'file enriched from its name'
		})
	]
};

async function serve(page: Page): Promise<{ posted: string[] }> {
	const posted: string[] = [];
	await page.route('**/api/assets/*/thumb', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/workbench', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(BOARD) })
	);
	await page.route('**/api/suggestions/**', async (route) => {
		posted.push(new URL(route.request().url()).pathname);
		await route.fulfill({ status: 500 });
	});
	return { posted };
}

function cards(page: Page) {
	return page.locator('ul[aria-label="Organize"] > li');
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1600, height: 1000 });
});

test('every card is one size and one shape, with one button saying Review', async ({ page }) => {
	await serve(page);
	await page.goto('/organize');

	await expect(cards(page)).toHaveCount(3);
	const sizes = await cards(page).evaluateAll((all) =>
		all.map((one) => {
			const box = one.getBoundingClientRect();
			return [Math.round(box.width), Math.round(box.height)];
		})
	);
	expect(new Set(sizes.map((one) => one.join('x'))).size).toBe(1);

	for (const at of [0, 1, 2]) {
		const card = cards(page).nth(at);
		await expect(card.getByRole('button')).toHaveCount(1);
		await expect(card.getByRole('button')).toHaveText(/Review/);
		await expect(card.getByRole('link')).toHaveCount(0);
	}
	await expect(cards(page).first().locator('.purpose')).toHaveText(
		'Folders whose names look like a person or a Site, for you to confirm.'
	);
	await expect(page.getByText('Is Elina Sorrel the person in this folder?')).toHaveCount(0);
	await expect(page.getByRole('button', { name: 'Yes, file them' })).toHaveCount(0);
});

test('Review opens the pile and decides nothing', async ({ page }) => {
	const traffic = await serve(page);
	await page.goto('/organize');

	await cards(page).first().getByRole('button', { name: 'Review Folders to review' }).click();

	await expect(page).toHaveURL(/\/organize\/folders$/);
	expect(traffic.posted).toEqual([]);
});

test('a press on the ground of a card opens its pile, as the button does', async ({ page }) => {
	const traffic = await serve(page);
	await page.goto('/organize');

	await cards(page).first().locator('.purpose').click();

	await expect(page).toHaveURL(/\/organize\/folders$/);
	expect(traffic.posted).toEqual([]);
});
