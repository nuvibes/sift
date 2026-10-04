/* The asset modal's own frame: how big it is, and what sits behind it.
 *
 * Both are geometry and a real compositing property, so neither is answerable without a layout
 * engine: the unit environment renders the markup happily whatever the stylesheet does with it.
 */
import { expect, test, type Locator, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

/*
 * Wait for the viewer to finish arriving before measuring it.
 *
 * It grows into place on a transition, so `toBeVisible` is the FIRST frame of that rather than
 * where it ends up, and every measurement taken there is of a smaller, lower dialog.
 *
 * Two frames agreeing about the box is not enough: a Svelte `css` transition compiles to a real CSS
 * animation, which the browser runs off the main thread, so two `getBoundingClientRect()` reads a
 * frame apart can agree in the MIDDLE of it and the wait ends early with no tell at all.
 *
 * The animation's own `finished` promise is the fact, so that is what is waited on. A frame first,
 * because the transition is registered on the tick after the element appears, and the loop repeats
 * so that a second animation starting behind the first is waited on too; it ends when a frame goes
 * by with nothing running.
 */
async function settled(sheet: Locator): Promise<void> {
	await sheet.evaluate(async (element) => {
		for (let round = 0; round < 4; round += 1) {
			await new Promise((resolve) => requestAnimationFrame(() => resolve(undefined)));
			const running = element.getAnimations();
			if (running.length === 0) return;
			// A cancelled animation rejects; that is the element having settled, not a failure.
			await Promise.all(running.map((one) => one.finished.catch(() => undefined)));
		}
	});
}

const CLIP = {
	id: 'm1',
	media_type: 'photo',
	width: 1600,
	height: 900,
	duration_ms: null,
	favorite: false,
	rating: null,
	concealed: false,
	// What wrote to it without a person doing it, which the marks beside the name are drawn from.
	// Present and empty is what the server answers for a file nothing has enriched.
	enriched_by: [],
	original_filename: 'holiday.jpg'
};

async function serveLibrary(page: Page) {
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [CLIP], total: 1, limit: 50, offset: 0 })
		})
	);
	await page.route('**/api/assets/m1', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CLIP) })
	);
	await page.route('**/api/assets/*/thumb', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/assets/*/preview', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/assets/m1/view', (route) => route.fulfill({ status: 204, body: '' }));
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
});

test('the popup is a quarter bigger than the 900px base', async ({ page }) => {
	// A quarter bigger than 900px is 1125, which is what should show up here given a viewport wide
	// enough that the vw cap never binds.
	await serveLibrary(page);
	await page.goto('/browse');
	await page.locator('.tile').first().click();

	const sheet = page.getByRole('dialog');
	await expect(sheet).toBeVisible();
	await settled(sheet);
	const box = (await sheet.boundingBox())!;
	expect(box.width, `the dialog was ${box.width}px wide`).toBeGreaterThan(1100);
	expect(box.width).toBeLessThan(1150);
});

test('the popup lines up with the line under the search bar, not the card above it', async ({
	page
}) => {
	// Level with the line under the top bar: not lower (a visible gap reads as the dialog floating
	// below the page), and not level with the content card's outer edge, which is a whole top bar
	// too high.
	await serveLibrary(page);
	await page.goto('/browse');

	const topbarBottom = await page
		.locator('.topbar')
		.evaluate((el) => el.getBoundingClientRect().bottom);

	await page.locator('.tile').first().click();
	const sheet = page.getByRole('dialog');
	await expect(sheet).toBeVisible();

	await settled(sheet);

	const sheetTop = (await sheet.boundingBox())!.y;

	expect(
		Math.abs(sheetTop - topbarBottom),
		`the topbar ends at ${topbarBottom} and the dialog starts at ${sheetTop}`
	).toBeLessThan(2);
});

test('the grid behind the popup is softened, not just darkened', async ({ page }) => {
	await serveLibrary(page);
	await page.goto('/browse');
	await page.locator('.tile').first().click();

	await expect(page.getByRole('dialog')).toBeVisible();
	const veil = page.locator('.veil');
	const blur = await veil.evaluate((el) => getComputedStyle(el).backdropFilter);
	expect(blur, 'the veil has no backdrop-filter at all').not.toBe('none');
	expect(blur).toContain('blur');
});

test('a tile clicked and clicked away from is not left outlined', async ({ page }) => {
	/*
	 * The modal returns focus to the tile it was opened from on close, which is right for a
	 * keyboard user who would otherwise be dropped at the top of the page. But a tile CLICKED with
	 * a mouse shows no focus ring, and returning focus to it would light one that sits there like a
	 * stuck selection. For a mouse-only session there is nothing to put back, so focus is left
	 * where the close leaves it and the tile keeps no ring.
	 */
	await serveLibrary(page);
	await page.goto('/browse');

	const tile = page.locator('.tile').first();
	await tile.click(); // opened with the mouse
	await expect(page.getByRole('dialog')).toBeVisible();

	// Off to the side of the sheet, where the veil is actually exposed: a click in the middle lands
	// on the dialog that sits over it.
	await page.locator('.veil').click({ position: { x: 8, y: 450 } }); // closed with the mouse
	await expect(page.getByRole('dialog')).toHaveCount(0);

	expect(
		await tile.evaluate((el) => el === document.activeElement),
		'focus was pushed back onto the tile after a mouse-only session'
	).toBe(false);
	expect(
		await tile.evaluate((el) => el.matches(':focus-visible')),
		'the clicked tile kept its blue outline after the modal closed'
	).toBe(false);
});

test('a keyboard user is put back on the tile they opened', async ({ page }) => {
	/* The other half: somebody who arrived by keyboard must not be dropped at the top of the
	 * page.
	 *
	 * What decides it is how the dialog was OPENED, not how it was closed. Opening a clip with
	 * the mouse and pressing Escape must not light the tile as though it were being pointed at:
	 * Escape is a fast way out for a mouse user too, and says nothing about how they are working.
	 */
	await serveLibrary(page);
	await page.goto('/browse');

	const tile = page.locator('.tile').first();
	await tile.focus();
	await page.keyboard.press('Enter');
	await expect(page.getByRole('dialog')).toBeVisible();

	await page.keyboard.press('Escape');
	await expect(page.getByRole('dialog')).toHaveCount(0);

	expect(
		await tile.evaluate((el) => el === document.activeElement),
		'a keyboard user was dropped off their tile'
	).toBe(true);
});

test('and a mouse user pressing Escape is not given a focus ring', async ({ page }) => {
	// Open a clip with the mouse, press Escape: the outline must not come back on the tile.
	await serveLibrary(page);
	await page.goto('/browse');

	const tile = page.locator('.tile').first();
	await tile.click();
	await expect(page.getByRole('dialog')).toBeVisible();

	await page.keyboard.press('Escape');
	await expect(page.getByRole('dialog')).toHaveCount(0);

	expect(
		await tile.evaluate((el) => el.matches(':focus-visible')),
		'Escape after a click left the tile outlined'
	).toBe(false);
});

/*
 * THE RECORD, AS THREE PANES.
 *
 * In a browser rather than in the unit suite because what is being asked is whether the panes are
 * really a tablist in the shipped page (one pane reachable at a time, each drawing its own half of
 * the registry), and the unit environment renders the markup happily whatever the library does with
 * it. The split itself is proved in `RecordGrid.svelte.test.ts`; this is that the strip is wired.
 */
test('the record is three panes and each draws its own half', async ({ page }) => {
	await serveLibrary(page);
	// The history is this file's own address and this file is invented, so the server would answer
	// 404 for it. Empty is the ordinary answer for a library that was scanned rather than organized.
	await page.route('**/api/assets/m1/history*', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: '{"items":[],"total":0}' })
	);
	await page.goto('/browse');
	await page.locator('.tile').first().click();

	const sheet = page.getByRole('dialog');
	await expect(sheet).toBeVisible();

	const strip = sheet.getByRole('tablist', { name: "What this file's record says" });
	await expect(strip.getByRole('tab')).toHaveCount(3);
	/* Every opening starts on About (the pane is not remembered), so the first assertion below
	   is a fact about the screen rather than about what a previous test in this shared account
	   happened to press. */

	// What somebody wrote. Nothing has been written about this file, so the rows invite a value.
	await expect(sheet.getByRole('tabpanel')).toContainText('Title');
	await expect(
		sheet.getByRole('tabpanel').getByRole('button', { name: 'Add Title' })
	).toBeVisible();

	// What the file is. The other half, and the title is not in it.
	await strip.getByRole('tab', { name: /^Media/ }).click();
	await expect(sheet.getByRole('tabpanel')).toContainText('Dimensions');
	await expect(sheet.getByRole('tabpanel')).not.toContainText('Title');

	/* What has happened to it, read when the pane is opened rather than with the file. The tab is
	   called History. */
	await strip.getByRole('tab', { name: /^History/ }).click();
	await expect(sheet.getByRole('tabpanel')).toContainText('Nothing has been recorded');
});
