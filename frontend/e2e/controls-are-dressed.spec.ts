import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { existsSync } from 'node:fs';
import { signInAsAdmin } from './admin';

/** Whether the DESIGN GALLERY is nested in this tree (see the root .gitignore). */
const GALLERY_HERE = existsSync(new URL('../src/routes/design/+page.svelte', import.meta.url));

/*
 * No text box wears the browser's clothes. A design gate reads the stylesheet's baseline; this
 * reads what the elements came out as. Every surface here is dark, so a pale box is one nobody
 * dressed.
 */

/** What was looked at, and which of it the browser dressed: `examined` is the known positive. */
async function inspect(page: Page): Promise<{ examined: number; dressedByTheBrowser: string[] }> {
	return await page.evaluate(() => {
		const boxy = (el: HTMLInputElement | HTMLTextAreaElement) =>
			el.tagName === 'TEXTAREA' ||
			!['range', 'checkbox', 'radio', 'file', 'color', 'hidden'].includes(
				(el as HTMLInputElement).type
			);
		const found: string[] = [];
		let examined = 0;
		for (const el of document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>(
			'input, textarea'
		)) {
			if (!boxy(el)) continue;
			examined += 1;
			// The computed value, not the rule.
			const parts = getComputedStyle(el).backgroundColor.match(/rgba?\((\d+), (\d+), (\d+)/);
			if (!parts) continue;
			const pale = [1, 2, 3].every((at) => Number(parts[at]) > 200);
			if (!pale) continue;
			found.push(
				`${el.getAttribute('aria-label') ?? el.getAttribute('placeholder') ?? el.id ?? el.tagName}` +
					` (${getComputedStyle(el).backgroundColor})`
			);
		}
		return { examined, dressedByTheBrowser: found };
	});
}

const SCREENS = [
	'/browse',
	'/people',
	'/tags',
	'/sites',
	'/collections',
	'/photo-sets',
	'/loops',
	'/settings/profile',
	'/settings/connections',
	// The DESIGN GALLERY draws one of nearly everything; a clone has no such route.
	...(GALLERY_HERE ? ['/design'] : [])
];

test('no text box on any screen is left in the browser default', async ({ page }) => {
	await signInAsAdmin(page);

	for (const screen of SCREENS) {
		await page.goto(screen);
		// Not `networkidle`: a live stream never goes quiet.
		await page.waitForLoadState('domcontentloaded');
		// The known positive: a box is there to measure.
		await page.locator('input, textarea').first().waitFor({ state: 'attached' });
		const seen = await inspect(page);
		expect(seen.examined, `nothing to measure on ${screen}`).toBeGreaterThan(0);
		expect(seen.dressedByTheBrowser, `on ${screen}`).toEqual([]);
	}
});

test('nor one inside a sheet, where a new one is likeliest', async ({ page }) => {
	test.skip(!GALLERY_HERE, 'the private gallery is not in this tree');
	/* A dialog is drawn into `document.body`, where new controls appear unnoticed. */
	await signInAsAdmin(page);
	await page.goto('/design');
	await page.waitForLoadState('domcontentloaded');

	/* Only sheets that really hold a text box, named so one that stops holding it fails. */
	for (const sheet of ['Stash-box']) {
		/* Waited for, never skipped: a check that can examine nothing is not a check. */
		const opener = page.getByRole('button', { name: sheet, exact: true }).first();
		await opener.waitFor({ state: 'visible' });
		await opener.click();
		/* By the class every sheet wears: the role matches the overlay and the content both. */
		const dialog = page.locator('.sheet');
		await dialog.waitFor({ state: 'visible' });
		await dialog.locator('input, textarea').first().waitFor({ state: 'attached' });

		const seen = await inspect(page);
		expect(seen.examined, `the ${sheet} sheet held nothing to measure`).toBeGreaterThan(0);
		expect(seen.dressedByTheBrowser, `in the ${sheet} sheet`).toEqual([]);

		await page.keyboard.press('Escape');
		await dialog.waitFor({ state: 'detached' });
	}
});
