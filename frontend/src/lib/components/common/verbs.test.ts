/*
 * What a pick LANDED as, read off the server's own answer, which is what the picker puts its tick
 * back from (`PickLanded`, `landedOf`).
 *
 * The one reading that matters and is easy to get wrong: `changed` does not answer this. Putting a
 * tag on files that already carry it changes nothing and is a pick that landed; what the server did
 * NOT act on is `skipped`, counted in the items it was asked about.
 */
import { describe, expect, it } from 'vitest';

import { landedOf } from './verbs';

const done = { changed: 0, skipped: 0, reason: null, reason_many: null, vault_locked: false };

describe('what a pick landed as', () => {
	it('landed where nothing was left out, even where nothing changed', () => {
		expect(landedOf({ ...done, changed: 0, skipped: 0 }, 3)).toBe('landed');
		expect(landedOf({ ...done, changed: 3, skipped: 0 }, 3)).toBe('landed');
	});

	it('refused where every one was left out', () => {
		expect(landedOf({ ...done, skipped: 3 }, 3)).toBe('refused');
	});

	it('partly where some were left out and some were not', () => {
		expect(landedOf({ ...done, changed: 2, skipped: 1 }, 3)).toBe('partly');
	});

	it('refused where the write failed outright', () => {
		expect(landedOf(null, 3)).toBe('refused');
	});
});
