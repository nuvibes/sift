import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/* Songs, in a real browser, against the real server.
 *
 * A song is a thing of the library like a Photo Set: on the Music page, in the sidebar between Tags and Loops,
 * a wall of cards whose cover nobody chose is the music glyph, and a page of its own. What is held
 * here is the round trip a person makes: the sidebar row leads to the wall, Add song makes one on
 * the blank record form and lands on its page, a second one made beside it is merged into it from
 * its card's menu, and a third is deleted from its card's menu. Every write is the server's, so
 * the assertions are about what the server accepted.
 *
 * The names are this file's own, so other files sharing the server cannot meet them.
 */

test.describe.configure({ mode: 'serial' });

const KEPT = 'Quillmoor Anthem';
const TWIN = 'Quillmoor Anthem (radio edit)';
const GONE = 'Quillmoor Interlude';

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
});

test('the sidebar lists Music between Tags and Loops, and it opens the songs', async ({ page }) => {
	/* The row is Music, the page that holds the songs, and its address is `/songs`. */
	await page.goto('/browse');
	// The shell is drawn in the browser, so the rail is read once it has rows.
	await expect(page.locator('nav.rail a.item').first()).toBeVisible();
	const rail = page.getByRole('navigation', { name: 'Main' });
	// The label is the last line of a row; the glyph before it is the icon font's own character.
	const labels = (await rail.getByRole('link').allInnerTexts()).map(
		(one) => one.split('\n').at(-1)?.trim() ?? ''
	);
	const music = labels.indexOf('Music');
	expect(music).toBeGreaterThan(-1);
	expect(labels[music - 1]).toBe('Tags');
	expect(labels[music + 1]).toBe('Loops');
	await rail.getByRole('link', { name: 'Music', exact: true }).click();
	await expect(page).toHaveURL(/\/songs$/);
	await expect(page.getByRole('heading', { name: 'Music', level: 1 })).toBeVisible();
});

test('Add song makes one on the blank record form and lands on its page', async ({ page }) => {
	for (const name of [KEPT, TWIN, GONE]) {
		await page.goto('/songs');
		await page.getByRole('button', { name: 'Add song' }).click();
		await expect(page).toHaveURL(/\/songs\/new$/);
		await page.getByRole('textbox', { name: 'Name' }).fill(name);
		await page.getByRole('button', { name: 'Save' }).first().click();
		await expect(page).toHaveURL(/\/songs\/[0-9A-Z]{26}$/);
		await expect(page.getByRole('heading', { name })).toBeVisible();
	}
});

test("a song's card merges it into another from its own menu", async ({ page }) => {
	await page.goto('/songs');
	const card = page.getByRole('listitem').filter({ hasText: TWIN });
	await card.click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Merge' }).click();
	const sheet = page.getByRole('alertdialog');
	await sheet
		.getByRole('radio')
		.filter({ hasText: KEPT })
		.filter({ hasNotText: 'radio edit' })
		.click();
	await sheet.getByRole('button', { name: `Merge into ${KEPT}` }).click();
	await expect(page.getByRole('listitem').filter({ hasText: TWIN })).toHaveCount(0);
	await expect(page.getByRole('listitem').filter({ hasText: KEPT })).toHaveCount(1);
});

test("a song's card deletes it after asking", async ({ page }) => {
	await page.goto('/songs');
	await page.getByRole('listitem').filter({ hasText: GONE }).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Delete' }).click();
	await page.getByRole('alertdialog').getByRole('button', { name: 'Delete song' }).click();
	await expect(page.getByRole('listitem').filter({ hasText: GONE })).toHaveCount(0);
});

test('a song is shared, reported on and hidden like every other named thing', async ({ page }) => {
	/* The grant and hide tables know a song, so its card offers Share, Visibility and Hide as a
	   tag's does, beside the song's own Rename and Merge. */
	await page.goto('/songs');
	await page.getByRole('listitem').filter({ hasText: KEPT }).click({ button: 'right' });
	// Each row's words are its last line; the glyph before them is the icon font's own character.
	const rows = (await page.getByRole('menuitem').allInnerTexts()).map(
		(one) =>
			one
				.split('\n')
				.filter((line) => line.trim())
				.at(-1)
				?.trim() ?? ''
	);
	for (const offered of ['Rename', 'Merge', 'Share', 'Visibility', 'Hide']) {
		expect(rows).toContain(offered);
	}
});
