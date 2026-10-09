/*
 * Whatever a frame holds is fitted: video, picture, and the `<canvas>` a GIF is drawn on where it
 * can be held still. Read from the source, comments out: jsdom lays nothing out.
 */
import { describe, expect, it } from 'vitest';
import stageSource from './MediaStage.svelte?raw';
import cornerSource from './MiniPlayer.svelte?raw';
import stillSource from './StillView.svelte?raw';
import cellSource from '../theater/CellView.svelte?raw';

function styles(source: string): string {
	const sheet = source.slice(source.indexOf('<style>'));
	return sheet.replace(/\/\*[\s\S]*?\*\//g, '');
}

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
		/* A cell sits inside a stage, so a rule of its own would be a second answer. */
		expect(fitRules(cellSource)).toHaveLength(0);
	});
});
