/* A recap card as a picture: what it says is the card's own words, and what it is called. The
 * drawing itself needs a canvas, which the test browser has none of, so it is checked in a real
 * browser. */
import { describe, expect, it } from 'vitest';

import type { RecapCard } from '$lib/library/recaps.svelte';

import { shareName, shareOf } from './share-card';

const piece = (text: string) => ({
	text,
	kind: null,
	id: null,
	href: null,
	gone: false,
	rest: [],
	lead: ''
});

const CARD: RecapCard = {
	id: 'headline',
	kind: 'headline',
	statement: [piece('You viewed 19 hours '), piece('last week.')],
	figure: {
		label: 'Viewed',
		value: 68_400_000,
		unit: 'ms',
		hidden_part: 0,
		caption: [],
		said: '19 h',
		hidden_said: '',
		trend: []
	},
	cover: '/api/people/p1/cover',
	rows: [],
	chart: null,
	hidden_things: [],
	hidden: false
};

describe('a picture of a recap card', () => {
	it("says the card's own words and figure, and nothing composed", () => {
		expect(shareOf(CARD, 'Your week', '1 of 11', 'September 21 to 27, 2026')).toEqual({
			heading: 'Your week',
			place: '1 of 11',
			span: 'September 21 to 27, 2026',
			label: 'Viewed',
			figure: '19 h',
			statement: 'You viewed 19 hours last week.',
			cover: '/api/people/p1/cover'
		});
	});

	it('is named for the recap and the card, safe as a file name', () => {
		expect(shareName('week:2026-W39', 0)).toBe('recap-week-2026-W39-1.png');
	});
});
