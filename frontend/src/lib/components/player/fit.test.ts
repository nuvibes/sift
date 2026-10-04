/*
 * Whatever a frame holds is fitted to it: a video, a picture, and a GIF drawn frame by frame.
 *
 * Wherever the browser can decode a GIF one frame at a time (the desktop app, a localhost tab), the
 * GIF is drawn onto a `<canvas>` rather than shown by an `<img>`, so it can be held still
 * (`$lib/player/animation`). Fit rules naming `video` and `img` and not the canvas would leave a
 * GIF in the desktop app at its own size in the top left corner: a tall one cut off at the foot, a
 * wide one small in a corner of an empty frame. Over plain http the same GIF is an `<img>` and
 * fitted, so the fault shows only where the canvas is used.
 *
 * Read out of the source, deliberately: the unit document has no renderer and lays nothing out,
 * so a rendered test would pass with the rule missing. What can be held here is that the one rule
 * of each frame names all three, and fits rather than crops.
 *
 * Comments are taken out first, because the prose above each rule names the elements too.
 */
import { describe, expect, it } from 'vitest';
import stageSource from './MediaStage.svelte?raw';
import cornerSource from './MiniPlayer.svelte?raw';
import stillSource from './StillView.svelte?raw';
import cellSource from '../theater/CellView.svelte?raw';

/** The style block alone, without its comments. */
function styles(source: string): string {
	const sheet = source.slice(source.indexOf('<style>'));
	return sheet.replace(/\/\*[\s\S]*?\*\//g, '');
}

/** Every rule in a style block that says how its picture fits, as [selector, body]. */
function fitRules(source: string): Array<[string, string]> {
	return [...styles(source).matchAll(/([^{}]*)\{([^{}]*object-fit[^{}]*)\}/g)].map(
		([, selector, body]) => [selector.trim(), body]
	);
}

describe.each([
	['the stage (the pop-out, full screen, a Theater cell)', stageSource, '.stage'],
	['the corner player', cornerSource, '.screen']
])('%s', (_name, source, frame) => {
	const rules = fitRules(source);

	it('has one fit rule, and it is the frame-s', () => {
		expect(rules, 'one rule fits whatever the frame holds').toHaveLength(1);
		expect(rules[0][0].startsWith(frame)).toBe(true);
	});

	it('names a video, a picture and a GIF drawn on a canvas', () => {
		const [selector] = rules[0];
		for (const element of ['video', 'img', 'canvas']) {
			expect(selector, `a ${element} in the frame is not fitted`).toMatch(
				new RegExp(`\\b${element}\\b`)
			);
		}
	});

	it('fills the frame and fits the whole picture into it, never cropping', () => {
		const [, body] = rules[0];
		expect(body).toMatch(/inline-size:\s*100%/);
		expect(body).toMatch(/block-size:\s*100%/);
		expect(body).toMatch(/object-fit:\s*contain/);
	});
});

describe('the things the frames hold', () => {
	it('draws a held GIF on a canvas, which is what the frames must name', () => {
		expect(stillSource).toMatch(/<canvas[\s\S]*?class="picture"/);
	});

	it('leaves the fit to the stage in a Theater cell, rather than a second copy of it', () => {
		/* A cell sits inside a stage. A rule of its own would be a second answer to the same
		 * question, free to drift from the stage's. */
		expect(fitRules(cellSource)).toHaveLength(0);
	});
});
