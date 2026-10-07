import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/*
 * The settings search, from outside.
 *
 * Its whole value is reaching things a unit test cannot see. `search.test.ts` pins the ordering, the
 * AND across words and the gathering, and every one of those asserts that a result names a section
 * id the index knows, not that the id OPENS anything. Twenty sections, and a wrong one is a dead
 * end that looks exactly like a working search. `test_every_settings_control_can_be_found.py` is
 * file-level by design and cannot see it either.
 *
 * Four of the tests below are about a screen rather than a list, and none of them could be written
 * anywhere else: where the results stand, whether the pane beside them follows, how many clicks it
 * takes to leave, and which of two search boxes Ctrl-F means.
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
	/* The reason a hand-written pane declares `keywords` at all: the control is called Tunnels and
	   the person is typing "vpn". */
	await page.locator(box()).fill('vpn');

	await expect(page.locator(results())).toContainText('Tunnels');
});

test('a section can be found by its own name', async ({ page }) => {
	/* A section is searchable by its own name, not only through the settings on it. Otherwise
	   typing a section's name would find whatever happened to mention it in a sentence. */
	/* Playback: Theater is a group ON Playback, and its old address lands there. */
	await page.locator(box()).fill('playback');

	await expect(page.locator(`${results()} button`).first()).toHaveText(/Playback/);
});

test('the letters that were typed are marked, so a row explains itself', async ({ page }) => {
	/* The same mark the library's search dropdown uses: one component, so "this is why this row
	   is on the list" cannot be two colours on two screens. */
	await page.locator(box()).fill('theat');

	await expect(page.locator(`${results()} b`).first()).toHaveText('Theat');
});

test('a row found only by its sentence says so, and one found by its name does not', async ({
	page
}) => {
	/*
	 * A ROW THAT EXPLAINS NOTHING READS AS THE SEARCH HAVING GUESSED.
	 *
	 * The typed letters are marked in the NAME, so a row found by its name explains itself. A row
	 * found by its help sentence shows that sentence under it (`Searchable.help`), so a match is
	 * explicable.
	 *
	 * Both halves are asserted, because the rule is that it appears ONLY then: shown under every
	 * row, this doubles the height of a 220px column to repeat what the marked name already says.
	 */
	/*
	 * THE ROW IS NAMED AND WAITED FOR, and that is the assertion.
	 *
	 * The list is built from the server's own description of every setting, which arrives over a
	 * request, so a count taken across the whole list can be taken before a row has been drawn. A
	 * count of zero against a list that has not arrived is not a measurement of anything.
	 *
	 * So one row is named, waited for, and asked about itself. Every word of "default layout" is in
	 * `Default layout when Theater opens` and the tier is decided by where ALL of them are (see
	 * `matchedIn`), which makes this a name match with a help sentence sitting right there
	 * unshown.
	 */
	/* Every word typed is in the row's NAME, which is what makes it a name match with a help
	   sentence sitting right there unshown. If the row is renamed, this phrase has to follow it. */
	await page.locator(box()).fill('default layout');
	const named = page
		.locator(`${results()} .under button`)
		.filter({ hasText: 'Default layout when Theater opens' });
	await expect(named, 'the row this is about never arrived').toHaveCount(1);
	await expect(named.locator('.why'), 'a name match was explained anyway').toHaveCount(0);

	/* "differ" is in the sentence under the stash-boxes' More settings ("how much two lengths may
	   differ") and in neither that row's name nor its keywords, so that row is found by its
	   sentence alone. Keywords are deliberately the words somebody types INSTEAD of the sentence,
	   which is why a keyword match is not this case. If the sentence is rewritten, pick another
	   word the same way: in a help sentence, and in neither the name nor the keywords of the row
	   it is in. */
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
	/*
	 * The column is 220px, and two lists in it is a search that has to be scrolled past to reach
	 * the thing it was helping you leave. The results are drawn in the list's place, not by the box
	 * in the row above, so they do not push the pane on the right down.
	 *
	 * So the pane is measured as well, and that is the half an "is the list hidden" check cannot
	 * see.
	 */
	const sections = page.locator('nav[aria-label="Settings sections"]');
	await expect(sections).toBeVisible();

	/*
	 * Measured against the PANEL, and both boxes read in ONE pass.
	 *
	 * The panel rises 12px as it opens. Measured against the WINDOW, a reading taken on the way in
	 * is up to ten pixels out, and the two readings differ by the animation rather than by anything
	 * the search did. Subtracting the panel's own position cancels that, and ONLY IF both are
	 * sampled in the same frame: two `boundingBox()` calls are two round-trips, so the animation
	 * moves between them and the subtraction cancels nothing.
	 */
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
	/* Without it the right-hand column keeps whatever was open when somebody started typing, so
	   the screen is a list of answers beside a pane about something else, which looks blank on
	   a fresh panel, because it is the default section. */
	await expect(page).toHaveURL(/\/settings\/appearance/);

	/* "theater" finds Theater's rows, which are drawn on Playback, so the pane that follows is
	   Playback, and the address is Playback's, not the retired one. */
	await page.locator(box()).fill('theater');

	await expect(page).toHaveURL(/\/settings\/playback/);
	await expect(page.getByRole('heading', { name: 'Playback' }).first()).toBeVisible();
});

test('a result opens the section it names, by its ID and not by its name', async ({ page }) => {
	/*
	 * "Description models" is registered under the section NAME "Smart Search", and that section's
	 * address is `/settings/semantic`. A result carrying the name would link to `/settings/Smart Search`
	 * and open nothing, which looks exactly like a working search until somebody presses one.
	 *
	 * Chosen for that reason. Half the sections in Settings are named the same as their id, so a
	 * result from one of those cannot tell the two apart: a mutation that swaps the id for the
	 * name would survive a test that searched "invite" and opened Users.
	 *
	 * The row UNDER the heading, which is the setting rather than its section: that is the path that
	 * carries a key, and the one the heading's own press does not exercise.
	 */
	await page.locator(box()).fill('description models');
	const setting = page.locator(`${results()} .under button`).first();
	await expect(setting).toBeVisible();
	await setting.click();

	await expect(page).toHaveURL(/\/settings\/semantic/);
	// ...and the pane behind the address is really that pane, which is the half an address cannot
	// prove on its own.
	await expect(page.getByRole('heading', { name: 'Smart Search' }).first()).toBeVisible();
});

test('opening a result leaves ONE thing to close, not two', async ({ page }) => {
	/*
	 * One dismissal, and Settings is gone. Opening a result must replace the history entry rather
	 * than push one, or clicking outside the panel goes back to the panel it was already showing.
	 *
	 * Measured through the address rather than by counting clicks, because that is the fault.
	 */
	await page.locator(box()).fill('description models');
	await page.locator(`${results()} .under button`).first().click();
	await expect(page).toHaveURL(/\/settings\/semantic/);

	/* Clicked BESIDE the panel, which is the gesture this is about, and by position, because
	   the veil and the close button in the corner share the name "Close settings" and the veil is
	   the one behind everything. */
	await page.mouse.click(20, 500);

	await expect(page).not.toHaveURL(/\/settings\//);
});

test('Ctrl-F means THIS box while Settings is open, not the library sheet', async ({ page }) => {
	/* Ctrl-F is the library's search everywhere else in Sift, and while Settings is up that is the
	   wrong box: it opens a sheet OVER the panel to search files. */
	await page.locator('.pane').click({ position: { x: 10, y: 10 } });
	await page.keyboard.press('Control+f');

	await expect(page.locator(box())).toBeFocused();
	await expect(page.getByRole('dialog', { name: /search/i })).toHaveCount(0);
});

test('the arrows walk the results and Enter opens the one they are on', async ({ page }) => {
	/* The same keys the library's own search dropdown answers. The rows are a flat sequence (a
	   section, then what was found on it, then the next section), so "down" means down across the
	   boundary between two groups rather than something different there. */
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

	// And the input announces which row, or a screen reader hears nothing move.
	await expect(page.locator(box())).toHaveAttribute('aria-activedescendant', /.+/);

	await page.locator(box()).press('Enter');
	await expect(page.locator(results())).toHaveCount(0);
});

test('opening a result puts the section list back', async ({ page }) => {
	/* The results are an alternative TO the list. Left up after somebody has gone where they were
	   going, coming back to Settings shows yesterday's search over the navigation. */
	await page.locator(box()).fill('theater');
	await page.locator(`${results()} .item`).first().click();

	await expect(page.locator(box())).toHaveValue('');
	await expect(page.locator('nav[aria-label="Settings sections"]')).toBeVisible();
});

test('the cross is the app cross, not the browser one', async ({ page }) => {
	/* `type=search` draws one of its own in the operating system's look. `app.css` switches that off
	   for every search field in Sift and `.field-clear` is what they all wear instead: the same
	   class the library's own box uses, so the two cannot drift into two crosses. */
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
	// And Settings is still open: the press emptied the box rather than closing the screen it was
	// helping somebody search.
	await expect(page).toHaveURL(/\/settings\//);
});

test('a result for a hand-built control points at the block it names', async ({ page }) => {
	/*
	 * A result opens the section it is on and then rings the row it names.
	 *
	 * A registry-drawn row carries its key as an id by construction; a control on a hand-built pane
	 * has to carry one too, or the result takes somebody to the right pane, at the top, with
	 * nothing pointed at, and `revealSetting` gives up quietly by design, so it looks identical
	 * to a result that never promised anything.
	 *
	 * Static checking cannot finish this. `test_every_settings_result_points_at_something.py`
	 * proves every key names an id something draws; what it cannot prove is that the id is on a
	 * sensible element, or that the mark is visible when you arrive. That is what this is for.
	 */
	await page.locator(box()).fill('cookies');
	await page.locator(`${results()} .under button`).first().click();

	const block = page.locator('#sites\\.cookies');
	await expect(block).toBeVisible();
	/* The ring is an attribute the reveal puts on for a couple of seconds, and it is what says the
	   right element was found rather than merely that the pane opened. */
	await expect(block).toHaveAttribute('data-sift-found', '', { timeout: 5000 });
});
