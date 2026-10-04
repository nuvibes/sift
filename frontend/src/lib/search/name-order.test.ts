/*
 * The client's copy of the server's sort key, held to the orders the server gives.
 *
 * The expected lists are what `GET /api/tags?sort=name_az` and `/api/people?sort=name_az` answer,
 * written with invented names of the same shape. If this and the server ever disagree, a sheet and
 * a flyout over the same list order it differently.
 */
import { describe, expect, it } from 'vitest';

import { byName, nameKey } from './name-order';

function ordered(...names: string[]): string[] {
	return names
		.map((name) => ({ name }))
		.sort(byName)
		.map((one) => one.name);
}

describe('where a name files', () => {
	it('ignores case, as the walls do', () => {
		expect(ordered('Bicycle', 'beach', 'BBQ', 'Autumn', '69')).toEqual([
			'69',
			'Autumn',
			'BBQ',
			'beach',
			'Bicycle'
		]);
	});

	it('files an accented name with its plain spelling', () => {
		// Built from code points: every source file in this repository is ASCII.
		const ringed = `${String.fromCodePoint(0xc5)}lesund`;
		const acute = `${String.fromCodePoint(0xe9)}clair`;
		expect(ordered('Zoe', ringed, 'apple', acute)).toEqual([ringed, 'apple', acute, 'Zoe']);
	});

	it('puts punctuation where the server key puts it, not where a locale would', () => {
		// "@" is below the letters in the key, so a handle files first, as the People page does.
		expect(ordered('Ada Byron', '@somebody', 'aaron')).toEqual(['@somebody', 'aaron', 'Ada Byron']);
	});

	it('collapses whitespace, and is never an error on an empty name', () => {
		expect(nameKey(`  Blue ${String.fromCodePoint(0xa0)} hour `)).toBe('blue hour');
		expect(nameKey('')).toBe('');
	});

	it('is a total order, so two spellings of one key do not swap between runs', () => {
		expect(ordered('dune', 'Dune')).toEqual(['Dune', 'dune']);
		expect(ordered('Dune', 'dune')).toEqual(['Dune', 'dune']);
	});
});
