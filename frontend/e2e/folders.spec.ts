import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/*
 * The folder view, where the faults are SILENT: the ordering's second request, the hover tally
 * asked once, the confirm sentence built from it, the row menu's trigger out of the Tab order,
 * the columns layout, the properties panel and the ground menu.
 */

const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

const ROOTS = [{ id: 'r1', name: 'Media' }];

/* Name order and every other order DISAGREE here, so an order silently not applied shows. */
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
	/* Inside one, because Delete is withheld from a library folder. */
	{
		id: 'f4',
		name: 'Holiday',
		path: '/media/Alpha/Holiday',
		rel_path: 'Alpha/Holiday',
		parent_id: 'f1',
		root_id: 'r1'
	}
];

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

/** Every request the folder view makes, so "asked once" and "not asked" are assertable. */
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
	/* Before the tree route: Playwright takes the LAST match, and both begin with its address. */
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

async function openFolders(page: Page): Promise<Served> {
	const served = await serveLibrary(page);
	await page.goto('/browse');
	await page.getByRole('button', { name: 'Folders' }).click();
	await expect(page.getByRole('button', { name: 'Alpha', exact: true })).toBeVisible();
	return served;
}

/** The folder names in drawn order, matched by accessible name: the raw text carries the icon
 * ligature, and a bare role query answers every name twice. */
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
	/* The tree carries no counts, so the size and time orders make a request of their own, and
	   name order must not. */
	const served = await openFolders(page);

	expect(await order(page)).toEqual(['Alpha', 'Mike', 'Zulu']);
	expect(served.facts, 'name order asked the server how big every folder is').toEqual([]);
});

test('choosing an order by size fetches the facts once and reorders on them', async ({ page }) => {
	const served = await openFolders(page);

	await page.getByRole('button', { name: /Sort by/ }).click();
	/* An option in the app's chooser. "Most files": the same key orders folders by how many files
	   are under each (`COUNTED_INSTEAD` in `sort-state.svelte.ts`). */
	await page.getByRole('option', { name: 'Most files' }).click();

	await expect.poll(() => order(page)).toEqual(['Zulu', 'Mike', 'Alpha']);
	expect(served.facts).toHaveLength(1);
});

test('pointing at a folder asks what is in it ONCE, and asks nothing before that', async ({
	page
}) => {
	/* Asked when a pointer first crosses the row, and never again for the same folder. */
	const served = await openFolders(page);
	expect(served.properties, 'a folder nobody has pointed at was asked about').toEqual([]);

	const alpha = page.getByRole('button', { name: 'Alpha', exact: true });
	await alpha.hover();
	await expect.poll(() => served.properties).toEqual(['f1']);

	// Cached, so the second crossing asks nothing.
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
	/* The confirm names the files ("Delete Holiday and the 1,954 files in it?"), from the tally.
	 * On a folder inside a library one: Delete is withheld from a library folder. */
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
	/* Or the test above passes against a menu offering Delete everywhere. */
	await openFolders(page);

	await page.getByRole('button', { name: 'Alpha', exact: true }).click({ button: 'right' });

	await expect(page.getByRole('menuitem', { name: 'Rename' })).toHaveCount(0);
	await expect(page.getByRole('menuitem', { name: 'Delete' })).toHaveCount(0);
});

test('the row menu trigger cannot be reached by Tab, which is why it has no ring', async ({
	page
}) => {
	/* Its ring is suppressed on the premise that no Tab lands on the wrapper, so the premise is
	   asserted (see `ContextMenu`). */
	await openFolders(page);

	const trigger = page.locator('[data-context-menu-trigger].menu-wrap').first();
	await expect(trigger).toBeVisible();
	await expect(trigger).toHaveAttribute('tabindex', '-1');
});

/* The columns layout, the properties panel and the ground menu need no real library: whether
 * the numbers are true of a disk is the server's to test. */

/** Enough folders for more than one column, named to sort the way they are made. */
const MANY = Array.from({ length: 24 }, (_, at) => ({
	id: `m${at}`,
	name: `Folder ${String(at).padStart(2, '0')}`,
	path: `/media/F${at}`,
	rel_path: `F${at}`,
	parent_id: null,
	root_id: 'r1'
}));

/** How many distinct x positions the rows sit at. */
async function columnsUsed(page: Page): Promise<number> {
	/* `EntityBand` on a search result uses the same class. */
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
	/* Registered last, so it wins. */
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

	/* A list puts every name at the same x. */
	expect(await columnsUsed(page), 'the list view is already in columns').toBe(1);

	await page.getByRole('button', { name: 'Folders in columns' }).click();
	await expect.poll(() => columnsUsed(page), { timeout: 5000 }).toBeGreaterThan(1);

	await page.getByRole('button', { name: 'Folders as a list' }).click();
	await expect.poll(() => columnsUsed(page), { timeout: 5000 }).toBe(1);
});

test('Properties says all six things about a folder, off the one request', async ({ page }) => {
	/* All six: four come from the properties request, so a lost request still draws two. */
	await openFolders(page);

	await page.getByRole('button', { name: 'Alpha', exact: true }).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Properties' }).click();

	/* By a row it must have; its title is the folder's own name. */
	const sheet = page.getByRole('dialog').filter({ hasText: 'Size on disk' });
	await expect(sheet).toBeVisible();

	for (const name of ['Location', 'Size on disk', 'Contains', 'Created', 'Shared', 'Hidden']) {
		await expect(sheet, `${name} is not one of the rows`).toContainText(name);
	}

	/* The fixture's `f1`; no creation time is drawn as words. */
	await expect.poll(() => sheet.innerText()).toContain('/media/f1');
	await expect(sheet).toContainText('1 File, 0 Folders');
	await expect(sheet).toContainText('1,000 bytes');
	await expect(sheet).toContainText('Not known');
});

test('right-clicking the empty ground of the band offers what the folder itself can do', async ({
	page
}) => {
	/* A press where there is no row, inside a folder: at the top there is nothing for Properties to
	 * be about (`FolderGround`). */
	await openFolders(page);
	await page.getByRole('button', { name: 'Alpha', exact: true }).click();
	await expect(page.getByRole('button', { name: 'Holiday', exact: true })).toBeVisible();

	/* Found by asking the page: where the empty space is depends on the window. */
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

/* Naming a folder as somebody is the only correction Sift can learn a MISS from. */
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
	/* Somebody already in the library, so the pick is a pick. */
	await page.route('**/api/people*', (route) => {
		if (route.request().method() !== 'GET') return route.fallback();
		return route.fulfill({ json: { items: [{ id: 'x1', name: 'Wren Halloway' }], total: 1 } });
	});

	/* Person on a folder names the FOLDER, one correction rather than one per file. */
	await page.getByRole('button', { name: 'Alpha', exact: true }).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Add to' }).hover();
	await page.getByRole('menuitem', { name: 'Person', exact: true }).hover();
	await expect(page.getByLabel('Filter the people list')).toBeVisible();
	await page.locator('.pick .list .item').filter({ hasText: 'Wren Halloway' }).first().click();

	await expect.poll(() => sent.length).toBe(1);
	expect(sent[0].id, 'the correction went to the wrong folder').toBe('f1');
	expect(sent[0].body).toEqual({ name: 'Wren Halloway', kind: 'person' });
	/* Seven, from the answer rather than from the rows on screen. */
	await expect(page.getByText(/Added 7 files to\s+Wren Halloway/)).toBeVisible();
});
