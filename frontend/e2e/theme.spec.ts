/* Changing the look, in a real browser, where the stylesheet is actually resolved.
 *
 * Nothing in this file can be answered without one. The unit environment renders the appearance pane
 * happily and reports every custom property as the empty string, because jsdom has no cascade: it
 * would say the page was themed while the page was not. So the questions that matter are asked here:
 * does the accent actually change, does the whole screen change with it, does the choice come
 * back after a reload, and does it come back WITHOUT the wrong theme being painted first.
 */
import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/*
 * SERIAL, and it is not optional.
 *
 * The theme is a PER-ACCOUNT setting and every spec in this suite signs in as the same admin, so
 * these six tests all read and write one shared value. Run in parallel (which is this project's
 * default, four workers), one test's `afterEach` puts the theme back to midnight/blue while another
 * is halfway through asserting chrome and magenta. It would surface as a different one or two
 * failing on each run, which reads as the feature being flaky rather than as the suite competing
 * with itself. Same reason `browse.spec.ts` and `tags.spec.ts` are serial.
 */
test.describe.configure({ mode: 'serial' });

const APPEARANCE = '/settings/appearance';

/** What the page is wearing, read off the document the way the stylesheet keys on it. */
const wearing = (page: Page) =>
	page.evaluate(() => ({
		base: document.documentElement.getAttribute('data-base'),
		accent: document.documentElement.getAttribute('data-accent'),
		// Two faces: a pairing sets both, and either can be chosen on its own.
		faceDisplay: document.documentElement.getAttribute('data-face-display'),
		faceBody: document.documentElement.getAttribute('data-face-body')
	}));

/** A resolved token, as the browser computes it, not as the file writes it. */
const token = (page: Page, name: string) =>
	page.evaluate(
		(property) => getComputedStyle(document.documentElement).getPropertyValue(property).trim(),
		name
	);

/** Press a swatch and wait for the save to actually land.
 *
 * The app applies a choice to the screen before the server confirms it, on purpose: judging a
 * colour means looking at it, and a round trip's wait to find out what one looks like feels broken.
 * That is right in the app and a trap in a test: a hard navigation (`goto`, `reload`) cancels a
 * request still in flight, so a spec that clicks and immediately reloads is testing what happens
 * when the save is abandoned rather than what happens when it works. Inside the app the same journey
 * is a client-side navigation, which cancels nothing.
 */
async function choose(page: Page, name: string): Promise<void> {
	/* Matched on the swatch's OWN name, not on its accessible name.
	 *
	 * A swatch's accessible name is everything inside it (the sample, the name and the note),
	 * so `getByRole('radio', { name: 'Chrome' })` also matches Graphite, whose note reads "the
	 * same grey as Chrome, darker throughout", and taking the first match presses the wrong
	 * swatch.
	 */
	const swatch = page
		.getByRole('radio')
		.filter({ has: page.locator('.name, .accent-name').filter({ hasText: name }) })
		.first();
	/* Found, said out loud: a missing swatch fails here, by name (the choice card's own
	   `.name`), rather than as a thirty-second timeout inside a helper that reads as the setting
	   being slow. */
	await expect(swatch, `there is no swatch called ${name}`).toBeVisible();
	/* Not before the pane knows what the account is wearing. The store paints the default until
	   the server answers, so a swatch read a beat too early looks unpressed, the click lands on
	   a choice the store has just loaded as already made, nothing is saved, and the wait below
	   never ends. The pane says it is busy until then. */
	await expect(page.locator('section[aria-busy="true"]')).toHaveCount(0);
	if ((await swatch.getAttribute('aria-checked')) === 'true') return;
	const saved = page.waitForResponse(
		(response) => response.url().includes('/api/settings') && response.request().method() === 'PUT'
	);
	await swatch.click();
	await saved;
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
});

/** The token the API asks for on a write, the same way the app gets it. */
async function csrf(page: Page): Promise<string> {
	const me = await page.request.get('/api/auth/me');
	return String((await me.json()).csrf_token);
}

test.afterEach(async ({ page }) => {
	// The choice is per account and this suite shares one, so a spec that left the account in
	// chrome with a magenta accent would hand the next file a differently coloured app.
	await page.request.put('/api/settings', {
		data: {
			values: {
				'appearance.theme_base': 'midnight',
				'appearance.theme_accent': 'blue',
				'appearance.theme_accent_hex': '#2563eb',
				'appearance.theme_face_display': 'archivo',
				'appearance.theme_face_body': 'instrument-sans'
			}
		},
		headers: { 'x-csrf-token': await csrf(page) }
	});
});

test('the accent changes the whole app, not the swatch that was pressed', async ({ page }) => {
	await page.goto(APPEARANCE);
	await expect(page.getByRole('heading', { name: 'Appearance' })).toBeVisible();

	const before = await token(page, '--sift-accent');

	await choose(page, 'Green');

	await expect.poll(async () => (await wearing(page)).accent).toBe('green');
	const after = await token(page, '--sift-accent');
	expect(after, 'the accent token did not move').not.toBe(before);

	/* The point of the split, checked rather than assumed: the accent is not swapped screen by
	 * screen, it is swapped once underneath the semantic name, so a page that was never open when
	 * the choice was made is already wearing it. */
	await page.goto('/browse');
	expect(await token(page, '--sift-accent')).toBe(after);
	expect((await wearing(page)).accent).toBe('green');
});

test('the background changes every surface with it', async ({ page }) => {
	await page.goto(APPEARANCE);
	const canvas = await token(page, '--sift-bg');
	const card = await token(page, '--sift-surface-2');
	const ink = await token(page, '--sift-ink-3');

	await choose(page, 'Chrome');
	await expect.poll(async () => (await wearing(page)).base).toBe('chrome');

	expect(await token(page, '--sift-bg'), 'the canvas').not.toBe(canvas);
	expect(await token(page, '--sift-surface-2'), 'the cards').not.toBe(card);
	/* A base that moved the surfaces and left the muted ink where it was would be a base whose
	 * captions are under the contrast floor. So the ink moving is the evidence that the second
	 * base was measured rather than copied.
	 */
	expect(await token(page, '--sift-ink-3'), 'the caption ink was re-measured').not.toBe(ink);
});

test('the lettering changes the family and leaves every size alone', async ({ page }) => {
	await page.goto(APPEARANCE);

	const sizesBefore = await page.evaluate(() => {
		const style = getComputedStyle(document.body);
		return { size: style.fontSize, line: style.lineHeight };
	});
	const familyBefore = await token(page, '--font-sans');

	await choose(page, 'Geist Mono and Geist');
	await expect.poll(async () => (await wearing(page)).faceDisplay).toBe('geist-mono');
	await expect.poll(async () => (await wearing(page)).faceBody).toBe('geist');

	expect(await token(page, '--font-sans')).not.toBe(familyBefore);
	await expect
		.poll(async () =>
			page.evaluate(() => {
				const style = getComputedStyle(document.body);
				return { size: style.fontSize, line: style.lineHeight };
			})
		)
		.toEqual(sizesBefore);
});

test('the choice comes back after a reload, and nothing else is painted first', async ({
	page
}) => {
	await page.goto(APPEARANCE);
	await choose(page, 'Chrome');
	await choose(page, 'Magenta');
	await expect.poll(async () => (await wearing(page)).accent).toBe('magenta');

	/*
	 * THE FLASH, caught rather than assumed away.
	 *
	 * The choice lives on the server, so it arrives one request after the page. Without the boot
	 * script the browser would paint the default theme and change colour a moment later, on every
	 * single load. So the check is not "is it right eventually" but "was it ever wrong": the
	 * attributes are read at the earliest moment a script can run in the new document, before the
	 * app has mounted and long before any request could have come back.
	 */
	const painted: { base: string | null; accent: string | null }[] = [];
	await page.addInitScript(() => {
		// Queued before anything else in the document. The boot script runs during parsing, so by the
		// time this fires on DOMContentLoaded the attributes are either right or they never were.
		document.addEventListener('DOMContentLoaded', () => {
			const root = document.documentElement;
			(window as unknown as { firstPaint: unknown }).firstPaint = {
				base: root.getAttribute('data-base'),
				accent: root.getAttribute('data-accent')
			};
		});
	});

	await page.reload();
	painted.push(await page.evaluate(() => (window as unknown as { firstPaint: never }).firstPaint));

	expect(painted[0], 'the page was painted in the default and corrected afterwards').toEqual({
		base: 'chrome',
		accent: 'magenta'
	});
	expect(await wearing(page)).toMatchObject({ base: 'chrome', accent: 'magenta' });
});

test('the tightest labels still fit under every pairing', async ({ page }) => {
	/*
	 * Two faces at one size are not one width, and it is never the headings that break. It is the
	 * small button labels and the chips, which are sized to the text they hold. A pairing that
	 * overflows one of those does it on a screen nobody thought to look at, in a face nobody has
	 * chosen yet.
	 */
	await page.goto(APPEARANCE);

	const faces = [
		'Archivo and Instrument Sans',
		'Space Grotesk and Inter',
		'Geist Mono and Geist',
		'Manrope and Public Sans',
		'Space Grotesk and DM Sans',
		'JetBrains Mono and Sora'
	];

	for (const face of faces) {
		await choose(page, face);
		// Wait for the family to be the one that was asked for before measuring anything in it.
		await expect.poll(async () => (await wearing(page)).faceDisplay).not.toBe(null);
		await page.waitForFunction(() => document.fonts.ready.then(() => true));

		const overflowing = await page.evaluate(() => {
			const tight = [...document.querySelectorAll('button, .pick-name, .accent-name, .face-data')];
			return tight
				.filter((el) => el.scrollWidth > el.clientWidth + 1)
				.map((el) => `${el.className || el.tagName}: ${el.textContent?.trim().slice(0, 30)}`);
		});

		expect(overflowing, `labels overflow under ${face}`).toEqual([]);
	}
});

test('every swatch shows the theme it offers, not the one the page is wearing', async ({
	page
}) => {
	/*
	 * Each swatch previews its own choice.
	 *
	 * A custom property that reads another is substituted once, on the element it is declared on,
	 * and what inherits down is the answer rather than the question. So `--sift-accent:
	 * var(--p-accent)` on `:root` resolves to the page's accent and inherits that COLOUR, so putting
	 * `data-accent` on a swatch changes a primitive underneath a semantic that has already stopped
	 * asking. Every swatch would render, every button work, every choice apply, and every preview
	 * show only what you already have.
	 *
	 * A unit test cannot ask this: jsdom has no cascade and reports every custom property as the
	 * empty string, so it would say the swatches were fine. Only a browser resolves it.
	 */
	await page.goto(APPEARANCE);
	await expect(page.getByRole('radio', { name: 'Green' })).toBeVisible();

	const distinct = async (
		selector: string,
		property: 'backgroundColor' | 'backgroundImage' | 'fontFamily'
	) => {
		const values = await page.$$eval(
			selector,
			(els, prop) => els.map((el) => getComputedStyle(el)[prop as 'backgroundColor']),
			property
		);
		expect(values.length, `nothing matched ${selector}`).toBeGreaterThan(1);
		return new Set(values);
	};

	// The six NAMED accents. The seventh is excluded by its own class: it wears the whole circle of
	// hues, which is a gradient and so has no background COLOUR at all. Counted, it would read as
	// a seventh value that is the same transparent nothing whatever anybody chose.
	expect(
		await distinct('.accent .dot:not(.rainbow)', 'backgroundColor'),
		'six accents, six colours'
	).toHaveProperty('size', 6);
	// A base's canvas is its lit page ground, a gradient, so it is read as the image it paints.
	expect(await distinct('.sample', 'backgroundImage'), 'four bases, four canvases').toHaveProperty(
		'size',
		4
	);
	// Six pairings and five Main faces: Space Grotesk heads two of them.
	expect(
		await distinct('.face-display', 'fontFamily'),
		'six pairings, five Main faces'
	).toHaveProperty('size', 5);
});
