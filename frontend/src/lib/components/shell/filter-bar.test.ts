import { describe, expect, it } from 'vitest';

import { ChipOrder, flip, opposite, pick, placed, stanceOf } from './filter-bar.svelte';
import type { Narrowing } from './screen-bar.svelte';

/** A filtering held in a string, which is all a writer needs. */
function held(start: string): Narrowing & { now: string } {
	const where = {
		now: start,
		read: () => new URLSearchParams(where.now),
		write: (next: URLSearchParams) => {
			where.now = next.toString();
		}
	};
	return where;
}

describe('a facet written back', () => {
	it('stands where its first parameter stood', () => {
		const at = new URLSearchParams('tags=a&people=c&tags=-b');
		expect(placed(at, 'tags', ['b', '-a']).toString()).toBe('tags=b&tags=-a&people=c');
	});

	it('goes at the end when it was not there, and comes off when it holds nothing', () => {
		const at = new URLSearchParams('people=c');
		expect(placed(at, 'tags', ['a']).toString()).toBe('people=c&tags=a');
		expect(placed(at, 'people', []).toString()).toBe('');
	});
});

describe('a name beginning with a minus on a wall of things', () => {
	it.each(['person', 'tag', 'site'] as const)(
		'%s: is picked in quotes and refused outside them',
		(wall) => {
			const where = held('');
			pick(wall, 'tags', '-raw', 'on', where);
			expect(where.now).toBe('tags=%22-raw%22');
			expect(stanceOf('tags', '-raw', where)).toBe('on');
			pick(wall, 'tags', '-raw', 'out', where);
			expect(where.now).toBe('tags=-%22-raw%22');
			expect(stanceOf('tags', '-raw', where)).toBe('out');
		}
	);
});

describe('a press on a chip', () => {
	it('refuses a value where its facet stood', () => {
		const where = held('tags=a%7Cb&people=c');
		flip('asset', 'tags', 'a', where);
		expect(where.now).toBe('tags=b&tags=-a&people=c');
	});

	it('swaps Has for No rather than refusing it', () => {
		const where = held('tags=any&people=c');
		flip('asset', 'tags', 'any', where);
		expect(where.now).toBe('tags=none&people=c');
		flip('asset', 'tags', 'none', where);
		expect(where.now).toBe('tags=any&people=c');
	});

	it('swaps Has tags for No tags on a wall of things, and refuses its other columns', () => {
		const where = held('tags=none');
		flip('person', 'tags', 'none', where);
		expect(where.now).toBe('tags=any');
		const sites = held('sites=any');
		flip('person', 'sites', 'any', sites);
		expect(sites.now).toBe('sites=-any');
	});
});

describe('the order chips are drawn in', () => {
	const read = ([field, value]: [string, string]) => ({ field, values: value.split('|') });

	it('keeps a refused value where the bar first saw it, under the same key', () => {
		const order = new ChipOrder();
		const first = order.lay<[string, string]>(
			[
				['tags', 'a|b'],
				['people', 'c']
			],
			read
		);
		const after = order.lay<[string, string]>(
			[
				['tags', 'b'],
				['tags', '-a'],
				['people', 'c']
			],
			(pair) => read([pair[0], pair[1].replace(/^-/, '')])
		);
		expect(after.map((chip) => chip.key)).toEqual(first.map((chip) => chip.key));
		expect(after.map((chip) => chip.value)).toEqual(['a', 'b', 'c']);
	});

	it('keeps a presence chip under one key when Has swaps for No, so the keyboard stays on it', () => {
		const order = new ChipOrder();
		const presence = (field: string, value: string) =>
			opposite('asset', field, value) === undefined ? value : 'any|none';
		const lay = (tags: string) =>
			order.lay<[string, string]>(
				[
					['tags', tags],
					['media', 'video']
				],
				read,
				presence
			);
		const before = lay('any');
		const after = lay('none');
		expect(after.map((chip) => chip.key)).toEqual(before.map((chip) => chip.key));
		expect(after.map((chip) => chip.value)).toEqual(['none', 'video']);
	});

	it('draws a value seen again after it went at the end, and a repeat under its own key', () => {
		const order = new ChipOrder();
		order.lay<[string, string]>([['tags', 'a|b']], read);
		order.lay<[string, string]>([['tags', 'b']], read);
		const back = order.lay<[string, string]>([['tags', 'a|b|b']], read);
		expect(back.map((chip) => chip.key)).toEqual(['tags=b#0', 'tags=b#1', 'tags=a#0']);
	});
});
