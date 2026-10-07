import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';
import {
	removeCollections,
	removePhotos,
	seedCollection,
	seedPhotos,
	type SeededFolder
} from './seed';

/*
 * A file's History pane: oldest first, and a line the moment something is done to the file.
 *
 * The journey: open a real file, open History, put the file in a collection through "Add to", and
 * the pane gains the line at the BOTTOM without being reopened, then a second, which lands under
 * the first. Two, because with one new line "at the bottom" and "at the top" can both be true of a
 * short pane.
 *
 * A real file (`seedPhotos`), because a history is the server's record of what happened to one and
 * a mocked one would only prove the client draws what it is handed.
 */

test.describe.configure({ mode: 'serial' });

const STAMP = Date.now();
const FIRST = `e2e-hist-${STAMP}-first`;
const SECOND = `e2e-hist-${STAMP}-second`;
let folder: SeededFolder;
let collections: string[] = [];

test.beforeAll(async ({ browser }) => {
	const page = await browser.newPage();
	await signInAsAdmin(page);
	folder = await seedPhotos(page, `hist-${STAMP}`, 1);
	collections = [await seedCollection(page, FIRST), await seedCollection(page, SECOND)];
	await page.close();
});

test.afterAll(async ({ browser }) => {
	const page = await browser.newPage();
	await signInAsAdmin(page);
	await removeCollections(page, collections);
	if (folder) await removePhotos(page, folder);
	await page.close();
});

const rows = (page: Page) => page.locator('.history-row');

/** "Add to", then Collection, then the named row: the file's own menu, as a person reaches it. */
async function addToCollection(page: Page, name: string): Promise<void> {
	await page.getByRole('button', { name: 'Add to', exact: true }).click();
	await page.getByRole('menuitem', { name: 'Collection', exact: true }).hover();
	await expect(page.getByLabel('Filter the collections list')).toBeVisible();
	await page.locator('.pick .list .item').filter({ hasText: name }).first().click();
	/* The pick closes the flyout by itself. NO Escape is pressed on top of that: while the menu
	   animates out it still counts as open, and an Escape sent then lands on the popout and
	   closes the very pane this journey reads. */
	await expect(page.getByRole('menu'), 'the pick did not close the flyout').toHaveCount(0);
	await expect(page.getByRole('dialog')).toBeVisible();
}

test('the History pane reads oldest first and gains a line immediately after Add to', async ({
	page
}) => {
	await signInAsAdmin(page);
	const id = folder.assetIds[0];
	await page.goto('/browse');
	await page.locator('.tile').first().click();
	const sheet = page.getByRole('dialog');
	await expect(sheet).toBeVisible();
	await sheet
		.getByRole('tablist', { name: "What this file's record says" })
		.getByRole('tab', { name: /^History/ })
		.click();
	/* A file that was only scanned may have nothing recorded yet; either answer is a loaded pane. */
	await expect(
		sheet
			.getByRole('tabpanel')
			.locator('.history-row')
			.or(sheet.getByText('Nothing has been recorded'))
			.first()
	).toBeVisible();
	/* By what each line says, never by how many there are: a file read a moment ago is still
	   gaining lines from the passes that follow a scan, and they arrive when they arrive. */
	const line = (name: string) => rows(page).filter({ hasText: name });
	const placeOf = async (name: string) =>
		(await rows(page).allInnerTexts()).findIndex((text) => text.includes(name));

	await addToCollection(page, FIRST);
	await expect(line(FIRST), 'the pane did not gain a line for the first collection').toHaveCount(1);

	await addToCollection(page, SECOND);
	await expect(line(SECOND), 'the pane did not gain a line for the second collection').toHaveCount(
		1
	);
	expect(await placeOf(SECOND), 'the newer line is not below the older one').toBeGreaterThan(
		await placeOf(FIRST)
	);

	/* And the server's own order agrees with what is drawn: oldest first, by the moment. */
	const events = (
		(await (await page.request.get(`/api/assets/${id}/history`)).json()) as {
			items: { at: string | number | null }[];
		}
	).items;
	const moments = events.map((event) => event.at).filter((at) => at !== null);
	expect(moments, 'the history route did not answer oldest first').toEqual(
		[...moments].sort((a, b) => (a! < b! ? -1 : a! > b! ? 1 : 0))
	);
});
