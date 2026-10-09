import { type Locator, type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';
import { stillFor } from './settled';

/* Saving a set of filters under a name, against the real server: a saved search is per account
 * only if the server says so. */

// Serial: they share one account, and one test's clean-up would wipe another's rows.
test.describe.configure({ mode: 'serial' });

/** There is nothing to keep until the screen is narrowed. */
async function narrowedTo(page: Page, query: string) {
	await page.goto(`/browse?q=${encodeURIComponent(query)}`);
	await expect(page.getByRole('button', { name: 'Add to saved filters' })).toBeVisible();
}

/** Keep what is on screen under a name, waiting for the write rather than for the dialog. */
async function keepAs(page: Page, called: string) {
	await page.getByRole('button', { name: 'Add to saved filters' }).click();
	await page.getByRole('dialog').getByLabel('Name').fill(called);
	/* The POST pins the wait to THIS save; the GET after it pins the store to having taken it. A
	 * GET alone can latch onto one already in flight and return before the save lands. */
	const written = page.waitForResponse(
		(answer) => answer.url().endsWith('/api/search/saved') && answer.request().method() === 'POST'
	);
	await page.getByRole('button', { name: 'Save', exact: true }).click();
	await written;
	await page.waitForResponse(
		(answer) => answer.url().endsWith('/api/search/saved') && answer.request().method() === 'GET'
	);
}

/** The kept filters, at the foot of the Filter panel, opened by a press so they stay open. */
async function keptList(page: Page) {
	const list = page.getByRole('region', { name: 'Saved filters' });
	if ((await list.count()) === 0) {
		/* The bookmark's sheet first, or its veil swallows the press. */
		await expect(page.getByRole('dialog')).toHaveCount(0);
		/* Enabled first: straight after a `goto` the trigger is dimmed and a press does nothing. */
		const filter = page.getByRole('button', { name: 'Filter', exact: true });
		await expect(filter).toBeEnabled();
		/* The trigger toggles, and the panel may already be open from the last call. */
		if ((await filter.getAttribute('aria-expanded')) !== 'true') await filter.click();
	}
	await expect(list).toBeVisible();
	return list;
}

/** The three-dot menu on one kept filter, opened. */
async function menuFor(page: Page, name: string) {
	const list = await keptList(page);
	await list.getByRole('button', { name: `More for ${name}` }).click();
	return page.getByRole('menu');
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
	await page.request.get('/api/search/saved').then(async (answer) => {
		const csrf = await page.request
			.get('/api/auth/me')
			.then(async (me) => (await me.json()).csrf_token as string);
		for (const item of (await answer.json()).items as { id: string }[]) {
			await page.request.delete(`/api/search/saved/${item.id}`, {
				headers: { 'x-csrf-token': csrf }
			});
		}
	});
});

test('a set of filters can be kept, applied, and deleted', async ({ page }) => {
	await narrowedTo(page, 'tags:beach');
	await keepAs(page, 'Beach clips');

	/* Exact: the row's own controls carry the search's name too. */
	const kept = await keptList(page);
	const row = kept.getByRole('button', { name: 'Beach clips', exact: true });
	await expect(row).toBeVisible();

	/* The address carries the filter as a NAMED PARAMETER, not inside `q` as it was typed: the
	 * store moves it (`asNamedFilters`), and this is the one place that rewrite runs end to end. */
	await page.goto('/browse');
	await (await keptList(page)).getByRole('button', { name: 'Beach clips', exact: true }).click();
	await expect(page).toHaveURL(/[?&]tags=beach/);
	await expect(page, 'the filter was left inside the search box').not.toHaveURL(/[?&]q=/);

	await (await menuFor(page, 'Beach clips')).getByRole('menuitem', { name: 'Delete' }).click();
	await expect(
		page.getByText(
			'No saved filters yet. Select some filters, then press the Add to saved filters icon on the filters bar.'
		)
	).toBeVisible();
});

test('keeping one under a name already used replaces it rather than adding a second', async ({
	page
}) => {
	await narrowedTo(page, 'tags:beach');
	await keepAs(page, 'My search');
	await expect(
		(await keptList(page)).getByRole('button', { name: 'My search', exact: true })
	).toHaveCount(1);

	// Same name, different filter.
	await narrowedTo(page, 'tags:sunset');
	await keepAs(page, 'My search');

	/* Reloaded first: a replaced row looks identical, so the list is read back from the server. */
	await page.reload();

	const kept = await keptList(page);
	await expect(kept.getByRole('button', { name: 'My search', exact: true })).toHaveCount(1);
	await kept.getByRole('button', { name: 'My search', exact: true }).click();
	// Named, not typed: see the first test.
	await expect(page).toHaveURL(/[?&]tags=sunset/);
});

test('a kept search can be renamed in place', async ({ page }) => {
	await narrowedTo(page, 'tags:beach');
	await keepAs(page, 'First name');

	await (await menuFor(page, 'First name')).getByRole('menuitem', { name: 'Rename' }).click();
	const kept = await keptList(page);
	await kept.getByLabel('Rename First name').fill('Second name');
	await kept.getByLabel('Rename First name').press('Enter');

	await expect(kept.getByRole('button', { name: 'Second name', exact: true })).toBeVisible();
	await expect(kept.getByRole('button', { name: 'First name', exact: true })).toHaveCount(0);
});

/* The glyphs, not just the buttons, sit on the field's middle: a line box can reserve room for a
 * descender a lone icon does not have (`app.css` collapses that strut). */
test('the marks inside the rename box sit on the middle of the field', async ({ page }) => {
	await narrowedTo(page, 'tags:beach');
	await keepAs(page, 'Centred name');

	await (await menuFor(page, 'Centred name')).getByRole('menuitem', { name: 'Rename' }).click();
	const kept = await keptList(page);
	await expect(kept.getByLabel('Rename Centred name')).toBeVisible();

	const off = await page.evaluate(() => {
		const box = document.querySelector('.renaming');
		if (!box) return null;
		const field = box.querySelector('input');
		const glyphs = [...box.querySelectorAll('.marks .icon')];
		if (!field || glyphs.length === 0) return null;
		const middle = (one: Element) => {
			const at = one.getBoundingClientRect();
			return (at.top + at.bottom) / 2;
		};
		const wanted = middle(field);
		return { drift: glyphs.map((one) => Math.abs(middle(one) - wanted)), marks: glyphs.length };
	});

	expect(off, 'the rename box was not on screen to measure').not.toBeNull();
	// Both, or a rule that reached one passes.
	expect(off!.marks).toBe(2);
	for (const drift of off!.drift) expect(drift).toBeLessThanOrEqual(1);
});

test('there is nothing to keep on a screen nobody has narrowed', async ({ page }) => {
	/* An unnarrowed screen would keep "everything", so the control is absent. */
	await page.goto('/browse');

	await expect(page.getByRole('button', { name: 'Add to saved filters' })).toHaveCount(0);
});

test('what one account keeps is its own', async ({ page }) => {
	// The scoping is the server's; this checks the list is this account's.
	await narrowedTo(page, 'tags:beach');
	await keepAs(page, 'Admin only');
	const kept = await keptList(page);
	await expect(kept.getByRole('button', { name: 'Admin only', exact: true })).toBeVisible();

	await (await menuFor(page, 'Admin only')).getByRole('menuitem', { name: 'Delete' }).click();
	await expect(kept.getByRole('button', { name: 'Admin only', exact: true })).toHaveCount(0);
});

/* EDITING ONE DOES NOT TOUCH THE SCREEN: an edit works on a draft, so every check below is about
 * what did NOT change. */

/** What is narrowing the screen. A wall writes its paging cursor into the address on its own, so
 * that goes. */
function narrowing(page: Page): string {
	const params = new URL(page.url()).searchParams;
	params.delete('from');
	params.delete('near');
	params.delete('offset');
	params.sort();
	return params.toString();
}

/** Open the editor for one kept filter. */
async function editorFor(page: Page, name: string) {
	await (await menuFor(page, name)).getByRole('menuitem', { name: 'Edit' }).click();
	const editing = page.getByRole('group', { name: 'Editing a saved filter' });
	await expect(editing).toBeVisible();
	return editing;
}

/** The chips describing the SCREEN. The count is waited for first, or an empty row taken before
 * the bar is drawn would pass as unchanged. Non-ASCII goes: the cross is a ligature. */
async function screenChips(page: Page, how_many: number): Promise<string[]> {
	const chips = page.locator('.filters .chip');
	await expect(chips).toHaveCount(how_many);
	return (await chips.allTextContents()).map((one) =>
		one
			.replace(/[^\x20-\x7E]/g, '')
			.replace(/\s+/g, ' ')
			.trim()
	);
}

test('editing a kept filter leaves the screen exactly where it was', async ({ page }) => {
	await narrowedTo(page, 'tags:beach');
	await keepAs(page, 'Runway clips');

	/* Narrowed by something else, which opening an editor must not clear. */
	await page.goto('/browse?media=video');
	const narrowedBy = narrowing(page);
	expect(await screenChips(page, 1)).toEqual(['media: Videos']);

	const editing = await editorFor(page, 'Runway clips');

	expect(narrowing(page), 'opening the editor changed what the screen is narrowed by').toBe(
		narrowedBy
	);
	expect(await screenChips(page, 1), 'the row stopped describing the screen').toEqual([
		'media: Videos'
	]);
	/* And the editor shows the FILTER: two sets of chips, each saying what it is for. */
	await expect(editing.locator('.chip')).toHaveCount(1);
	await expect(editing.locator('.chip')).toContainText('beach');

	/* An emptied edit draws "Everything", so the word, not a count, says the drop landed. */
	await pressCross(editing.locator('.chip').first());
	await expect(editing.locator('.chip')).toHaveText('Everything');
	await expect(editing.locator('.chip .remove')).toHaveCount(0);
	expect(narrowing(page), 'changing the draft changed the screen').toBe(narrowedBy);
	expect(await screenChips(page, 1)).toEqual(['media: Videos']);

	await editing.getByRole('button', { name: 'Cancel' }).click();
	await expect(editing).toHaveCount(0);
	expect(narrowing(page), 'cancelling changed the screen').toBe(narrowedBy);
	expect(await screenChips(page, 1)).toEqual(['media: Videos']);

	await page.reload();
	await (await keptList(page)).getByRole('button', { name: 'Runway clips', exact: true }).click();
	await expect(page, 'Cancel wrote the draft anyway').toHaveURL(/[?&]tags=beach/);
});

/** Press a chip's cross: it takes the pointer only while the chip is hovered, and a sliding sheet
    moves it from under the pointer. */
async function pressCross(chip: Locator): Promise<void> {
	for (let attempt = 0; attempt < 2; attempt += 1) {
		await stillFor(chip);
		await chip.hover();
		try {
			await chip.locator('.remove').click({ timeout: 5_000 });
			return;
		} catch {
			// Moved: once more from a still sheet.
		}
	}
	await stillFor(chip);
	await chip.hover();
	await chip.locator('.remove').click();
}

test('Save keeps the draft under the name, and still does not move the screen', async ({
	page
}) => {
	/* The one place Save is pressed end to end. */
	await page.goto('/browse?tags=beach&media=video');
	await keepAs(page, 'Two things');

	await page.goto('/browse?people=esmewrenfield');
	const narrowedBy = narrowing(page);

	const editing = await editorFor(page, 'Two things');
	await expect(editing.locator('.chip')).toHaveCount(2);

	await pressCross(editing.locator('.chip').first());
	await expect(editing.locator('.chip')).toHaveCount(1);
	const kept = page.waitForResponse(
		(answer) => answer.url().endsWith('/api/search/saved') && answer.request().method() === 'GET'
	);
	await editing.getByRole('button', { name: 'Save', exact: true }).click();
	await kept;

	expect(narrowing(page), 'saving an edit changed the screen').toBe(narrowedBy);
	expect(await screenChips(page, 1)).toEqual(['people: esmewrenfield']);

	await page.reload();
	await (await keptList(page)).getByRole('button', { name: 'Two things', exact: true }).click();
	await expect(page).toHaveURL(/[?&]media=video/);
	await expect(page, 'Save kept the filter it started from').not.toHaveURL(/[?&]tags=beach/);
});

test('"Update from current filters" keeps what is on screen under an existing name', async ({
	page
}) => {
	/* Reads the SCREEN rather than a draft, so it is a separate verb from Save. */
	await narrowedTo(page, 'tags:beach');
	await keepAs(page, 'Moving target');

	await page.goto('/browse?media=video');
	const kept = page.waitForResponse(
		(answer) => answer.url().endsWith('/api/search/saved') && answer.request().method() === 'GET'
	);
	await (
		await menuFor(page, 'Moving target')
	)
		.getByRole('menuitem', { name: 'Update from current filters' })
		.click();
	await kept;

	await page.goto('/browse');
	await (await keptList(page)).getByRole('button', { name: 'Moving target', exact: true }).click();
	await expect(page).toHaveURL(/[?&]media=video/);
	await expect(page).not.toHaveURL(/[?&]tags=beach/);
});

/* A HOVER-OPENED PANEL FALLS SHUT, UNLESS SOMETHING IN IT IS HALF DONE: the rename form carries
 * `data-unfinished`, which only a real browser can open. A pressed panel is pinned. */

/** The dwell before a trigger opens, plus room (`dwell()` in `ScreenMenus`). */
const OPENS_AFTER_MS = 400;
/** The grace before a left panel shuts, plus room (`leaving()` in `screen-bar`). */
const SHUTS_AFTER_MS = 600;

/** Opened by hover, so not pinned. */
async function hoverOpen(page: Page) {
	await page.getByRole('button', { name: 'Filter', exact: true }).hover();
	await page.waitForTimeout(OPENS_AFTER_MS);
	const list = page.getByRole('region', { name: 'Saved filters' });
	await expect(list).toBeVisible();
	return list;
}

test('a hover-opened panel shuts when you point away, and not while a name is half typed', async ({
	page
}) => {
	await narrowedTo(page, 'tags:beach');
	await keepAs(page, 'Half typed');

	const list = page.getByRole('region', { name: 'Saved filters' });

	/* The known positive: without it, a panel that never shuts passes the check below. */
	await hoverOpen(page);
	await page.mouse.move(0, 0);
	await page.waitForTimeout(SHUTS_AFTER_MS);
	await expect(list, 'a hover-opened panel did not fall shut when the pointer left').toHaveCount(0);

	/* With the caret OUT of the box, or `#someoneIsTyping` holds the panel on its own. Tab moves to
	 * Cancel, so only the attribute is holding it. */
	await hoverOpen(page);
	await page.getByRole('button', { name: 'More for Half typed' }).click();
	await page.getByRole('menuitem', { name: 'Rename' }).click();
	const box = page.getByLabel('Rename Half typed');
	await expect(box).toBeVisible();
	await box.fill('Half typed and not fin');
	await box.press('Tab');
	await expect(
		box,
		'the caret is still in the box, so this proves the wrong guard'
	).not.toBeFocused();

	await page.mouse.move(0, 0);
	await page.waitForTimeout(SHUTS_AFTER_MS);

	await expect(list, 'the panel shut on a half-typed name and destroyed it').toBeVisible();
	await expect(box).toHaveValue('Half typed and not fin');
});

test('an edit reorders the columns and does not overwrite the arrangement somebody set', async ({
	page
}) => {
	/* An edit leads the columns with what the filter names, and must not overwrite an arrangement
	 * set by hand. Only a real browser can set one: the chooser is a Select jsdom cannot drive. */
	await page.goto('/browse?tags=beach');
	await keepAs(page, 'Leads with people');
	/* A dimension NOT in the opening five, so leading with it is visible. */
	await page.goto('/browse?people=esmewrenfield');
	await (
		await menuFor(page, 'Leads with people')
	)
		.getByRole('menuitem', { name: 'Update from current filters' })
		.click();

	/* Per noun: the flat key is migrated into the file wall's. */
	const arrangement = () => page.evaluate(() => localStorage.getItem('sift.filters.columns.asset'));
	/* The caret is a ligature inside the text, so non-ASCII goes first. */
	const headings = () =>
		page
			.locator('.column')
			.evaluateAll((columns) =>
				columns.map((one) =>
					(one.querySelector('button')?.textContent ?? '').replace(/[^\x20-\x7E]/g, '').trim()
				)
			);

	await keptList(page);
	const chooser = page.getByLabel('What this column shows').first();
	await chooser.click();
	await page.getByRole('option', { name: 'Rating', exact: true }).click();
	await expect.poll(arrangement).toContain('rating');
	const byHand = await arrangement();
	expect(byHand, 'nothing was written down, so this test cannot see it being kept').toBeTruthy();

	/* People FIRST, as a position: it is in the opening five anyway, so membership is no proof. */
	const editing = await editorFor(page, 'Leads with people');
	await expect.poll(async () => (await headings())[0]).toBe('People');
	expect(
		await arrangement(),
		'the app-chosen columns were written down as the arrangement somebody set'
	).toBe(byHand);

	await editing.getByRole('button', { name: 'Cancel' }).click();
	await page.getByRole('button', { name: 'Filter', exact: true }).click();
	await expect(page.getByRole('region', { name: 'Saved filters' })).toHaveCount(0);
	await keptList(page);

	expect(await arrangement(), 'the hand-set arrangement did not survive an edit').toBe(byHand);
	// Back at the front, where the editor had put People.
	await expect.poll(async () => (await headings())[0]).toBe('Rating');
});
