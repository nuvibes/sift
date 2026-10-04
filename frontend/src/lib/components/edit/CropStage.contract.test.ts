/*
 * One crop control in the app, drawn by both screens that crop: the picture editor and a cover's
 * framing step. `CropStage` is the one implementation. This keeps it one: the rectangle's marks
 * exist in that file and nowhere else, and both screens reach it.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = 'src';
const THE_ONE = 'src/lib/components/edit/CropStage.svelte';

function svelteFiles(dir: string): string[] {
	return readdirSync(dir).flatMap((name) => {
		const path = join(dir, name);
		if (statSync(path).isDirectory()) return svelteFiles(path);
		return name.endsWith('.svelte') ? [path.replaceAll('\\', '/')] : [];
	});
}

/** What drawing a crop rectangle takes: the dimming veil, the grips, the geometry's drag. */
const MARKS = [/--sift-crop-veil/, /data-grip=/, /\bshaped\(\s*dragged\(/];

describe('the crop control', () => {
	it('is drawn by one file and no other', () => {
		const drawers = svelteFiles(ROOT).filter((file) =>
			MARKS.some((mark) => mark.test(readFileSync(file, 'utf8')))
		);
		expect(drawers).toEqual([THE_ONE]);
	});

	it('is what the picture editor draws', () => {
		const panel = readFileSync('src/lib/components/edit/PicturePanel.svelte', 'utf8');
		expect(panel).toContain('<CropStage');
	});

	it('is what a cover is framed with, on both screens that frame one', () => {
		const framer = readFileSync('src/lib/components/entity/CoverFramer.svelte', 'utf8');
		expect(framer).toContain('<CropStage');
		// None of a separate framer's own gestures: the editor has none of them.
		expect(framer).not.toMatch(/<Slider|onwheel|role="slider"/);
		for (const screen of ['EntityHeader', 'PickPicture']) {
			const source = readFileSync(`src/lib/components/entity/${screen}.svelte`, 'utf8');
			expect(source, `${screen} frames a cover some other way`).toContain('<CoverFramer');
		}
	});
});
