/* The year's own cards, each drawn by the one card at story size: the five files, the first and
 * last, the first and last month, the top five month by month, the year's days, the longest
 * session's pages and the closing card's six figures. A micro card draws none of them. */
import { afterEach, describe, expect, it } from 'vitest';
import { mount, unmount } from 'svelte';

import type { RecapCard as Card } from '$lib/library/recaps.svelte';
import RecapCard from '$lib/components/insights/RecapCard.svelte';
import type { SessionPath } from './kinds';

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
});

function piece(text: string, over: Partial<Card['statement'][number]> = {}) {
	return { text, kind: null, id: null, href: null, gone: false, rest: [], lead: '', ...over };
}

type Figure = NonNullable<Card['figure']>;

function figure(label: string, value: number, unit: Figure['unit'], caption = ''): Figure {
	return {
		label,
		value,
		unit,
		hidden_part: 0,
		caption: caption ? [piece(caption, { kind: 'person', id: 'p1' })] : [],
		defines: [],
		said: '',
		hidden_said: '',
		trend: []
	};
}

function row(name: string, id: string, value: number, unit: Figure['unit'] = 'views') {
	return {
		piece: piece(name, { kind: 'asset', id, href: `/asset/${id}` }),
		value,
		unit,
		cover: `/api/assets/${id}/thumb`,
		said: ''
	};
}

function card(kind: Card['kind'], over: Partial<Card> = {}): Card {
	return {
		id: kind,
		kind,
		statement: [piece('A sentence.')],
		figure: null,
		cover: null,
		chart: null,
		rows: [],
		calendar: null,
		figures: [],
		hidden_things: [],
		hidden: false,
		...over
	};
}

function draw(one: Card, extra: { size?: 'micro' | 'story'; session?: SessionPath } = {}) {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(RecapCard, { target: host, props: { card: one, ...extra } });
	return host;
}

const bar = (label: string, parts: [string, number][]) => ({
	label,
	said: '',
	parts: parts.map(([kind, value]) => ({ kind, value, said: '' }))
});

describe('the five files', () => {
	it('stand side by side, the first the lead tile, each named as a way to it', () => {
		const rows = ['a', 'b', 'c', 'd', 'e', 'f'].map((id, n) => row(`${id}.mp4`, id, 10 - n));
		const drawn = draw(card('mosaic', { rows }));
		const tiles = drawn.querySelectorAll('.mosaic .tile');
		expect(tiles).toHaveLength(5);
		expect(tiles[0].classList.contains('lead')).toBe(true);
		expect(tiles[0].querySelector('a')?.getAttribute('href')).toBe('/asset/a');
		expect(drawn.querySelector('.ranked-list')).toBeNull();
	});
});

describe('the first and last', () => {
	it('are split by the seam, with the files that arrived between as its figure', () => {
		const rows = [
			row('first.mp4', 'f1', 550, 'minute_of_day'),
			row('last.mp4', 'f2', 1380, 'minute_of_day')
		];
		const drawn = draw(card('first_last', { rows, figure: figure('Files arrived', 142, 'count') }));
		const ends = drawn.querySelectorAll('.first-last .end');
		expect([...ends].map((one) => one.querySelector('.which')?.textContent)).toEqual([
			'First',
			'Last'
		]);
		expect(drawn.querySelector('.seam')?.textContent).toContain('A lot happened in between');
		expect(drawn.querySelector('.seam .figure-card')?.textContent).toContain('Files arrived');
		// The figure is in the seam, not a second time at the foot.
		expect(drawn.querySelectorAll('.figure-card')).toHaveLength(1);
	});
});

describe('the first and last month', () => {
	it('are two bars of the same kinds, each named by its month', () => {
		const chart = {
			kind: 'share' as const,
			unit: 'ms' as const,
			today: null,
			caption: [],
			bars: [
				bar('January', [
					['video', 9],
					['image', 1]
				]),
				bar('December', [
					['video', 2],
					['image', 8]
				])
			]
		};
		const drawn = draw(card('before_after', { chart }));
		expect([...drawn.querySelectorAll('.month-name')].map((one) => one.textContent)).toEqual([
			'January',
			'December'
		]);
		expect(drawn.querySelectorAll('.month .share-bar')).toHaveLength(2);
		expect(drawn.querySelector('.hour-ring')).toBeNull();
	});
});

describe('the top five month by month', () => {
	it('is a still table of places, a month with no time a dot', () => {
		const rows = [row('Elina Sorrel', 'p1', 9, 'ms'), row('Cassia Lynn', 'p2', 6, 'ms')].map(
			(one) => ({ ...one, piece: { ...one.piece, kind: 'person' } })
		);
		const chart = {
			kind: 'bars' as const,
			unit: 'ms' as const,
			today: null,
			caption: [],
			bars: [
				bar('Jan', [
					['p1', 4],
					['p2', 2]
				]),
				bar('Feb', [
					['p1', 0],
					['p2', 3]
				]),
				bar('Mar', [
					['p1', 5],
					['p2', 5]
				])
			]
		};
		const drawn = draw(card('race', { rows, chart }));
		const cells = [...drawn.querySelectorAll('tbody tr')].map((tr) =>
			[...tr.querySelectorAll('td')].map((td) => td.textContent)
		);
		// A tie stands in the year's order.
		expect(cells).toEqual([
			['1', '\u00b7', '1'],
			['2', '1', '2']
		]);
		expect([...drawn.querySelectorAll('thead th[abbr]')].map((th) => th.textContent)).toEqual([
			'J',
			'F',
			'M'
		]);
	});
});

describe("the year's days", () => {
	it('are the calendar of the Insights page', () => {
		const days = Array.from({ length: 14 }, (_, n) => ({
			day: `2025-01-${String(n + 1).padStart(2, '0')}`,
			value: n * 60_000,
			said: ''
		}));
		const drawn = draw(
			card('heatmap', { calendar: { unit: 'ms', days }, figure: figure('Viewed', 780_000, 'ms') })
		);
		expect(drawn.querySelectorAll('.heat .cell').length).toBeGreaterThanOrEqual(14);
	});
});

describe('how one session went', () => {
	const step = (text: string, after: number, href: string | null = null) => ({
		piece: piece(text, href ? { href } : { kind: 'person', id: 'p1' }),
		after_ms: after
	});

	it('draws its pages as a path, the pages between counted before the last two', () => {
		const session = {
			steps: [
				step('Browse', 0, '/browse'),
				step('Elina Sorrel', 60_000),
				step('Settings', 120_000, '/settings'),
				step('Organize', 3_600_000, '/organize'),
				step('Insights', 4_000_000, '/insights')
			],
			more: 3
		};
		const drawn = draw(card('session', { figure: figure('Longest visit', 7_200_000, 'ms') }), {
			session
		});
		expect(drawn.querySelector('.session .title')?.textContent).toBe('How one session went');
		const items = [...drawn.querySelectorAll('.path li')].map((li) => li.textContent?.trim());
		expect(items[0]).toContain('Start');
		expect(items[3]).toBe('3 more pages');
		expect(drawn.querySelector('.path a[href="/settings"]')).not.toBeNull();
		expect(drawn.querySelectorAll('.path .node')).toHaveLength(5);
	});

	it('is the figure and its sentence until its pages arrive', () => {
		const drawn = draw(card('session', { figure: figure('Longest visit', 7_200_000, 'ms') }));
		expect(drawn.querySelector('.session')).toBeNull();
		expect(drawn.querySelector('.figure-card')).not.toBeNull();
	});
});

describe('the closing card', () => {
	it('sums the year up in its figures, each thing named as a way to it', () => {
		const figures = [
			figure('Viewed', 41 * 3_600_000, 'ms'),
			figure('Files arrived', 142, 'count'),
			figure('Top person', 6 * 3_600_000, 'ms', 'Elina Sorrel'),
			figure('Sessions', 212, 'count')
		];
		const drawn = draw(card('closing', { statement: [piece('That was 2025.')], figures }));
		const tiles = drawn.querySelectorAll('.summary .tile');
		expect([...tiles].map((one) => one.querySelector('.label')?.textContent)).toEqual([
			'Viewed',
			'Files arrived',
			'Top person',
			'Sessions'
		]);
		// The first spans the card, and the last where it stands alone on its row.
		expect([...tiles].map((one) => one.classList.contains('wide'))).toEqual([
			true,
			false,
			false,
			true
		]);
		expect(tiles[2].querySelector('.named')?.textContent?.trim()).toBe('Elina Sorrel');
		expect(drawn.textContent).toContain('That was 2025.');
	});

	it('is its sentence alone where it has no figures', () => {
		const drawn = draw(card('closing', { statement: [piece('That was last week.')] }));
		expect(drawn.querySelector('.summary')).toBeNull();
	});
});

describe('a micro card', () => {
	it("draws none of the year's pictures", () => {
		const rows = [row('a.mp4', 'a', 3), row('b.mp4', 'b', 2)];
		const drawn = draw(card('mosaic', { rows }), { size: 'micro' });
		expect(drawn.querySelector('.mosaic')).toBeNull();
	});
});
