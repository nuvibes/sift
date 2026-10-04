import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

/*
 * The folder view, in a browser.
 *
 * The behaviours here are the ones whose faults are SILENT:
 *
 *   - the ordering menu's second request, which is not made at all in name order;
 *   - the hover tally: one request per folder, on the first pointer-move, cached;
 *   - the confirm sentence, built from that tally, with a countless wording when it has not landed;
 *   - the row menu's trigger being unreachable by Tab, which is the whole argument for its ring
 *     being suppressed;
 *   - and, below, the columns layout, the properties panel and the ground menu.
 */

const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

const ROOTS = [{ id: 'r1', name: 'Media' }];

/* Named so that alphabetical order and every other order DISAGREE. `Alpha` is first by name and
   smallest; `Zulu` is last by name and biggest. An order that is silently not applied therefore
   reads as name order, and name order is exactly what this can tell apart. */
const FOLDERS = [
	{
		id: 'f1',
		name: 'Alpha',
		path: '/media/Alpha',
		rel_path: 'Alpha',
		parent_id: null,
		root_id: 'r1'
	},
	{ id: 'f2', name: 'Mike', path: '/media/Mike', rel_path: 'Mike', parent_id: null, root_id: 'r1' },
	{ id: 'f3', name: 'Zulu', path: '/media/Zulu', rel_path: 'Zulu', parent_id: null, root_id: 'r1' },
	/* INSIDE one of them, because Delete is deliberately withheld from a library folder: that folder
	   is not inside anything Sift may write to, and "delete" on one means "stop reading this
	   library", which is a different act with its own confirmation on the Folders screen. */
	{
		id: 'f4',
		name: 'Holiday',
		path: '/media/Alpha/Holiday',
		rel_path: 'Alpha/Holiday',
		parent_id: 'f1',
		root_id: 'r1'
	}
];

/** What the facts request answers: `Zulu` holds the most and `Alpha` the least. */
const FACTS = {
	folders: [
		{ id: 'f1', file_count: 1, newest_at: 1_000, size_bytes: 1_000 },
		{ id: 'f2', file_count: 50, newest_at: 2_000, size_bytes: 50_000 },
		{ id: 'f3', file_count: 900, newest_at: 3_000, size_bytes: 900_000 }
	]
};

const PROPERTIES: Record<string, { file_count: number; folder_count: number }> = {
	f1: { file_count: 1, folder_count: 0 },
	f2: { file_count: 50, folder_count: 2 },
	f3: { file_count: 900, folder_count: 3 },
	f4: { file_count: 1954, folder_count: 7 }
};

/** Every request the folder view makes, recorded, so "asked once" and "not asked at all" are both
 *  assertable, and neither is visible on screen. */
type Served = { facts: string[]; properties: string[] };

async function serveLibrary(page: Page): Promise<Served> {
	const served: Served = { facts: [], properties: [] };
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 })
		})
	);
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);
	await page.route('**/api/library/roots', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ roots: ROOTS })
		})
	);
	/* BEFORE the tree route, because Playwright takes the LAST match: `/library/folders/facts` and
	   `/library/folders/f1/properties` both begin with the tree's address. */
	await page.route('**/api/library/folders/facts**', (route) => {
		served.facts.push(route.request().url());
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify(FACTS)
		});
	});
	await page.route('**/api/library/folders/*/properties', (route) => {
		const id = new URL(route.request().url()).pathname.split('/').at(-2) ?? '';
		served.properties.push(id);
		const said = PROPERTIES[id] ?? { file_count: 0, folder_count: 0 };
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				location: `/media/${id}`,
				created_at: null,
				size_bytes: said.file_count * 1_000,
				...said
			})
		});
	});
	await page.route('**/api/library/folders', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ folders: FOLDERS })
		})
	);
	return served;
}

/** Browse, with the folder view turned on. */
async function openFolders(page: Page): Promise<Served> {
	const served = await serveLibrary(page);
	await page.goto('/browse');
	await page.getByRole('button', { name: 'Folders' }).click();
	await expect(page.getByRole('button', { name: 'Alpha', exact: true })).toBeVisible();
	return served;
}

/** The folder names in the order the band draws them.
 *
 * Read off each ROW rather than from the buttons' text, and neither choice is tidiness. A bare role
 * query answers with every name twice (each row is wrapped in a context-menu trigger, so the
 * wrapper and the button inside it both carry the name), and a row's `innerText` opens with the
 * icon font's ligature on a line of its own, so the raw strings are `"\nAlpha"` and never equal
 * `"Alpha"`. The accessible NAME is clean, which is why the locator below matches on it.
 */
const NAMES = ['Alpha', 'Mike', 'Zulu'];

async function order(page: Page): Promise<string[]> {
	const rows = page.locator('.band li');
	const many = await rows.count();
	const seen: string[] = [];
	for (let at = 0; at < many; at += 1) {
		const words = (await rows.nth(at).innerText()).split(/\s+/);
		const found = NAMES.find((one) => words.includes(one));
		if (found) seen.push(found);
	}
	return seen;
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
});

test('opens in name order and asks nothing extra for it', async ({ page }) => {
	/* The folder tree carries no counts, deliberately: a count that is right needs the whole
	   concealment rule, and that rule belongs in one query rather than three. So the orders about
	   size and time are fed by a request of their own, and name order must not make it: a library of
	   four hundred folders would otherwise walk four hundred subtrees to draw a list nobody has
	   asked to reorder. */
	const served = await openFolders(page);

	expect(await order(page)).toEqual(['Alpha', 'Mike', 'Zulu']);
	expect(served.facts, 'name order asked the server how big every folder is').toEqual([]);
});

test('choosing an order by size fetches the facts once and reorders on them', async ({ page }) => {
	const served = await openFolders(page);

	await page.getByRole('button', { name: /Sort by/ }).click();
	/* An OPTION: the orders are a list in the app's own chooser rather than a strip of cards, so the
	   role is the listbox's. See `top-bar.spec.ts`, which pins the same six words on five screens. */
	/*
	 * "Most files", not "Biggest first". The same KEY (`largest`) orders a wall of FILES by
	 * bytes and a wall of folders by how many files are under each, so `COUNTED_INSTEAD` in
	 * `sort-state.svelte.ts` gives the counting walls their own two labels and this list says what
	 * it is actually sorting by.
	 */
	await page.getByRole('option', { name: 'Most files' }).click();

	// Zulu holds 900 and Alpha holds 1: an order that was not applied would read as name order,
	// which is what this fixture is arranged to tell apart.
	await expect.poll(() => order(page)).toEqual(['Zulu', 'Mike', 'Alpha']);
	expect(served.facts).toHaveLength(1);
});

test('pointing at a folder asks what is in it ONCE, and asks nothing before that', async ({
	page
}) => {
	/* The tally a file manager puts in its status bar. Asked when a pointer first crosses the row,
	   never up front, and never again for the same folder, because the panel and the label are the
	   same request and asking twice is how they come to disagree. */
	const served = await openFolders(page);
	expect(served.properties, 'a folder nobody has pointed at was asked about').toEqual([]);

	const alpha = page.getByRole('button', { name: 'Alpha', exact: true });
	await alpha.hover();
	await expect.poll(() => served.properties).toEqual(['f1']);

	// Away and back: the answer is cached, so the second crossing asks nothing.
	await page.getByRole('button', { name: 'Zulu', exact: true }).hover();
	await expect.poll(() => served.properties).toEqual(['f1', 'f3']);
	await alpha.hover();
	await page.waitForTimeout(250);
	expect(served.properties, 'the tally was fetched again for a folder already known').toEqual([
		'f1',
		'f3'
	]);
});

test('the delete question carries the count, and the folder it is about is inside one', async ({
	page
}) => {
	/*
	 * "Delete Holiday?" answers nothing. "Delete Holiday and the 1,954 files in it?" is a question
	 * somebody can actually answer, which is the whole reason the hover tally is fetched at all.
	 *
	 * Reached by a RIGHT-CLICK, because the row's menu is a context menu rather than a button, and
	 * on a folder INSIDE a library one: Delete is withheld from a library folder, where it would
	 * mean "stop reading this library": a different act, on a different screen.
	 */
	const served = await openFolders(page);
	await page.getByRole('button', { name: 'Alpha', exact: true }).click();

	const holiday = page.getByRole('button', { name: 'Holiday', exact: true });
	await expect(holiday).toBeVisible();
	await holiday.hover();
	await expect.poll(() => served.properties).toContain('f4');

	await holiday.click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Delete' }).click();

	const sheet = page.getByRole('alertdialog');
	await expect(sheet).toContainText('Delete Holiday?');
	await expect(sheet, 'the count the tally fetched is not in the question').toContainText('1,954');
	await expect(sheet).toContainText('no way back');
});

test('a library folder is not offered Delete at all', async ({ page }) => {
	/* The other half, and without it the test above would pass against a menu that offers Delete
	   everywhere. A library folder is not inside anything Sift may write to. */
	await openFolders(page);

	await page.getByRole('button', { name: 'Alpha', exact: true }).click({ button: 'right' });

	await expect(page.getByRole('menuitem', { name: 'Rename' })).toHaveCount(0);
	await expect(page.getByRole('menuitem', { name: 'Delete' })).toHaveCount(0);
});

test('the row menu trigger cannot be reached by Tab, which is why it has no ring', async ({
	page
}) => {
	/* Its focus ring is suppressed, and the argument for suppressing it is that no Tab ever lands
	   there: the trigger is a WRAPPER, and the thing that can be operated is the row inside it,
	   which keeps its own ring. If bits-ui ever makes the wrapper tabbable that argument is void and
	   the ring has to come back, so the premise is what is asserted rather than the rule resting on
	   it. Measured rather than assumed: the note in `ContextMenu` says so in those words. */
	await openFolders(page);

	const trigger = page.locator('[data-context-menu-trigger].menu-wrap').first();
	await expect(trigger).toBeVisible();
	await expect(trigger).toHaveAttribute('tabindex', '-1');
});

/*
 * The columns layout, the properties panel and the ground menu, which need no real library:
 *
 *   - COLUMNS is `columns: 240px` on the same list the other view draws. What it changes is where a
 *     row ENDS UP, which is geometry and needs only enough rows to fill more than one column.
 *   - PROPERTIES reads one request (`/library/folders/{id}/properties`), and this fixture already
 *     answers it, because the hover tally reads the same one.
 *   - THE GROUND MENU is a right-click on the BAND's background, not on the wall of files. The band
 *     is drawn from the folder tree, which this fixture serves.
 *
 * What does want a real library is what none of these assert: whether the numbers are TRUE of a
 * disk. That is the server's, and it has its own tests.
 */

/** Enough folders that more than one column is possible. Named so they sort the way they are made. */
const MANY = Array.from({ length: 24 }, (_, at) => ({
	id: `m${at}`,
	name: `Folder ${String(at).padStart(2, '0')}`,
	path: `/media/F${at}`,
	rel_path: `F${at}`,
	parent_id: null,
	root_id: 'r1'
}));

/** Where each folder row sits across the width, rounded, because a column is an x and not a name. */
async function columnsUsed(page: Page): Promise<number> {
	/* `.first()` because `EntityBand` on a search result uses the same class name: a bare locator
	   would be a strict-mode violation the day a folder view and one of those share a screen. */
	return page
		.locator('.band')
		.first()
		.evaluate((band) => {
			const lefts = [...band.querySelectorAll('li')].map((row) =>
				Math.round(row.getBoundingClientRect().left)
			);
			return new Set(lefts).size;
		});
}

test('the columns view uses the width of the window and the list does not', async ({ page }) => {
	await serveLibrary(page);
	/* Registered AFTER `serveLibrary`, which is what makes it win: Playwright takes the last
	   matching handler, so this replaces the four-folder tree with one long enough to wrap. */
	await page.route('**/api/library/folders', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ folders: MANY })
		})
	);
	await page.setViewportSize({ width: 1600, height: 900 });
	await page.goto('/browse');
	await page.getByRole('button', { name: 'Folders' }).click();
	await expect(page.getByRole('button', { name: 'Folder 00', exact: true })).toBeVisible();

	/* The claim is comparative, so both halves are measured. One window, two views: a list puts
	   every name at the same x, and that is the whole of what "one name per line" means. */
	expect(await columnsUsed(page), 'the list view is already in columns').toBe(1);

	await page.getByRole('button', { name: 'Folders in columns' }).click();
	await expect.poll(() => columnsUsed(page), { timeout: 5000 }).toBeGreaterThan(1);

	await page.getByRole('button', { name: 'Folders as a list' }).click();
	await expect.poll(() => columnsUsed(page), { timeout: 5000 }).toBe(1);
});

test('Properties says all six things about a folder, off the one request', async ({ page }) => {
	/* Six rows, and the reason to name all six rather than spot-check one: four of them come from
	   the properties request and two from the row already in hand, so a panel that lost the request
	   still draws a convincing-looking two-thirds of itself. */
	await openFolders(page);

	await page.getByRole('button', { name: 'Alpha', exact: true }).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Properties' }).click();

	/* Picked out by a row it must have rather than by its title, which is the folder's own name and
	   would make this test's subject its own locator. */
	const sheet = page.getByRole('dialog').filter({ hasText: 'Size on disk' });
	await expect(sheet).toBeVisible();

	for (const name of ['Location', 'Size on disk', 'Contains', 'Created', 'Shared', 'Hidden']) {
		await expect(sheet, `${name} is not one of the rows`).toContainText(name);
	}

	/* The values, from the fixture's own answer for `f1`: one file, no folders inside it, a
	   thousand bytes, and no creation time, which is drawn as words rather than left blank. */
	await expect.poll(() => sheet.innerText()).toContain('/media/f1');
	await expect(sheet).toContainText('1 File, 0 Folders');
	await expect(sheet).toContainText('1,000 bytes');
	await expect(sheet).toContainText('Not known');
});

test('right-clicking the empty ground of the band offers what the folder itself can do', async ({
	page
}) => {
	/*
	 * The gesture a file manager has and a web page usually does not: press where there is no row.
	 *
	 * INSIDE a folder rather than at the top of the tree, and that is the half worth having.
	 * "Your folders" is a place on this screen and not a folder on a disk, so at the top there is
	 * nothing for Properties to be about and the menu deliberately withholds it. See
	 * `FolderGround`. A test taken at the top would pass against a menu that had lost the row.
	 */
	await openFolders(page);
	await page.getByRole('button', { name: 'Alpha', exact: true }).click();
	await expect(page.getByRole('button', { name: 'Holiday', exact: true })).toBeVisible();

	/* A point inside the band that no row is under, found by asking the page rather than by
	   guessing at an offset: the band is as tall as what is in it, so where its empty space is
	   depends on the window and on how many folders the fixture drew. */
	const ground = await page
		.locator('.band-wrap')
		.first()
		.evaluate((wrap) => {
			const box = wrap.getBoundingClientRect();
			const rows = [...wrap.querySelectorAll('li')];
			for (let y = Math.floor(box.bottom) - 3; y > box.top; y -= 3) {
				for (let x = Math.floor(box.right) - 5; x > box.left; x -= 5) {
					const at = document.elementFromPoint(x, y);
					if (at && wrap.contains(at) && !rows.some((row) => row.contains(at))) return { x, y };
				}
			}
			return null;
		});
	expect(ground, 'the band has no empty space to press').not.toBeNull();

	await page.mouse.click(ground?.x ?? 0, ground?.y ?? 0, { button: 'right' });

	await expect(page.getByRole('menuitem', { name: 'New folder' })).toBeVisible();
	await expect(page.getByRole('menuitem', { name: 'Properties' })).toBeVisible();
});

/*
 * WHO A FOLDER IS, said by a person.
 *
 * The only correction Sift can learn a MISS from: yes and "not a person" both answer a question it
 * asked, and neither says who it should have been. So the request this sends is the one thing on
 * the folder view that teaches the library something it could not have worked out.
 */
test('naming a folder as somebody sends the correction for that folder', async ({ page }) => {
	await openFolders(page);

	const sent: { id: string; body: unknown }[] = [];
	await page.route('**/api/suggestions/folder/*', async (route) => {
		sent.push({
			id: new URL(route.request().url()).pathname.split('/').at(-1) ?? '',
			body: route.request().postDataJSON()
		});
		await route.fulfill({ json: { files: 7, person_id: 'x1', name: 'Wren Halloway' } });
	});
	/* The people the list offers. Somebody already in the library, so the pick is a pick rather
	   than a person made on the way; making one from the list has its own tests. */
	await page.route('**/api/people*', (route) => {
		if (route.request().method() !== 'GET') return route.fallback();
		return route.fulfill({ json: { items: [{ id: 'x1', name: 'Wren Halloway' }], total: 1 } });
	});

	/* Named through the folder's own Add to: Person on a folder names the FOLDER (one
	   correction for the folder), rather than filing each file under them one at a time. */
	await page.getByRole('button', { name: 'Alpha', exact: true }).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Add to' }).hover();
	await page.getByRole('menuitem', { name: 'Person', exact: true }).hover();
	await expect(page.getByLabel('Filter the people list')).toBeVisible();
	await page.locator('.pick .list .item').filter({ hasText: 'Wren Halloway' }).first().click();

	await expect.poll(() => sent.length).toBe(1);
	expect(sent[0].id, 'the correction went to the wrong folder').toBe('f1');
	expect(sent[0].body).toEqual({ name: 'Wren Halloway', kind: 'person' });
	/* What it did, said back as the toast every Add to says: seven files, from the answer rather
	   than from the number of rows on screen. */
	await expect(page.getByText(/Added 7 files to\s+Wren Halloway/)).toBeVisible();
});
