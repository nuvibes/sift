import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { existsSync } from 'node:fs';
import { signInAsAdmin } from './admin';

/** Whether the private gallery is nested in this tree. See the root .gitignore. */
const GALLERY_HERE = existsSync(new URL('../src/routes/design/+page.svelte', import.meta.url));

/*
 * No text box wears the browser's clothes.
 *
 * ## The failure this is for
 *
 * An `<input>` nobody dressed is a white box with the operating system's border and font, sitting
 * in the middle of a dark interface. It is not caught by anything static: the markup says
 * `<input>`, which is correct, and the stylesheet is full of rules, none of which happens to reach
 * it.
 *
 * The look is a baseline in the global stylesheet, and a design gate checks the baseline is still
 * there. This is the other half: the gate reads the stylesheet, and this reads what the elements
 * ACTUALLY ENDED UP LOOKING LIKE. A rule that exists and does not reach an element is
 * indistinguishable, in a stylesheet, from a rule that does.
 *
 * ## Why it asks about paleness rather than about an exact colour
 *
 * Every surface this app draws is dark, and the browser's default is white. So the question is not
 * "is it the right token" (the token gate answers that) but "did anybody dress this at all",
 * and the honest test for that is a box light enough to be the default one. A theme that ever ships
 * a light palette will need this rewritten, and it should be: at that point "pale" stops meaning
 * "nobody styled it".
 */

/**
 * What was looked at, and which of it the browser dressed rather than the app.
 *
 * `examined` is not decoration. Returning only the offenders, a screen that had not finished
 * drawing (or a sheet that never opened) would answer "nothing wrong" and be indistinguishable
 * from a screen where everything was right, and a deliberately broken stylesheet would pass.
 * Every check below asserts a known positive first.
 */
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
			// The computed value, not the rule: what is being asked is what it came out as.
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
	// The gallery draws one of nearly everything, which is the point of it, so it is the screen
	// most likely to hold a control no page has got round to using yet. Only where it is: the
	// gallery is kept in a separate repository, and a clone has no such route.
	...(GALLERY_HERE ? ['/design'] : [])
];

test('no text box on any screen is left in the browser default', async ({ page }) => {
	await signInAsAdmin(page);

	for (const screen of SCREENS) {
		await page.goto(screen);
		// Not `networkidle`: some of these hold a live stream and never go quiet. What is being
		// measured is laid out well before that anyway.
		await page.waitForLoadState('domcontentloaded');
		// The known positive: wait for a box to BE there rather than for a moment to pass. A screen
		// still drawing has nothing to measure and reports no offences.
		await page.locator('input, textarea').first().waitFor({ state: 'attached' });
		const seen = await inspect(page);
		expect(seen.examined, `nothing to measure on ${screen}`).toBeGreaterThan(0);
		expect(seen.dressedByTheBrowser, `on ${screen}`).toEqual([]);
	}
});

test('nor one inside a sheet, where a new one is likeliest', async ({ page }) => {
	test.skip(!GALLERY_HERE, 'the private gallery is not in this tree');
	/* A dialog is drawn into `document.body` rather than into the page, and it is the place a new
	   control is most likely to appear without anybody thinking about a form. */
	await signInAsAdmin(page);
	await page.goto('/design');
	await page.waitForLoadState('domcontentloaded');

	/* Only sheets that really hold a text box.
	 *
	 * Sharing is a list of accounts and has none, and Move chooses its destination with a
	 * `Select`, so demanding a box there fails on a sheet that is perfectly correct. The one
	 * below is named rather than discovered because the point of the check is the known positive:
	 * if it stops holding a box, this should fail and be looked at rather than quietly passing
	 * over it.
	 */
	for (const sheet of ['Stash-box']) {
		/* Waited for, never skipped: a loop that skipped an empty sheet would, on a gallery still
		 * drawing, pass over all three and report success having opened nothing. A check that can
		 * quietly examine nothing is not a check.
		 */
		const opener = page.getByRole('button', { name: sheet, exact: true }).first();
		await opener.waitFor({ state: 'visible' });
		await opener.click();
		/* Located by the class every sheet in this app wears, not by the ARIA role: the overlay and
		   the content are two elements and the role match is ambiguous between them. `.sheet` is the
		   app's own name for the thing being measured. */
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
