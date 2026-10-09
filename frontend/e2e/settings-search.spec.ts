import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/*
 * The settings search, from outside: the unit tests prove a result names a section id the index
 * knows, not that the id OPENS anything, nor where the results stand or which box Ctrl-F means.
 */

const box = () => '.search-slot input';
const results = () => '[aria-label="Search results"]';

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1600, height: 1000 });
	await page.goto('/settings/appearance');
	await expect(page.locator(box())).toBeVisible();
});

test('a word nothing is called still finds the setting it belongs to', async ({ page }) => {
	/* Why a hand-written pane declares `keywords`: the control is Tunnels, a person types "vpn". */
	await page.locator(box()).fill('vpn');

	await expect(page.locator(results())).toContainText('Tunnels');
});

test('a section can be found by its own name', async ({ page }) => {
	/* A section is found by its own name. Theater is a group on Playback. */
	await page.locator(box()).fill('playback');

	await expect(page.locator(`${results()} button`).first()).toHaveText(/Playback/);
});

test('the letters that were typed are marked, so a row explains itself', async ({ page }) => {
	/* The library dropdown's mark: one component, one colour. */
	await page.locator(box()).fill('theat');

	await expect(page.locator(`${results()} b`).first()).toHaveText('Theat');
});

test('a row found only by its sentence says so, and one found by its name does not', async ({
	page
}) => {
	/* A row found by its help sentence shows the sentence; a name match does not, or the 220px
	 * column doubles. The row is named and waited for: the list is built from a request. */
	await page.locator(box()).fill('default layout');
	const named = page
		.locator(`${results()} .under button`)
		.filter({ hasText: 'Default layout when Theater opens' });
	await expect(named, 'the row this is about never arrived').toHaveCount(1);
	await expect(named.locator('.why'), 'a name match was explained anyway').toHaveCount(0);

	/* "differ" is in that row's help sentence only, not its name or keywords. */
	await page.locator(box()).fill('differ');
	const why = page.locator(`${results()} .why`).first();
	await expect(why).toBeVisible();
	await expect(why).toContainText(/differ/i);
	await expect(
		why.locator('b').first(),
		'the sentence is shown with nothing marked in it'
	).toHaveText(/differ/i);
});

test('the results stand where the section list stands, not above it', async ({ page }) => {
	/* The results replace the list in a 220px column, and the pane beside it must not move down. */
	const sections = page.locator('nav[aria-label="Settings sections"]');
	await expect(sections).toBeVisible();

	/* Against the PANEL, both boxes in one pass: the panel rises as it opens. */
	const offset = () =>
		page.evaluate(() => {
			const panel = document.querySelector('.settings')!.getBoundingClientRect();
			const pane = document.querySelector('.pane-slot')!.getBoundingClientRect();
			return { down: pane.y - panel.y, tall: pane.height };
		});
	const before = await offset();

	await page.locator(box()).fill('let');
	await expect(sections).toBeHidden();
	await expect(page.locator(results())).toBeVisible();

	const during = await offset();
	expect(during.down, 'the results pushed the pane down the panel').toBeCloseTo(before.down, 0);
	expect(during.tall, 'the results squeezed the pane').toBeCloseTo(before.tall, 0);

	await page.locator(box()).fill('');
	await expect(sections).toBeVisible();
});

test('the pane follows the first thing found, as it is typed', async ({ page }) => {
	/* Otherwise the pane beside the results stays on whatever was open. */
	await expect(page).toHaveURL(/\/settings\/appearance/);

	/* Theater's rows are drawn on Playback, so the pane and the address are Playback's. */
	await page.locator(box()).fill('theater');

	await expect(page).toHaveURL(/\/settings\/playback/);
	await expect(page.getByRole('heading', { name: 'Playback' }).first()).toBeVisible();
});

test('a result opens the section it names, by its ID and not by its name', async ({ page }) => {
	/* Registered under the section NAME "Smart Search", whose address is `/settings/semantic`, so a
	 * result linking by name would open nothing. The row under the heading carries a key. */
	await page.locator(box()).fill('description models');
	const setting = page.locator(`${results()} .under button`).first();
	await expect(setting).toBeVisible();
	await setting.click();

	await expect(page).toHaveURL(/\/settings\/semantic/);
	// And the pane behind the address is really that pane.
	await expect(page.getByRole('heading', { name: 'Smart Search' }).first()).toBeVisible();
});

test('opening a result leaves ONE thing to close, not two', async ({ page }) => {
	/* One dismissal leaves Settings: a result replaces the history entry, never pushes one. */
	await page.locator(box()).fill('description models');
	await page.locator(`${results()} .under button`).first().click();
	await expect(page).toHaveURL(/\/settings\/semantic/);

	/* Beside the panel, by position: the veil and the corner button share a name. */
	await page.mouse.click(20, 500);

	await expect(page).not.toHaveURL(/\/settings\//);
});

test('Ctrl-F means THIS box while Settings is open, not the library sheet', async ({ page }) => {
	/* Outside this panel, Ctrl-F is the library's search, which opens a sheet over the panel. */
	await page.locator('.pane').click({ position: { x: 10, y: 10 } });
	await page.keyboard.press('Control+f');

	await expect(page.locator(box())).toBeFocused();
	await expect(page.getByRole('dialog', { name: /search/i })).toHaveCount(0);
});

test('the arrows walk the results and Enter opens the one they are on', async ({ page }) => {
	/* The rows are one flat sequence, so "down" crosses from one group into the next. */
	await page.locator(box()).fill('let');
	await expect(page.locator(`${results()} .item`).first()).toBeVisible();

	await page.locator(box()).press('ArrowDown');
	await expect(page.locator(`${results()} .item.on`)).toHaveCount(1);
	const first = await page.locator(`${results()} .item.on`).innerText();

	await page.locator(box()).press('ArrowDown');
	const second = await page.locator(`${results()} .item.on`).innerText();
	expect(second, 'the second press did not move').not.toBe(first);

	await page.locator(box()).press('ArrowUp');
	expect(await page.locator(`${results()} .item.on`).innerText()).toBe(first);

	// Or a screen reader hears nothing move.
	await expect(page.locator(box())).toHaveAttribute('aria-activedescendant', /.+/);

	await page.locator(box()).press('Enter');
	await expect(page.locator(results())).toHaveCount(0);
});

test('opening a result puts the section list back', async ({ page }) => {
	/* Left up, coming back to Settings would show yesterday's search. */
	await page.locator(box()).fill('theater');
	await page.locator(`${results()} .item`).first().click();

	await expect(page.locator(box())).toHaveValue('');
	await expect(page.locator('nav[aria-label="Settings sections"]')).toBeVisible();
});

test('the cross is the app cross, not the browser one', async ({ page }) => {
	/* `app.css` turns the browser's own cross off; `.field-clear` is the library box's too. */
	await expect(page.locator(`.search-slot .field-clear`)).toHaveCount(0);

	await page.locator(box()).fill('vpn');
	await expect(page.locator(`.search-slot .field-clear`)).toBeVisible();

	await page.locator(`.search-slot .field-clear`).click();
	await expect(page.locator(box())).toHaveValue('');
	await expect(page.locator('nav[aria-label="Settings sections"]')).toBeVisible();
});

test('Escape puts the list back', async ({ page }) => {
	await page.locator(box()).fill('vpn');
	await expect(page.locator(results())).toBeVisible();

	await page.locator(box()).press('Escape');

	await expect(page.locator(results())).toHaveCount(0);
	await expect(page.locator('nav[aria-label="Settings sections"]')).toBeVisible();
	// Emptied the box, not closed Settings.
	await expect(page).toHaveURL(/\/settings\//);
});

test('a result for a hand-built control points at the block it names', async ({ page }) => {
	/* A result opens its section and rings the row it names: a hand-built control must carry its
	 * key as an id, or `revealSetting` quietly gives up. The static gate cannot see where the id
	 * is. */
	await page.locator(box()).fill('cookies');
	await page.locator(`${results()} .under button`).first().click();

	const block = page.locator('#sites\\.cookies');
	await expect(block).toBeVisible();
	/* The reveal's ring says the right element was found. */
	await expect(block).toHaveAttribute('data-sift-found', '', { timeout: 5000 });
});
