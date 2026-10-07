import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';
import {
	namePeopleOn,
	removePeople,
	removePhotos,
	seedPeople,
	seedPhotos,
	type SeededFolder
} from './seed';

/*
 * Picking on a person's Seen with tab: the picture is the pick, and the pick is the filter.
 *
 * The journey: press a card's PICTURE on the Seen with tab and the card washes, the pick is written
 * into the address (`people=<name>`, `entity/picks.ts`), the Files tab's count narrows to the files
 * carrying the pick, and the Files tab opens narrowed with the pick showing; "Clear picks" beside
 * the tabs takes every pick off.
 *
 * Real files and real people: three photos, the subject on all three, one companion on two of them
 * and another on the third. So the Files tab reads 3 before the pick and 2 after it: a count that
 * did not move would be the fault.
 */

test.describe.configure({ mode: 'serial' });

const STAMP = Date.now();
let folder: SeededFolder;
let people: string[] = [];
/** Names are the seeded ones: `<stem>-000` the subject, `-001` on two files, `-002` on one. */
const STEM = `e2e-seen-${STAMP}`;
const COMPANION = `${STEM}-001`;

test.beforeAll(async ({ browser }) => {
	const page = await browser.newPage();
	await signInAsAdmin(page);
	folder = await seedPhotos(page, `seen-${STAMP}`, 3);
	people = await seedPeople(page, STEM, 3);
	const [a, b, c] = folder.assetIds;
	await namePeopleOn(page, [a, b, c], [people[0]]);
	await namePeopleOn(page, [a, b], [people[1]]);
	await namePeopleOn(page, [c], [people[2]]);
	await page.close();
});

test.afterAll(async ({ browser }) => {
	const page = await browser.newPage();
	await signInAsAdmin(page);
	await removePeople(page, people);
	if (folder) await removePhotos(page, folder);
	await page.close();
});

/** The Files tab in the page's tab strip, found by the strip's name. Its words carry the count. */
const filesTab = (page: Page) =>
	page
		.getByRole('navigation', { name: /^What to show for / })
		.getByRole('link', { name: /^Files/ });

test('a picture pressed on Seen with washes, filters the address and the Files count, and clears', async ({
	page
}) => {
	await signInAsAdmin(page);
	await page.goto(`/people/${people[0]}?show=people`);

	const picture = page.getByRole('button', { name: `Filter the files to ${COMPANION}` });
	await expect(picture).toBeVisible();
	await expect(filesTab(page)).toContainText('3');

	await picture.click();

	// Washed: the pressed state, on one card only, the one named.
	await expect(picture).toHaveAttribute('aria-pressed', 'true');
	const pressed = page.locator('.card:has([aria-pressed="true"])');
	await expect(pressed).toHaveCount(1);
	await expect(pressed).toContainText(COMPANION);
	// Written to the address, in place.
	await expect.poll(() => new URL(page.url()).searchParams.getAll('people')).toContain(COMPANION);
	// The Files tab counts only the files carrying the pick.
	await expect(filesTab(page)).toContainText('2');
	await expect(page.getByRole('button', { name: /Clear picks/ })).toBeVisible();

	// Opened, the Files tab is narrowed and shows the pick.
	await filesTab(page).click();
	await expect(page).toHaveURL(new RegExp(`/people/${people[0]}\\?`));
	expect(new URL(page.url()).searchParams.getAll('people')).toContain(COMPANION);
	await expect(page.locator('.tile')).toHaveCount(2);
	await expect(page.getByText(COMPANION, { exact: true }).first()).toBeVisible();

	// And "Clear picks" takes it off: the address, the count, the grid.
	await page.getByRole('button', { name: /Clear picks/ }).click();
	await expect.poll(() => new URL(page.url()).searchParams.getAll('people')).toEqual([]);
	await expect(filesTab(page)).toContainText('3');
	await expect(page.locator('.tile')).toHaveCount(3);
	await expect(page.getByRole('button', { name: /Clear picks/ })).toHaveCount(0);
});
