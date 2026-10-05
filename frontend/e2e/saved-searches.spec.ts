import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

/*
 * Saving a set of filters under a name, and getting it back.
 *
 * Against the real server, because the point is the round trip: a saved search is written, listed,
 * applied and deleted through the same endpoints a person uses, and it is per-account, which is
 * only true if the server says so. The SQL that scopes it has its own tests; this is the feature
 * from the outside. The bar across the top carries a bookmark that keeps what is on screen, and a
 * second one that opens the list of what was kept.
 */

// These share the suite's one admin account and all mutate its saved searches, so they run one at a
// time rather than in parallel. Otherwise one test's clean-up wipes the rows another just created.
test.describe.configure({ mode: 'serial' });

/** The screen, narrowed. There is nothing to keep until something is. */
async function narrowedTo(page: Page, query: string) {
	await page.goto(`/browse?q=${encodeURIComponent(query)}`);
	await expect(page.getByRole('button', { name: 'Add to saved filters' })).toBeVisible();
}

/** Keep what is on screen under a name, through the bar's own controls.
 *
 * Waits for the write to land, not for the dialog to close. Saving under a name that already exists
 * REPLACES the row, so the list is one row long before and after, and a count that was already
 * what it is going to be cannot tell you the save has happened. A test that reads the list too early
 * then follows a row still carrying the previous query, which looks exactly like a saved
 * search that did not save.
 */
async function keepAs(page: Page, called: string) {
	await page.getByRole('button', { name: 'Add to saved filters' }).click();
	await page.getByRole('dialog').getByLabel('Name').fill(called);
	/*
	 * THE WRITE **AND THEN** THE RE-READ, and it takes both.
	 *
	 * Saving posts and then reloads the list, and the panel draws that list. Waiting only for the
	 * POST resolves before the client has handled it, so a test that follows a row straight
	 * afterwards follows the query the row was carrying BEFORE, which is what a saved search that
	 * did not save looks like.
	 *
	 * Waiting only for the GET is worse. A GET can already be in flight when the wait is registered
	 * (the live feed rings this list's bell, and the panel asks for it on open), so the wait
	 * latches onto a response that ARRIVES BEFORE THE POST DOES and returns while the save is still
	 * going; a `page.reload()` on the next line then cancels the in-flight POST and the row never
	 * changes.
	 *
	 * The POST first pins the write to THIS save; the GET after it pins the store to having taken
	 * it.
	 */
	const written = page.waitForResponse(
		(answer) => answer.url().endsWith('/api/search/saved') && answer.request().method() === 'POST'
	);
	await page.getByRole('button', { name: 'Save', exact: true }).click();
	await written;
	await page.waitForResponse(
		(answer) => answer.url().endsWith('/api/search/saved') && answer.request().method() === 'GET'
	);
}

/**
 * The list of what has been kept, open.
 *
 * The kept filters are drawn at the FOOT of the filter panel. See `SavedFilters` for why:
 * reaching for a narrowing you kept is part of deciding how to narrow, not a separate errand. So
 * this opens Filter and looks inside it.
 *
 * A `region` rather than a `group`: it is a labelled `<section>`, which is what a bordered band
 * with a heading is. Pressed rather than hovered: a press pins the panel open and a hover-opened
 * one falls shut the moment the pointer goes anywhere else, which is everywhere this helper is
 * used.
 */
async function keptList(page: Page) {
	const list = page.getByRole('region', { name: 'Saved filters' });
	if ((await list.count()) === 0) {
		/* The bookmark's sheet first, or its veil swallows the press and the panel never opens,
		   which reads as the kept filters having vanished rather than as a click landing on a
		   dialog that was still going away. */
		await expect(page.getByRole('dialog')).toHaveCount(0);
		/* And the trigger has to be LIVE, not merely present. A press landing before the screen has
		   published what it can narrow by finds a dimmed button and does nothing at all, and a
		   fresh `goto` is exactly that moment. Playwright waits for enabled on a click, but only
		   once the element exists with the right state; asserting it first is what makes the wait
		   about the app being ready rather than about a race. */
		const filter = page.getByRole('button', { name: 'Filter', exact: true });
		await expect(filter).toBeEnabled();
		/*
		 * `aria-expanded` is the app's own answer to "is this panel showing", and asking it is what
		 * stops this helper CLOSING the panel it came to open.
		 *
		 * The trigger toggles. Following a kept filter narrows the screen and leaves the panel up
		 * (it is a place you carry on working, not a place you leave), so on the next call the
		 * panel is already open and the drawer is briefly mid-animation. A helper that pressed on
		 * "the region is not visible yet" would shut it.
		 */
		if ((await filter.getAttribute('aria-expanded')) !== 'true') await filter.click();
	}
	await expect(list).toBeVisible();
	return list;
}

/**
 * The three-dot menu on one kept filter, opened. Rename and Delete are menu rows on one control per
 * filter, so no accessible name is a prefix of two others.
 */
async function menuFor(page: Page, name: string) {
	const list = await keptList(page);
	await list.getByRole('button', { name: `More for ${name}` }).click();
	return page.getByRole('menu');
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
	// Empty this account's saved searches so the run starts clean, whatever a previous run left.
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

	/* Exact, every time one of these is named: the row's rename and delete controls carry the search's
	   name too, so a substring match is three buttons. */
	const kept = await keptList(page);
	const row = kept.getByRole('button', { name: 'Beach clips', exact: true });
	await expect(row).toBeVisible();

	/*
	 * Applying it runs the search: the address carries the filter it was kept with, SPELLED AS A
	 * NAMED PARAMETER, which is not how it was typed.
	 *
	 * The screen was narrowed by typing `tags:beach` into the box, so the address carried it inside
	 * `q`. The store moves every filter it can out of `q` and into a named parameter, on the way in
	 * and on the way out (`asNamedFilters`), because a filter whose content lives in `q` puts chips
	 * back in the SEARCH BOX when it is applied and nothing can show what it holds. The two
	 * spellings are the same query (the server compiles `?tags=beach` and `tags:beach` through
	 * one node builder), so this is a change of spelling and not of meaning.
	 *
	 * Asserted in the normalised form deliberately: this is the only place anything drives that
	 * rewrite end to end against a real server, and it edits stored data as a side effect of
	 * reading it.
	 */
	await page.goto('/browse');
	await (await keptList(page)).getByRole('button', { name: 'Beach clips', exact: true }).click();
	await expect(page).toHaveURL(/[?&]tags=beach/);
	await expect(page, 'the filter was left inside the search box').not.toHaveURL(/[?&]q=/);

	// Reopen and delete it, through the row's own menu.
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

	/*
	 * Reloaded before following it, which is what makes this deterministic rather than nearly so.
	 *
	 * Re-saving under a name that already exists REPLACES the row, so the panel looks identical
	 * before and after and there is nothing on screen to wait for. Waiting for the server's answer is
	 * not enough either: the response arriving and the store having taken it are two moments, and on
	 * a loaded machine the row can still be carrying the query it had a moment ago. A reload reads the
	 * list from the server, which is the copy this test is about.
	 */
	await page.reload();

	// One row, and following it lands on the newer query.
	const kept = await keptList(page);
	await expect(kept.getByRole('button', { name: 'My search', exact: true })).toHaveCount(1);
	await kept.getByRole('button', { name: 'My search', exact: true }).click();
	// Named, not typed. See the first test for why the spelling moves.
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

/*
 * The tick and the cross inside the rename box sit on the field's own middle.
 *
 * The buttons can share the field's centre exactly while the GLYPHS sit several pixels above it, so
 * a test that measured the buttons would pass on a defect anybody could see: the same trap
 * `page-alignment.spec.ts` records for the chip in a tile corner, where the line box was right and
 * the ink was not.
 *
 * The cause is a line box reserving room under the baseline for a descender a lone icon does not
 * have; `app.css` collapses that strut for every `Pressable` whose only child is an icon.
 *
 * A pixel of tolerance, against a defect of nearly three.
 */
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
	// Both of them (the cross and the tick), or a rule that reached one is reported as a pass.
	expect(off!.marks).toBe(2);
	for (const drift of off!.drift) expect(drift).toBeLessThanOrEqual(1);
});

test('there is nothing to keep on a screen nobody has narrowed', async ({ page }) => {
	/* The refusal is the absence of the control rather than an error after the fact. A bookmark on
	 * an unnarrowed screen would keep "everything", which is what the screen already shows. */
	await page.goto('/browse');

	await expect(page.getByRole('button', { name: 'Add to saved filters' })).toHaveCount(0);
});

test('what one account keeps is its own', async ({ page }) => {
	// Kept by the signed-in admin, and gone once its owner deletes it. The scoping is the server's,
	// and the client-visible guarantee is that the list is this account's list.
	await narrowedTo(page, 'tags:beach');
	await keepAs(page, 'Admin only');
	const kept = await keptList(page);
	await expect(kept.getByRole('button', { name: 'Admin only', exact: true })).toBeVisible();

	await (await menuFor(page, 'Admin only')).getByRole('menuitem', { name: 'Delete' }).click();
	await expect(kept.getByRole('button', { name: 'Admin only', exact: true })).toHaveCount(0);
});

/*
 * --- EDITING ONE, WHICH DOES NOT TOUCH THE SCREEN. ---
 *
 * `savedSearches.editing` carries a DRAFT: the panel reads and writes the draft while an edit is
 * open and the address otherwise, and no part of an edit navigates.
 *
 * The guarantee is the negative one, which is why every check below is about what did NOT happen:
 * the address, and the chips describing it, are exactly where they were through opening an edit,
 * changing it, and both ways of leaving it.
 */

/**
 * WHAT IS NARROWING THE SCREEN, as a comparable string, and deliberately not the whole address.
 *
 * A wall writes its own paging cursor into the address once its first page lands
 * (`rememberAnchor`), so `?media=video` becomes `?media=video&from=01M1...&near=0` a moment later
 * without anybody doing anything. Comparing whole URLs would race that write.
 *
 * The cursor (the row it starts at and how far down the list that row was) is a POSITION IN A
 * PAGE, not part of what the screen is narrowed by; `partsOf` drops it for the same reason. So it
 * goes, and what is compared is the question being asked.
 */
function narrowing(page: Page): string {
	const params = new URL(page.url()).searchParams;
	params.delete('from');
	params.delete('near');
	params.delete('offset');
	params.sort();
	return params.toString();
}

/** Open the editor for one kept filter, and hand back the region it draws. */
async function editorFor(page: Page, name: string) {
	await (await menuFor(page, name)).getByRole('menuitem', { name: 'Edit' }).click();
	const editing = page.getByRole('group', { name: 'Editing a saved filter' });
	await expect(editing).toBeVisible();
	return editing;
}

/**
 * What the chips row says the SCREEN is narrowed by. Never the edit's own chips.
 *
 * `how many` is required rather than optional, and it is the difference between this asserting
 * something and asserting nothing. The row is drawn by the bar, which draws nothing until the
 * screen has PUBLISHED what it can be narrowed by, so a read taken straight after a `goto` finds
 * an empty list, and an empty list satisfies exactly the checks below that are looking for a row
 * that did not change. Waiting for the count first makes "unchanged" mean the row was there.
 *
 * Anything outside plain ASCII goes first: a chip's text carries the cross's own ligature.
 */
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

	/* A screen narrowed by something ELSE, chosen by hand: the filters on the bar are the
	   person's, and opening an editor must not clear them. */
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
	/* And the editor is showing the FILTER, which is the other half: two different sets of chips on
	   one screen, each saying what it is for. */
	await expect(editing.locator('.chip')).toHaveCount(1);
	await expect(editing.locator('.chip')).toContainText('beach');

	/* Changing the draft writes the draft. Nothing about the screen may move.
	   An emptied edit draws a quiet "Everything" chip rather than nothing, so what says the drop
	   landed is the WORD: a count would still be one, and would be one either way. */
	/* THE CHIP FIRST, THEN ITS CROSS. The cross is `opacity: 0`, sunk below its own line and
	   taking no pointer until the pointer is on the CHIP, so that a row of chips is not a row of
	   crosses. Aimed at from rest, the point Playwright computes is the sunk box and the hover
	   never reaches the chip, so the two wait on each other until the test's own deadline. */
	const draftChip = editing.locator('.chip').first();
	await draftChip.hover();
	await draftChip.locator('.remove').click();
	await expect(editing.locator('.chip')).toHaveText('Everything');
	await expect(editing.locator('.chip .remove')).toHaveCount(0);
	expect(narrowing(page), 'changing the draft changed the screen').toBe(narrowedBy);
	expect(await screenChips(page, 1)).toEqual(['media: Videos']);

	// And leaving without saving leaves both the screen and the stored filter alone.
	await editing.getByRole('button', { name: 'Cancel' }).click();
	await expect(editing).toHaveCount(0);
	expect(narrowing(page), 'cancelling changed the screen').toBe(narrowedBy);
	expect(await screenChips(page, 1)).toEqual(['media: Videos']);

	await page.reload();
	await (await keptList(page)).getByRole('button', { name: 'Runway clips', exact: true }).click();
	await expect(page, 'Cancel wrote the draft anyway').toHaveURL(/[?&]tags=beach/);
});

test('Save keeps the draft under the name, and still does not move the screen', async ({
	page
}) => {
	/* SAVE, PRESSED. It writes to the library, and the unit tests cover the store and cover neither
	   end of the round trip, so this is the one place it is pressed. */
	await page.goto('/browse?tags=beach&media=video');
	await keepAs(page, 'Two things');

	await page.goto('/browse?people=esmewrenfield');
	const narrowedBy = narrowing(page);

	const editing = await editorFor(page, 'Two things');
	await expect(editing.locator('.chip')).toHaveCount(2);

	// Drop one of the two, then keep what is left under the same name.
	/* THE CHIP FIRST, THEN ITS CROSS. The cross is `opacity: 0`, sunk below its own line and
	   taking no pointer until the pointer is on the CHIP, so that a row of chips is not a row of
	   crosses. Aimed at from rest, the point Playwright computes is the sunk box and the hover
	   never reaches the chip, so the two wait on each other until the test's own deadline. */
	const draftChip = editing.locator('.chip').first();
	await draftChip.hover();
	await draftChip.locator('.remove').click();
	await expect(editing.locator('.chip')).toHaveCount(1);
	const kept = page.waitForResponse(
		(answer) => answer.url().endsWith('/api/search/saved') && answer.request().method() === 'GET'
	);
	await editing.getByRole('button', { name: 'Save', exact: true }).click();
	await kept;

	expect(narrowing(page), 'saving an edit changed the screen').toBe(narrowedBy);
	expect(await screenChips(page, 1)).toEqual(['people: esmewrenfield']);

	// The stored filter is the draft: one dimension, not two.
	await page.reload();
	await (await keptList(page)).getByRole('button', { name: 'Two things', exact: true }).click();
	await expect(page).toHaveURL(/[?&]media=video/);
	await expect(page, 'Save kept the filter it started from').not.toHaveURL(/[?&]tags=beach/);
});

test('"Update from current filters" keeps what is on screen under an existing name', async ({
	page
}) => {
	/* The write that reads the SCREEN rather than a draft, which is why it is a separate verb
	   and not the same act as Save. */
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

/*
 * --- THE PANEL FALLS SHUT WHEN YOU POINT AWAY, UNLESS SOMETHING IN IT IS HALF DONE. ---
 *
 * `data-unfinished` has one user, the rename form: a half-typed name is genuinely destroyed by a
 * close, and `#someoneIsTyping` only holds while the caret is still in the box.
 *
 * The unit tests hand `screen-bar` a `<div data-unfinished>` of their own, so they prove the store
 * reads the attribute and prove nothing about the rename form carrying it;
 * `SavedFilters.svelte.test.ts` cannot, because the form is opened from a portalled menu jsdom will
 * not drive. So this performs it.
 *
 * It also needs a panel opened BY HOVER. One opened by a press is pinned, and `leaving()` returns
 * at its first line, so a version of this that pressed the trigger would pass whether the
 * attribute were there or not.
 */

/** The dwell before a pointed-at trigger opens, plus room. See `dwell()` in `ScreenMenus`. */
const OPENS_AFTER_MS = 400;
/** The grace before a left panel shuts, plus room. See `leaving()` in `screen-bar`. */
const SHUTS_AFTER_MS = 600;

/** Open the Filter panel the way a pointer does, so it is not pinned. */
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

	/* THE KNOWN POSITIVE, and it is the whole of this test's honesty: without it, a panel that never
	   shuts for any reason satisfies the check below and this file reports a guard that is doing
	   nothing. It is also the other half of the rule: Filter disappears once the pointer
	   leaves it. */
	await hoverOpen(page);
	await page.mouse.move(0, 0);
	await page.waitForTimeout(SHUTS_AFTER_MS);
	await expect(list, 'a hover-opened panel did not fall shut when the pointer left').toHaveCount(0);

	/*
	 * Now with a rename half typed in it, AND THE CARET OUT OF THE BOX, which is the whole of
	 * what this test is for.
	 *
	 * With the caret still in the field, `#someoneIsTyping` holds the panel on its own, and the
	 * test would pass with `data-unfinished` deleted from the form and with the store's reading of
	 * it stubbed out. The two guards answer different moments: one holds while somebody is typing,
	 * the other holds once they have stopped and the words are still there unsaved.
	 *
	 * Tab moves to Cancel, which is a button inside the panel, and not something
	 * `#someoneIsTyping` counts. So from here only the attribute is holding it.
	 */
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
	/*
	 * A SEQUENCE ONLY A REAL BROWSER CAN PERFORM.
	 *
	 * Editing a kept filter leads the panel's columns with the dimensions that filter names, so the
	 * thing being edited is the thing on screen. Those columns were chosen by the APP, for an edit
	 * that is about to end, so the note that remembers the arrangement is suppressed while such a
	 * leading arrangement is in force. Without that, opening one editor once would silently
	 * overwrite columns somebody had set by hand.
	 *
	 * A unit test covers the suppression at the store. It cannot cover this: the column chooser is
	 * a `bits-ui` Select, and a Select cannot be driven in jsdom: opening it needs a synthetic
	 * keydown and choosing an item does not fire `onValueChange` through `.click()`. So the only
	 * way to set a column BY HAND is a real browser.
	 */
	await page.goto('/browse?tags=beach');
	await keepAs(page, 'Leads with people');
	/* A filter over a dimension that is NOT in the opening five, so leading with it is a visible
	   change rather than a coincidence. */
	await page.goto('/browse?people=esmewrenfield');
	await (
		await menuFor(page, 'Leads with people')
	)
		.getByRole('menuitem', { name: 'Update from current filters' })
		.click();

	/* PER NOUN: the columns of a People wall and a Files wall share not one key, so each noun
	   keeps its own note. The flat `sift.filters.columns` key is migrated into the file wall's
	   and cleared on first use, so reading it flat gets null. */
	const arrangement = () => page.evaluate(() => localStorage.getItem('sift.filters.columns.asset'));
	/* The chooser's trigger carries its caret as an icon LIGATURE inside its own text, and a
	   ligature is a printable character that `trim` does not touch, so "People" reads as "People "
	   and no comparison against a plain word can ever match. Everything outside ASCII goes first. */
	const headings = () =>
		page
			.locator('.column')
			.evaluateAll((columns) =>
				columns.map((one) =>
					(one.querySelector('button')?.textContent ?? '').replace(/[^\x20-\x7E]/g, '').trim()
				)
			);

	// Set a column by hand, through the chooser, and let the note be written.
	await keptList(page);
	const chooser = page.getByLabel('What this column shows').first();
	await chooser.click();
	await page.getByRole('option', { name: 'Rating', exact: true }).click();
	await expect.poll(arrangement).toContain('rating');
	const byHand = await arrangement();
	expect(byHand, 'nothing was written down, so this test cannot see it being kept').toBeTruthy();

	/* Open the editor. The columns LEAD with what the filter names, asked as a POSITION, not
	   as membership. People is third in the natural order and is therefore in the opening five
	   anyway, so `toContain('People')` passes whether the ordering works or not. What only that
	   ordering can produce is People FIRST, in front of the column that was just set by hand. */
	const editing = await editorFor(page, 'Leads with people');
	await expect.poll(async () => (await headings())[0]).toBe('People');
	expect(
		await arrangement(),
		'the app-chosen columns were written down as the arrangement somebody set'
	).toBe(byHand);

	// Leave, shut the panel, and come back to it.
	await editing.getByRole('button', { name: 'Cancel' }).click();
	await page.getByRole('button', { name: 'Filter', exact: true }).click();
	await expect(page.getByRole('region', { name: 'Saved filters' })).toHaveCount(0);
	await keptList(page);

	expect(await arrangement(), 'the hand-set arrangement did not survive an edit').toBe(byHand);
	// And the hand-set column is back at the front, where the editor had put People.
	await expect.poll(async () => (await headings())[0]).toBe('Rating');
});
