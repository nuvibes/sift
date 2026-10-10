import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';
import { csrfOf } from './seed';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

/* One chooser for a place on the server, in a browser: Add a folder's and Migrate from Stash's.
 * Its top level is every folder Sift has, the rest of the device is one press away, and the Stash
 * chooser ends on a database FILE, never a folder. */

test.describe.configure({ mode: 'serial' });

/** Beside the media area, so a library folder here is no grant: added by its typed path. */
const TYPED = fileURLToPath(new URL('./.chooser-typed', import.meta.url));
const STASH = fileURLToPath(new URL('./.chooser-stash', import.meta.url));

async function write(page: Page, path: string, data: object) {
	const answer = await page.request.post(path, {
		data,
		headers: { 'x-csrf-token': await csrfOf(page) }
	});
	return answer;
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
});

test('Add a folder lists every folder Sift has, and the rest of the device a press away', async ({
	page
}) => {
	mkdirSync(join(TYPED, 'inside'), { recursive: true });
	const added = await write(page, '/api/library/roots', { abs_path: TYPED, scan: false });
	expect(added.ok(), await added.text()).toBeTruthy();
	const rootId = String((await added.json()).id);
	try {
		await page.goto('/settings/library');
		await page.getByRole('button', { name: 'Add a folder' }).click();
		const dialog = page.getByRole('dialog', { name: 'Add a folder' });

		await expect(dialog.getByText('Folders Sift already has')).toBeVisible();
		// Added by its path, never through a folder dialog: still one of the folders Sift has.
		await dialog.getByRole('button', { name: '.chooser-typed', exact: true }).click();
		await expect(dialog.getByRole('button', { name: 'inside', exact: true })).toBeVisible();

		await dialog.getByRole('button', { name: 'Browse this device' }).click();
		await expect(dialog.getByText('Drives where Sift runs')).toBeVisible();
		await expect(
			dialog.getByRole('button', { name: 'Back to the folders Sift has' })
		).toBeVisible();
	} finally {
		await page.request.delete(`/api/library/roots/${rootId}`, {
			headers: { 'x-csrf-token': await csrfOf(page) }
		});
		rmSync(TYPED, { recursive: true, force: true, maxRetries: 40, retryDelay: 250 });
	}
});

test('Migrate from Stash chooses the database file, and reads that file, never its folder', async ({
	page
}) => {
	mkdirSync(STASH, { recursive: true });
	for (const name of ['stash-go.sqlite', 'stash-go.sqlite.20260101_120000', 'notes.txt'])
		writeFileSync(join(STASH, name), 'not a database');
	// A library folder of its own, added by its path, so it heads the chooser whatever else is given.
	const added = await write(page, '/api/library/roots', { abs_path: STASH, scan: false });
	expect(added.ok(), await added.text()).toBeTruthy();
	const rootId = String((await added.json()).id);
	try {
		await page.goto('/settings/backup');
		const row = page.locator('.from-stash');
		await row.getByRole('button', { name: 'Choose\u2026' }).click();
		const dialog = page.getByRole('dialog', { name: "Choose Stash's database file" });
		await expect(dialog.getByRole('button', { name: 'Browse this device' })).toBeVisible();

		await dialog.getByRole('button', { name: '.chooser-stash', exact: true }).click();
		await expect(dialog.getByText('Which file')).toBeVisible();
		const files = dialog.locator('.file');
		await expect(files.locator('.name')).toHaveText([
			'stash-go.sqlite',
			'stash-go.sqlite.20260101_120000'
		]);

		// The folder alone is not an answer.
		await dialog.getByRole('button', { name: 'Use this file' }).click();
		await expect(dialog.getByText('Choose a file first')).toBeVisible();

		await files.nth(1).click();
		const read = page.waitForRequest(
			(request) =>
				request.url().endsWith('/api/stash-migration/read') && request.method() === 'POST'
		);
		await dialog.getByRole('button', { name: 'Use this file' }).click();
		expect((await read).postDataJSON()).toEqual({
			path: join(STASH, 'stash-go.sqlite.20260101_120000')
		});
	} finally {
		await page.request.delete(`/api/library/roots/${rootId}`, {
			headers: { 'x-csrf-token': await csrfOf(page) }
		});
		rmSync(STASH, { recursive: true, force: true, maxRetries: 40, retryDelay: 250 });
	}
});
