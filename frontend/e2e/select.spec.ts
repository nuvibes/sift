/* THE SHARED CHOOSER, DRIVEN.
 *
 * `Select` is drawn on many screens, and a unit test cannot prove any of it: opening one needs a
 * real pointer and a portal, and the list is rendered outside the component that asked for it,
 * so a unit test can mount it, read the trigger, and see nothing else at all.
 *
 * The Sort chooser on the screen bar is the caller used here, because it is on every wall in the
 * application and needs nothing but a library to be drawn. What is under test is the PRIMITIVE:
 * where its list is rendered, that a pointer and a keyboard both reach it, that the trigger
 * reports what was chosen, and that leaving without choosing changes nothing.
 */
import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

const ASSETS = [
	{ id: 's1', media_type: 'video', width: 1920, height: 1080, duration_ms: 95_000 },
	{ id: 's2', media_type: 'image', width: 1000, height: 1000, duration_ms: null }
].map((asset) => ({ ...asset, favorite: false, concealed: false, rating: null, thumb: true }));

const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

async function openWall(page: Page) {
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: ASSETS, total: ASSETS.length, limit: 50, offset: 0 })
		})
	);
	await page.route('**/api/assets/*/thumb*', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);
	await page.route('**/api/assets/*/preview*', (route) => route.fulfill({ status: 404 }));
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();
}

const trigger = (page: Page) => page.getByRole('button', { name: 'Sort by', exact: true });

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
	await openWall(page);
});

test('the list is drawn OUTSIDE the bar that asked for it', async ({ page }) => {
	/* The portal, which is the whole reason a unit environment can see nothing here. The bar is a
	   short row with its own stacking context; a list drawn inside it would be clipped by the row
	   and trapped behind whatever the screen puts over it. */
	await trigger(page).click();
	const list = page.getByRole('listbox');
	await expect(list).toBeVisible();

	const inside = await list.evaluate((element) => element.closest('.screenbar') !== null);
	expect(inside, 'the list was rendered inside the bar, where it can be clipped').toBe(false);
});

test('a pointer chooses, the list shuts, and the trigger says what was chosen', async ({
	page
}) => {
	await trigger(page).click();
	const wanted = page.getByRole('option', { name: 'Name A-Z', exact: true });
	await expect(wanted).toBeVisible();

	await wanted.click();

	await expect(page.getByRole('listbox'), 'the list stayed open over the wall').toBeHidden();
	await expect(trigger(page)).toContainText('Name A-Z');
});

test('the keyboard reaches every option, and Enter takes one', async ({ page }) => {
	/* A chooser somebody can open and not operate is worse than one that is simply absent: the
	   keyboard is the whole of what a screen reader and a speech command have. */
	await trigger(page).press('Enter');
	await expect(page.getByRole('listbox')).toBeVisible();

	await page.keyboard.press('ArrowDown');
	const highlighted = page.locator('[role="option"][data-highlighted]');
	await expect(highlighted).toHaveCount(1);
	/* The row's NAME, not the whole of its text. A row draws a mark beside its name: an icon
	   is a font ligature, so it is text, and it lands in the middle of a comparison of two strings
	   that look identical printed out. What the trigger has to report is the name. */
	const chosen = (await highlighted.locator('.ui-select-item-label').textContent())?.trim();

	await page.keyboard.press('Enter');

	await expect(page.getByRole('listbox')).toBeHidden();
	expect(chosen, 'nothing was highlighted to choose').toBeTruthy();
	await expect(trigger(page)).toContainText(chosen as string);
});

test('leaving without choosing changes nothing', async ({ page }) => {
	/* The other half, and the one that makes the two above worth having: a chooser that wrote on
	   every open would satisfy both of them perfectly. */
	const before = (await trigger(page).textContent())?.trim();

	await trigger(page).click();
	await expect(page.getByRole('listbox')).toBeVisible();
	await page.keyboard.press('Escape');

	await expect(page.getByRole('listbox')).toBeHidden();
	expect((await trigger(page).textContent())?.trim()).toBe(before);
});
