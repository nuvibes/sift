/*
 * A person whose facial fingerprints are thin, said where they leave this library and where they
 * arrive: the chooser's band and words, the held count, the group's question, and the one colour
 * rule the chooser shares with the person's page.
 */

import { describe, expect, it } from 'vitest';
import type { PickChoice } from '$lib/components/common/verbs';
import pickSource from '$lib/components/common/PickDialog.svelte?raw';
import pageSource from '$lib/components/RecognitionStrength.svelte?raw';
import { fingerprintQuestion } from './fingerprint-offers';
import { heldFaces, thinChoice, thinSaid } from './thin-fingerprints';

describe('a thin person on a chooser', () => {
	const row: PickChoice = { id: 'p1', name: 'Wren Hale' };

	it('is marked with the band the server gave, under twenty confirmed faces', () => {
		expect(thinChoice(row, { faces: 3, verdict: 'weak' }).strength).toEqual({
			band: 'weak',
			said: 'Only 3 confirmed faces: Sift recognizes them less surely'
		});
		expect(thinChoice(row, { faces: 7, verdict: 'fair' }).strength?.band).toBe('fair');
		expect(thinChoice(row, { faces: 19, verdict: 'good' }).strength?.band).toBe('good');
	});

	it('is left as it is once strong, or where the server gave no band', () => {
		expect(thinChoice(row, { faces: 25, verdict: 'strong' })).toBe(row);
		expect(thinChoice(row, { faces: 3 })).toBe(row);
	});

	it('says one face in the singular', () => {
		expect(thinSaid(1)).toBe('Only 1 confirmed face: Sift recognizes them less surely');
	});
});

describe('what a held entry brought', () => {
	it('says the faces alone where nothing said how many were confirmed', () => {
		expect(heldFaces(4)).toBe('4 faces');
		expect(heldFaces(1, null)).toBe('1 face');
	});

	it('says they were all confirmed where the file gave every one', () => {
		expect(heldFaces(3, 3)).toBe('3 confirmed faces');
		expect(heldFaces(1, 1)).toBe('1 confirmed face');
	});

	it('says how many of how many where the file gave the best of more', () => {
		expect(heldFaces(64, 210)).toBe('64 faces of 210 confirmed');
	});
});

describe("a group's question", () => {
	it('names where they came from and what it brought', () => {
		expect(
			fingerprintQuestion({ name: 'Liora Fenwick', source: 'Gallery', faces: 3, confirmed: 3 })
		).toBe(
			'This group looks like Liora Fenwick, from Gallery (3 confirmed faces). Create a person for them?'
		);
		expect(fingerprintQuestion({ name: 'Liora Fenwick', source: 'Gallery', faces: 4 })).toBe(
			'This group looks like Liora Fenwick, from Gallery (4 faces). Create a person for them?'
		);
	});

	it('asks as before where the answer named no source', () => {
		expect(fingerprintQuestion({ name: 'Liora Fenwick' })).toBe(
			'This group looks like Liora Fenwick, from a facial fingerprints file. Create a person for them?'
		);
	});
});

/** The three gradients a stylesheet draws, by band, as written. */
function gradients(source: string, selector: (band: string) => RegExp): Record<string, string> {
	const found: Record<string, string> = {};
	for (const band of ['fair', 'good']) {
		const rule = source.match(selector(band));
		found[band] = rule?.[1]?.replace(/\s+/g, ' ').trim() ?? '';
	}
	return found;
}

describe("the chooser's band and the person's page", () => {
	it('draw each band in the same colours', () => {
		const page = gradients(
			pageSource,
			(band) => new RegExp(`\\.spectrum\\[data-band='${band}'\\] \\{\\s*background: ([^;]+);`)
		);
		const chooser = gradients(
			pickSource,
			(band) => new RegExp(`\\.thin\\[data-band='${band}'\\] \\.band \\{\\s*background: ([^;]+);`)
		);
		expect(page.fair).toContain('linear-gradient');
		expect(chooser).toEqual(page);
		// The floor band and the orange it mixes, written in each.
		const floor = /--band-orange: ([^;]+);[\s\S]*?background: (linear-gradient\([^;]+\));/;
		expect(pickSource.match(floor)?.slice(1)).toEqual(pageSource.match(floor)?.slice(1));
	});
});
