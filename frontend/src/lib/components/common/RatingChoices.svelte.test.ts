import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';

import RatingChoices from './RatingChoices.svelte';
import { ratingScale } from '$lib/library/rating.svelte';
import { words } from '$lib/design/testing.svelte';

/*
 * The answers a rating can have, and the two things about them that are easy to get wrong.
 *
 * One star and a number per row, not a row of stars: ten glyphs per option would make reading a
 * matter of counting, telling four from five by comparing two nearly identical bars.
 *
 * Stored in, stored out. A rating is 1..10 on the wire whatever scale is on screen. The conversion
 * belongs to `ratingScale` alone, and this surface hands the number back, so a conversion missed
 * here would write the shown number into the library, where five would mean five out of ten for
 * ever after.
 */

let host: HTMLElement;

afterEach(() => {
	host?.remove();
	ratingScale.stars = 5;
});

function render(props: { rating: number | null; onpick: (rating: number | null) => void }) {
	host = document.createElement('div');
	document.body.append(host);
	mount(RatingChoices, { target: host, props });
	flushSync();
	return [...host.querySelectorAll<HTMLButtonElement>('.choice')];
}

describe('a row', () => {
	it('is one star and the number, not the whole scale drawn up to it', () => {
		ratingScale.stars = 10;
		const rows = render({ rating: null, onpick: vi.fn() });

		// Ten options, and exactly one glyph on each of them.
		expect(rows.filter((row) => row.querySelector('.row'))).toHaveLength(10);
		for (const row of rows) {
			if (!row.querySelector('.row')) continue;
			expect(row.querySelectorAll('.icon')).toHaveLength(1);
		}
	});

	it('says which number it is', () => {
		const rows = render({ rating: null, onpick: vi.fn() });
		const said = rows.map((row) => words(row)).filter((text) => text !== '');

		// Highest first, which is the order somebody reads a rating list in.
		expect(said.slice(0, 5)).toEqual(['5', '4', '3', '2', '1']);
	});
});

describe('what it hands back', () => {
	it('is the STORED number, not the one drawn', () => {
		// Three of five is six of ten. A conversion missed here writes 3 into the library.
		const onpick = vi.fn();
		const rows = render({ rating: null, onpick });

		rows.find((row) => words(row) === '3')?.click();

		expect(onpick).toHaveBeenCalledWith(6);
	});

	it('is the number itself on a ten-star account', () => {
		ratingScale.stars = 10;
		const onpick = vi.fn();
		const rows = render({ rating: null, onpick });

		rows.find((row) => words(row) === '3')?.click();

		expect(onpick).toHaveBeenCalledWith(3);
	});

	it('is null for the row that takes a rating away, which is not a zero', () => {
		/* There is no zero in the data at all: it is the value a caller would reach for to mean
		   "unrated", and stored it would sort and filter as a real rating for ever after. */
		const onpick = vi.fn();
		const rows = render({ rating: 6, onpick });

		rows.find((row) => words(row) === 'No rating')?.click();

		expect(onpick).toHaveBeenCalledWith(null);
	});

	it('offers nothing to take away when there is no rating', () => {
		const rows = render({ rating: null, onpick: vi.fn() });

		expect(rows.map((row) => words(row))).not.toContain('No rating');
	});
});

describe('the line above the row that takes a rating away', () => {
	/*
	 * As a `border-block-start` on the row itself, that row alone in the flyout would light as a
	 * square block ruled across the top while every other row lights as a rounded one. So it is
	 * an element between the rows, which is what a menu draws between groups anyway.
	 */
	it('is drawn as its own element, not as an edge on the row', () => {
		render({ rating: 6, onpick: vi.fn() });

		expect(host.querySelectorAll('.separator')).toHaveLength(1);
		const clearing = [...host.querySelectorAll('.choice')].at(-1);
		expect(clearing?.textContent?.trim()).toBe('No rating');
		// Nothing about that row differs from the rows above it. (The scoping class Svelte adds is
		// on every row, so only the written classes are read here.)
		expect(clearing?.classList.contains('clear')).toBe(false);
	});

	it('is absent when there is nothing to take away', () => {
		render({ rating: null, onpick: vi.fn() });

		expect(host.querySelectorAll('.separator')).toHaveLength(0);
	});
});
