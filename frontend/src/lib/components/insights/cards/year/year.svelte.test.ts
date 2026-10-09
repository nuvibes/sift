/* The year's own cards, each drawn on its own: the five files large, the first and last as a
 * strip, the first month against the last as two columns of pictures, how one visit went with
 * its pages' pictures, the top five as a podium a month, the year's days with the busiest named,
 * and the closing card with the top face and the top file. */
import { afterEach, describe, expect, it } from 'vitest';
import { mount, unmount, type Component } from 'svelte';

import type { components } from '$lib/api/schema';
import BeforeAfter from './BeforeAfter.svelte';
import Closing from './Closing.svelte';
import FirstLast from './FirstLast.svelte';
import Heatmap from './Heatmap.svelte';
import Mosaic from './Mosaic.svelte';
import Race from './Race.svelte';
import SessionPath from './SessionPath.svelte';
import { brightest, pictureOf, podiums, weekOf } from './pictures';

type Piece = components['schemas']['HistoryPiece'];
type Figure = components['schemas']['Figure'];
type Unit = Figure['unit'];

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
});

function draw<P extends Record<string, unknown>>(component: Component<P>, props: P): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(component, { target: host, props });
	return host;
}

function piece(text: string, over: Partial<Piece> = {}): Piece {
	return { text, kind: null, id: null, href: null, gone: false, rest: [], lead: '', ...over };
}

function row(name: string, id: string, value: number, unit: Unit = 'views', kind = 'asset') {
	const cover = kind === 'person' ? `/api/people/${id}/cover` : `/api/assets/${id}/thumb`;
	return { piece: piece(name, { kind, id, href: `/${kind}/${id}` }), value, unit, cover, said: '' };
}

function figure(label: string, value: number, unit: Unit, caption: Piece[] = []): Figure {
	return {
		label,
		value,
		unit,
		hidden_part: 0,
		caption,
		defines: [],
		said: '',
		hidden_said: '',
		trend: []
	};
}

const bar = (label: string, parts: [string, number][]) => ({
	label,
	said: '',
	parts: parts.map(([kind, value]) => ({ kind, value, said: '' }))
});

const sources = (drawn: HTMLElement, selector: string) =>
	[...drawn.querySelectorAll<HTMLImageElement>(`${selector} img`)].map((one) =>
		one.getAttribute('src')
	);

describe('the five files', () => {
	it('are drawn large, the first leading, each with its place and a way to it', () => {
		const rows = ['a', 'b', 'c', 'd', 'e', 'f'].map((id, n) => row(`${id}.mp4`, id, 10 - n));
		const drawn = draw(Mosaic, { rows });
		const tiles = drawn.querySelectorAll('.tile');
		expect(tiles).toHaveLength(5);
		expect([...tiles].map((one) => one.querySelector('.place')?.textContent)).toEqual([
			'1',
			'2',
			'3',
			'4',
			'5'
		]);
		expect(tiles[0].classList.contains('lead')).toBe(true);
		expect(tiles[0].querySelector('a')?.getAttribute('href')).toBe('/asset/a');
	});

	it('give a last file left alone the whole row', () => {
		const rows = ['a', 'b', 'c', 'd'].map((id, n) => row(`${id}.mp4`, id, 10 - n));
		const tiles = draw(Mosaic, { rows }).querySelectorAll('.tile');
		expect([...tiles].map((one) => one.classList.contains('alone'))).toEqual([
			false,
			false,
			false,
			true
		]);
	});
});

describe('the first and last', () => {
	it('are a strip, the first at the start and the last at the end, the seam under it', () => {
		const rows = [
			row('first.mp4', 'f1', 550, 'minute_of_day'),
			row('last.mp4', 'f2', 1380, 'minute_of_day')
		];
		const drawn = draw(FirstLast, { rows, figure: figure('Files imported', 142, 'count') });
		expect(sources(drawn, '.cover.start')).toEqual(['/api/assets/f1/thumb']);
		expect(sources(drawn, '.cover.end')).toEqual(['/api/assets/f2/thumb']);
		expect(drawn.querySelector('.about.start .which')?.textContent).toBe('First');
		expect(drawn.querySelector('.about.end .which')?.textContent).toBe('Last');
		expect(drawn.querySelector('.strip .track')).not.toBeNull();
		expect(drawn.querySelector('.seam')?.textContent).toContain('A lot happened in between');
		expect(drawn.querySelector('.seam .figure-card')?.textContent).toContain('Files imported');
	});
});

describe('the first month against the last', () => {
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

	it('stand as two columns, each its lead kind, its files and its bar', () => {
		const rows = [
			row('a.mp4', 'a', 5),
			row('b.mp4', 'b', 4),
			row('c.jpg', 'c', 9),
			row('d.jpg', 'd', 3)
		];
		const drawn = draw(BeforeAfter, { chart, rows });
		const months = drawn.querySelectorAll('.month');
		expect([...months].map((one) => one.querySelector('.month-name')?.textContent)).toEqual([
			'January',
			'December'
		]);
		expect([...months].map((one) => one.querySelector('.lead')?.textContent)).toEqual([
			'Videos',
			'Pictures'
		]);
		expect(sources(months[0] as HTMLElement, '.pictures')).toEqual([
			'/api/assets/a/thumb',
			'/api/assets/b/thumb'
		]);
		expect(sources(months[1] as HTMLElement, '.pictures')).toEqual([
			'/api/assets/c/thumb',
			'/api/assets/d/thumb'
		]);
		expect(months[0].querySelectorAll('.bar .part')).toHaveLength(2);
		expect(months[0].querySelector('.part')?.getAttribute('aria-label')).toMatch(/^Videos: /);
		expect(months[0].querySelector('.key')?.textContent).toContain('90%');
	});

	it('give the bar the pictures room where there are none', () => {
		const drawn = draw(BeforeAfter, { chart });
		expect(drawn.querySelector('.pictures')).toBeNull();
		expect(drawn.querySelectorAll('.bar.tall')).toHaveLength(2);
	});
});

describe('how one visit went', () => {
	it("draws each page's picture as its node, a dot for a place, and counts the pages between", () => {
		const step = (text: string, after: number, over: Partial<Piece> = {}) => ({
			piece: piece(text, over),
			after_ms: after
		});
		const path = {
			steps: [
				step('Browse', 0, { href: '/browse' }),
				step('Elina Sorrel', 60_000, { kind: 'person', id: 'p1', href: '/people/p1' }),
				step('harbour.mp4', 120_000, { kind: 'asset', id: 'f1', href: '/asset/f1' }),
				step('Insights', 600_000, { href: '/insights' })
			],
			more: 27
		};
		const drawn = draw(SessionPath, { path });
		expect(drawn.querySelector('.title')?.textContent).toBe('How one visit went');
		expect(sources(drawn, '.pictured')).toEqual(['/api/people/p1/cover', '/api/assets/f1/thumb']);
		const steps = drawn.querySelectorAll('.step');
		expect(steps[0].querySelector('.dot')).not.toBeNull();
		expect(drawn.querySelector('.gap')?.textContent).toContain('27 more pages');
	});
});

describe('the top five month by month', () => {
	it('stand on a podium a month, the first in the middle, a month with nobody a dot', () => {
		const rows = [
			row('Elina Sorrel', 'p1', 9, 'ms', 'person'),
			row('Cassia Lynn', 'p2', 8, 'ms', 'person'),
			row('Esme Wrenfield', 'p3', 7, 'ms', 'person')
		];
		const chart = {
			kind: 'bars' as const,
			unit: 'ms' as const,
			today: null,
			caption: [],
			bars: [
				bar('Jan', [
					['p1', 3],
					['p2', 5],
					['p3', 1]
				]),
				bar('Feb', [
					['p1', 0],
					['p2', 0],
					['p3', 0]
				])
			]
		};
		const drawn = draw(Race, { rows, chart });
		expect(drawn.querySelectorAll('.five .who')).toHaveLength(3);
		const [january, february] = drawn.querySelectorAll('.month');
		const steps = january.querySelectorAll('.step');
		expect([...steps].map((one) => one.className.match(/place-\d/)?.[0])).toEqual([
			'place-2',
			'place-1',
			'place-3'
		]);
		expect(sources(steps[1] as HTMLElement, '')).toEqual(['/api/people/p2/cover']);
		expect(sources(steps[0] as HTMLElement, '')).toEqual(['/api/people/p1/cover']);
		expect(february.querySelector('.none')).not.toBeNull();
		expect(february.querySelector('.podium')).toBeNull();
	});
});

describe("the year's days", () => {
	it('name the busiest day over the calendar', () => {
		const days = ['2025-03-13', '2025-03-14', '2025-03-15'].map((day, n) => ({
			day,
			value: [60_000, 7 * 3_600_000, 7 * 3_600_000][n],
			said: ''
		}));
		const drawn = draw(Heatmap, { calendar: { unit: 'ms' as const, days }, label: 'Viewed' });
		expect(drawn.querySelector('.best .day')?.textContent).toMatch(/14/);
		expect(drawn.querySelector('.best .time')?.textContent).toMatch(/7/);
		expect(drawn.querySelector('.heat-map')).not.toBeNull();
		// A few days fill less than the card: the name stands at the start.
		expect(drawn.querySelector<HTMLElement>('.best')?.style.getPropertyValue('--at')).toBe('0');
	});

	it("stand a year's busiest day over its own week", () => {
		const days = Array.from({ length: 365 }, (_, at) => ({
			day: new Date(Date.UTC(2025, 0, 1 + at)).toISOString().slice(0, 10),
			value: at === 300 ? 9 : 1,
			said: ''
		}));
		const drawn = draw(Heatmap, { calendar: { unit: 'ms' as const, days }, label: 'Viewed' });
		const best = drawn.querySelector<HTMLElement>('.best');
		// The best day is in the 44th week of 53, past the middle: the name reads back to its mark.
		expect(Number(best?.style.getPropertyValue('--at'))).toBeCloseTo(43.5 / 53, 5);
		expect(best?.classList.contains('flip')).toBe(true);
		expect(weekOf(days, '2025-01-01')).toEqual({ week: 0, weeks: 53, share: 0.5 / 53 });
		expect(weekOf([], '2025-01-01')).toBeNull();
	});
});

describe('the closing card', () => {
	const figures = [
		figure('Viewed', 3_600_000, 'ms'),
		figure('Top person', 3_600_000, 'ms', [
			piece('Elina Sorrel', { kind: 'person', id: 'p1', href: '/people/p1' })
		])
	];

	it('draws the top file as its band and the top face over it', () => {
		const drawn = draw(Closing, { figures, cover: '/api/assets/f1/thumb' });
		expect(sources(drawn, '.band')).toEqual(['/api/assets/f1/thumb']);
		expect(sources(drawn, '.top-face')).toEqual(['/api/people/p1/cover']);
		expect(drawn.querySelectorAll('.figures .tile')).toHaveLength(2);
	});

	it('draws no band where it has no picture', () => {
		const drawn = draw(Closing, { figures: [figure('Viewed', 3_600_000, 'ms')] });
		expect(drawn.querySelector('.pictures')).toBeNull();
	});
});

describe('the pictures', () => {
	it("are each thing's own, and none for a place or a thing gone", () => {
		expect(pictureOf(piece('x', { kind: 'site', id: 's/1' }))).toBe('/api/sites/s%2F1/cover');
		expect(pictureOf(piece('x', { kind: 'tag', id: 't1' }))).toBe('/api/tags/t1/cover');
		expect(pictureOf(piece('Browse', { href: '/browse' }))).toBeNull();
		expect(pictureOf(piece('x', { kind: 'person', id: 'p1', gone: true }))).toBeNull();
		expect(pictureOf(piece('x', { kind: 'wall', id: 'w1' }))).toBeNull();
	});

	it('find the brightest day, the earliest on a tie, and none in an empty year', () => {
		const days = [
			{ day: 'a', value: 1, said: '' },
			{ day: 'b', value: 5, said: '' },
			{ day: 'c', value: 5, said: '' }
		];
		expect(brightest(days)?.day).toBe('b');
		expect(brightest([{ day: 'a', value: 0, said: '' }])).toBeNull();
	});

	it("rank each month's podium, the year's order on a tie", () => {
		expect(
			podiums([
				{
					parts: [
						{ kind: 'a', value: 2 },
						{ kind: 'b', value: 2 },
						{ kind: 'c', value: 0 },
						{ kind: 'd', value: 9 }
					]
				}
			])
		).toEqual([['d', 'a', 'b']]);
	});
});
