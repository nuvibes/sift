/*
 * What a key just did, in the words the badge over the picture shows.
 *
 * The wording is the whole of what is worth pinning here. A volume badge that says "+2" and not
 * where that left the level is the report somebody could already have worked out, and one that says
 * the step it was ASKED for rather than the step it got claims a rise that did not happen at the top
 * of the range. Both of those read as correct until somebody looks.
 */
import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import { noteEcho, rateWords, volumeWords, type CellEcho } from './echoes';

describe('noting an echo against the cells it happened to', () => {
	it("raises each cell's own count, so a badge shows itself again", () => {
		const at: Record<string, CellEcho> = {};

		noteEcho(at, ['one', 'two'], { icon: 'volume_up', label: 'Volume' });
		noteEcho(at, ['one'], { icon: 'volume_up', label: 'Volume' });

		expect(at.one.press).toBe(2);
		expect(at.two.press, 'a press reached a cell it never addressed').toBe(1);
	});

	it('carries the words and the glyph across unchanged', () => {
		const at: Record<string, CellEcho> = {};

		noteEcho(at, ['one'], {
			icon: 'fast_forward',
			label: 'Faster',
			detail: '2x',
			muted: false
		});

		expect(at.one).toMatchObject({ icon: 'fast_forward', label: 'Faster', detail: '2x' });
	});

	it('leaves the record it was handed as the only copy', () => {
		// The screen holds one record and hands it to the wall. A function that replaced it would
		// leave the wall drawing whatever it was given the first time.
		const at: Record<string, CellEcho> = {};

		noteEcho(at, ['one'], { icon: 'pause', label: 'Stopped' });

		expect(Object.keys(at)).toEqual(['one']);
	});
});

describe('the words', () => {
	it('gives a volume its step AND the level it landed on', () => {
		expect(volumeWords(2, 54)).toBe('+2 (54)');
		expect(volumeWords(-10, 44)).toBe('-10 (44)');
	});

	it('says plainly that a press against the end of the range moved nothing', () => {
		expect(volumeWords(0, 100)).toBe('0 (100)');
	});

	it('writes a rate the way every player writes one', () => {
		expect(rateWords(0.5)).toBe('0.5x');
		expect(rateWords(2)).toBe('2x');
		expect(rateWords(1)).toBe('1x');
	});

	/* A step key shows no filename, so there is no `shortName` to test. See the note in
	   `echoes.ts`. */
});

describe('the words each player raises', () => {
	it('are built here and never written again on the Theater page', () => {
		/* The volume, seek and mute words written inline on the page beside the builders the
		   Player reads would be one press worded in two places. */
		const page = readFileSync('src/routes/theater/+page.svelte', 'utf8');
		expect(page.length).toBeGreaterThan(1000);
		for (const builder of ['volumeEcho(', 'skipEcho(', 'muteEcho('])
			expect(page).toContain(builder);
		for (const inline of [
			"label: 'Volume'",
			"'replay_5'",
			"'forward_5'",
			"label: silent ? 'Muted'"
		]) {
			expect(page, inline).not.toContain(inline);
		}
	});
});
