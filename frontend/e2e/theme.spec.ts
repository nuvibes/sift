/* Changing the look in a browser, where the cascade is real: jsdom reports every custom
 * property as empty. Includes that a reload paints the right theme first. */
import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

// Serial: the theme is a setting of the one admin every spec shares.
test.describe.configure({ mode: 'serial' });

const APPEARANCE = '/settings/appearance';

/** What the page is wearing, read off the document the way the stylesheet keys on it. */
const wearing = (page: Page) =>
	page.evaluate(() => ({
		base: document.documentElement.getAttribute('data-base'),
		accent: document.documentElement.getAttribute('data-accent'),
		faceDisplay: document.documentElement.getAttribute('data-face-display'),
		faceBody: document.documentElement.getAttribute('data-face-body')
	}));

/** A resolved token, as the browser computes it. */
const token = (page: Page, name: string) =>
	page.evaluate(
		(property) => getComputedStyle(document.documentElement).getPropertyValue(property).trim(),
		name
	);

/** Press a swatch and wait for the save: a hard reload would cancel it in flight. */
async function choose(page: Page, name: string): Promise<void> {
	// By the swatch's own name: Graphite's note mentions Chrome.
	const swatch = page
		.getByRole('radio')
		.filter({ has: page.locator('.name, .accent-name').filter({ hasText: name }) })
		.first();
	await expect(swatch, `there is no swatch called ${name}`).toBeVisible();
	// Once the pane is no longer busy, or the swatch reads unpressed and the click saves nothing.
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

async function csrf(page: Page): Promise<string> {
	const me = await page.request.get('/api/auth/me');
	return String((await me.json()).csrf_token);
}

test.afterEach(async ({ page }) => {
	// Put back: a shared account left in chrome and magenta colours the next file.
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

	// A screen never opened when the choice was made already wears it.
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
	// The caption ink moves too, so the base was measured for contrast, not copied.
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

	/* Never the wrong theme for a frame: read at DOMContentLoaded, before any request returns. */
	const painted: { base: string | null; accent: string | null }[] = [];
	await page.addInitScript(() => {
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
	// Every pairing fits the small labels and chips, where overflow shows first.
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
	/* Each swatch previews its own choice: a semantic token resolved on `:root` would make every
	 * preview show the current one. */
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

	// Six named accents; the seventh is a gradient with no background colour.
	expect(
		await distinct('.accent .dot:not(.rainbow)', 'backgroundColor'),
		'six accents, six colours'
	).toHaveProperty('size', 6);
	// A base's canvas is a gradient, so it is read as an image.
	expect(await distinct('.sample', 'backgroundImage'), 'four bases, four canvases').toHaveProperty(
		'size',
		4
	);
	expect(
		await distinct('.face-display', 'fontFamily'),
		'six pairings, five Main faces'
	).toHaveProperty('size', 5);
});
