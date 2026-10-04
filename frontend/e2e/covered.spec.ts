import { expect, test } from '@playwright/test';
import { existsSync } from 'node:fs';
import { signInAsAdmin } from './admin';

/** Whether the private gallery is nested in this tree. See the root .gitignore. */
const GALLERY_HERE = existsSync(new URL('../src/routes/design/+page.svelte', import.meta.url));

/*
 * A covered run of text leaves no letter on the screen, frost and all.
 *
 * `Covered` wears a soft edge and a blurred band of light so it reads as text out of focus rather
 * than a hard tile. The unit test holds the rules (transparent text, an opaque ground, the blur on
 * a layer of its own); this holds what came out of the renderer: the middle of the covered box,
 * photographed, is one smooth ground with no letter shapes in it, while the same text shown is
 * all edges. The two are three orders of magnitude apart (about 9 against about 9700 on the
 * gallery's specimen at 3x), so the floor sits well clear of both.
 */

/** The spread of the light in the middle of the nth covered box of the gallery's specimen. */
async function spread(page: import('@playwright/test').Page, nth: number): Promise<number> {
	const box = page.locator('section[data-names="Covered"] .covered').nth(nth);
	await box.scrollIntoViewIfNeeded();
	const at = await box.boundingBox();
	if (!at) throw new Error('the specimen drew no box');
	// A veil in from every edge, where the soft edge has ended and only the ground is left.
	const inset = 4;
	const png = await page.screenshot({
		clip: {
			x: at.x + inset,
			y: at.y + inset,
			width: at.width - 2 * inset,
			height: at.height - 2 * inset
		}
	});
	return await page.evaluate(async (data) => {
		const picture = new Image();
		picture.src = `data:image/png;base64,${data}`;
		await picture.decode();
		const canvas = document.createElement('canvas');
		canvas.width = picture.width;
		canvas.height = picture.height;
		const context = canvas.getContext('2d')!;
		context.drawImage(picture, 0, 0);
		const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
		let count = 0;
		let sum = 0;
		let squares = 0;
		for (let at = 0; at < pixels.length; at += 4) {
			const light = 0.2126 * pixels[at] + 0.7152 * pixels[at + 1] + 0.0722 * pixels[at + 2];
			count += 1;
			sum += light;
			squares += light * light;
		}
		const mean = sum / count;
		return squares / count - mean * mean;
	}, png.toString('base64'));
}

test('a covered box photographs as a smooth ground, and the same text shown does not', async ({
	page
}) => {
	test.skip(!GALLERY_HERE, 'the private gallery is not in this tree');
	await signInAsAdmin(page);
	await page.goto('/design');
	await expect(page.locator('section[data-names="Covered"] .covered')).toHaveCount(2);

	const covered = await spread(page, 0);
	const shown = await spread(page, 1);

	// The known positive first: shown, the letters are there to be found.
	expect(shown).toBeGreaterThan(1000);
	expect(covered).toBeLessThan(60);
});
