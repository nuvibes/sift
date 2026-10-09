/* The card's designs, drawn by the one card: its ground of pictures, its family's colour, the two
 * beats, the faces, the marks, the skyline, the shares, the days, the figures side by side, the
 * voice's lines, and a board's tile by its shape. The year's own drawings are `year/`'s. */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import { parseColour } from '$lib/design/colour';
import { motion } from '$lib/shell/motion.svelte';

import type { RecapCard as Card } from '$lib/library/recaps.svelte';
import RecapCard from '$lib/components/insights/RecapCard.svelte';
import { FAMILIES, HUES, familyOf, groundOf, swatchOf } from './family';
import type { SessionPath } from './kinds';
import { keyWords } from './voice';

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

afterEach(() => clear());

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
		headline: [],
		context: [],
		wall: null,
		accent_hue: null,
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

type Extra = {
	size?: 'micro' | 'story' | 'tile';
	session?: SessionPath;
	shape?: '1x1' | '2x1' | '2x2' | '4x2' | '6x2';
	ground?: string[];
	family?: 'viewing' | 'people';
	accent?: number | null;
	build?: boolean;
	heading?: string;
};

function draw(one: Card, extra: Extra = {}) {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(RecapCard, { target: host, props: { card: one, build: false, ...extra } });
	flushSync();
	return host;
}

function clear(): void {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
}

const article = () => host.querySelector('.recap-card') as HTMLElement;

function person(name: string, id: string, cover: string | null = `/api/people/${id}/cover`) {
	return {
		piece: piece(name, { kind: 'person', id, href: `/people/${id}` }),
		value: 3_600_000,
		unit: 'ms' as const,
		cover,
		said: '1 h'
	};
}

const bar = (label: string, parts: [string, number][]) => ({
	label,
	said: '',
	parts: parts.map(([kind, value]) => ({ kind, value, said: '' }))
});

describe('a year card', () => {
	it('is the figure and its sentence until its pages arrive', () => {
		const drawn = draw(card('session', { figure: figure('Longest visit', 7_200_000, 'ms') }));
		expect(drawn.querySelector('.figure-card')).not.toBeNull();
		expect(drawn.querySelector('.art')).toBeNull();
	});

	it('is its sentence alone where a closing card has no figures', () => {
		const drawn = draw(card('closing', { statement: [piece('That was last week.')] }));
		expect(drawn.querySelector('.tally')).toBeNull();
		expect(drawn.textContent).toContain('That was last week.');
	});

	it("draws none of the year's pictures on a micro card", () => {
		const rows = [row('a.mp4', 'a', 3), row('b.mp4', 'b', 2)];
		const drawn = draw(card('mosaic', { rows }), { size: 'micro' });
		expect(drawn.querySelector('.art')).toBeNull();
		expect(drawn.querySelector('.ground')).toBeNull();
	});
});

describe('the ground', () => {
	it("is the deck's pictures, blurred under the family's shade, a full grid of them", () => {
		const ground = ['a', 'b', 'c', 'd', 'e'].map((id) => `/api/assets/${id}/thumb`);
		draw(card('headline'), { ground });
		// Five would leave a cell empty: four are drawn, two by two.
		expect(host.querySelectorAll('.ground img')).toHaveLength(4);
		expect(host.querySelector('.ground.two')).not.toBeNull();
		expect(host.querySelector('.ground .shade')).not.toBeNull();
	});

	it('leaves out a picture that does not load, and is gone when none does', () => {
		draw(card('headline'), { ground: ['/api/assets/a/thumb'] });
		expect(host.querySelector('.ground.one')).not.toBeNull();
		host.querySelector('.ground img')?.dispatchEvent(new Event('error'));
		flushSync();
		expect(host.querySelector('.ground')).toBeNull();
	});

	it("is the card's own file pictures where it is handed none, and none on a locked tile", () => {
		draw(card('top_file', { cover: '/api/assets/f1/thumb' }));
		expect(host.querySelector('.ground img')?.getAttribute('src')).toBe('/api/assets/f1/thumb');
		clear();
		draw(card('top_file', { hidden: true, statement: [] }), { ground: ['/api/assets/a/thumb'] });
		expect(host.querySelector('.ground')).toBeNull();
	});

	it("is read off a deck's cards: files only, each once, none of a locked tile, at most six", () => {
		const files = ['a', 'b', 'c', 'd', 'e', 'f', 'g'].map((id) => row(`${id}.mp4`, id, 1));
		expect(
			groundOf([
				card('top_person', { cover: '/api/people/p1/cover' }),
				card('top_file', { cover: '/api/assets/a/thumb' }),
				card('new_favourite', { hidden: true, cover: '/api/assets/z/thumb' }),
				card('mosaic', { rows: files })
			])
		).toEqual(['a', 'b', 'c', 'd', 'e', 'f'].map((id) => `/api/assets/${id}/thumb`));
	});
});

describe("a card's colour", () => {
	it("is its family's, by its kind or as it is handed, in the swatch nearest the top file's", () => {
		draw(card('top_five'));
		expect(article().dataset.family).toBe('people');
		expect(article().dataset.swatch).toBe('b');
		clear();
		draw(card('sift_did'), { family: 'viewing', accent: 245 });
		expect(article().dataset.family).toBe('viewing');
		expect(article().dataset.swatch).toBe('a');
		clear();
		draw(card('theater', { accent_hue: 330 } as Partial<Card>));
		expect(article().dataset.swatch).toBe('c');
		expect(familyOf('closing')).toBe('viewing');
	});

	it('takes the nearest swatch the short way round the circle, the middle with no colour', () => {
		expect(swatchOf('people', 350)).toBe('a');
		expect(swatchOf('people', 40)).toBe('c');
		expect(swatchOf('viewing', null)).toBe('b');
		expect(swatchOf('viewing', Number.NaN)).toBe('b');
	});

	it("names the hues the stylesheet's swatches are", () => {
		const css = readFileSync(resolve('src/app.css'), 'utf8');
		for (const family of FAMILIES) {
			(['a', 'b', 'c'] as const).forEach((swatch, at) => {
				const written = new RegExp(`--family-${family}-${swatch}: ([^;]+);`).exec(css)?.[1];
				expect(written, `${family}-${swatch}`).toBeDefined();
				const { r, g, b } = parseColour(written ?? '');
				const hue = oklchHue(r, g, b);
				const turn = Math.abs(hue - HUES[family][at]) % 360;
				expect(Math.min(turn, 360 - turn), `${family}-${swatch}`).toBeLessThanOrEqual(1);
			});
		}
	});
});

/* OKLCH's hue of an sRGB colour, each channel 0 to 1. */
function oklchHue(r: number, g: number, b: number): number {
	const linear = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
	const [R, G, B] = [linear(r), linear(g), linear(b)];
	const l = Math.cbrt(0.4122214708 * R + 0.5363325363 * G + 0.0514459929 * B);
	const m = Math.cbrt(0.2119034982 * R + 0.6806995451 * G + 0.1073969566 * B);
	const s = Math.cbrt(0.0883024619 * R + 0.2817188376 * G + 0.6299787005 * B);
	const a = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
	const bb = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
	return ((Math.atan2(bb, a) * 180) / Math.PI + 360) % 360;
}

describe('the two beats', () => {
	it('holds the number and its context for a beat, then lands them', () => {
		vi.useFakeTimers();
		try {
			draw(card('headline', { figure: figure('Viewed', 3_600_000, 'ms') }), { build: true });
			expect(article().dataset.built).toBe('false');
			expect(host.querySelector('.later.waiting')).not.toBeNull();
			vi.advanceTimersByTime(450);
			flushSync();
			expect(article().dataset.built).toBe('true');
			expect(host.querySelector('.later.waiting')).toBeNull();
		} finally {
			vi.useRealTimers();
		}
	});

	it('lands both together where motion is reduced', () => {
		const was = motion.preference;
		motion.preference = 'reduce';
		try {
			draw(card('headline', { figure: figure('Viewed', 3_600_000, 'ms') }), { build: true });
			expect(article().dataset.built).toBe('true');
		} finally {
			motion.preference = was;
		}
	});
});

describe('the pictures a card draws', () => {
	it('stands the top five as faces, the first large, the rest in a row with their places', () => {
		const rows = ['p1', 'p2', 'p3', 'p4', 'p5'].map((id) => person(`Name ${id}`, id));
		draw(card('top_five', { rows }));
		expect(host.querySelector('.podium .lead-face')).not.toBeNull();
		expect(host.querySelector('.podium .lead a')?.textContent).toBe('Name p1');
		expect([...host.querySelectorAll('.rest .place')].map((one) => one.textContent)).toEqual([
			'2',
			'3',
			'4',
			'5'
		]);
		clear();
		draw(card('top_five', { rows }), { size: 'tile', shape: '2x1' });
		expect(host.querySelector('.podium.flat')).not.toBeNull();
	});

	it('draws a list of Sites as their marks, and a list with no pictures as a list', () => {
		const site = (id: string) => ({
			...person(id, id, `/api/sites/${id}/cover`),
			piece: piece(id, { kind: 'site', id })
		});
		draw(card('top_site', { rows: [site('s1'), site('s2')] }));
		expect(host.querySelectorAll('.podium .avatar.mark')).toHaveLength(2);
		clear();
		draw(card('theater_files', { rows: [person('Nine up', 'w1', null)] }));
		expect(article().dataset.design).toBe('list');
		expect(host.querySelector('.ranked-list')).not.toBeNull();
	});

	it('stacks the shares by kind, the largest in the ink', () => {
		const chart = {
			kind: 'share' as const,
			unit: 'ms' as const,
			today: null,
			caption: [],
			bars: [
				bar('Viewed', [
					['video', 30],
					['image', 10],
					['gif', 0]
				])
			]
		};
		draw(card('headline', { chart }));
		const parts = [...host.querySelectorAll('.shares li')];
		expect(parts.map((one) => one.querySelector('.share')?.textContent)).toEqual(['75%', '25%']);
		expect(parts[0].classList.contains('most')).toBe(true);
	});

	it("draws a week's days as squares, the brightest ringed and named, a year's as its calendar", () => {
		const days = Array.from({ length: 7 }, (_, at) => ({
			day: `2026-10-0${at + 5}`,
			value: at === 3 ? 9 : at,
			said: ''
		}));
		draw(card('heatmap', { calendar: { unit: 'count', days } }), { size: 'tile', shape: '2x1' });
		expect(host.querySelectorAll('.days .square')).toHaveLength(7);
		expect(host.querySelectorAll('.days .square.best')).toHaveLength(1);
		expect(host.querySelector('.days .named')?.textContent).toMatch(/Oct 8/);
		const squares = host.querySelector('.days .squares') as HTMLElement;
		squares.focus();
		squares.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
		flushSync();
		expect(squares.getAttribute('aria-label')).toMatch(/Oct 11.*, 6$/);
		clear();
		const year = Array.from({ length: 365 }, (_, at) => ({
			day: new Date(Date.UTC(2025, 0, 1 + at)).toISOString().slice(0, 10),
			value: at % 3,
			said: ''
		}));
		draw(card('heatmap', { calendar: { unit: 'count', days: year } }));
		expect(article().dataset.design).toBe('own');
		expect(host.querySelector('.days')).toBeNull();
		clear();
		// A year's days on a card that is not the year's own are still its calendar.
		draw(card('headline', { calendar: { unit: 'count', days: year } }));
		expect(article().dataset.design).toBe('calendar');
		expect(host.querySelector('.heat, .heatmap, figure')).not.toBeNull();
	});

	it('sets its figures side by side, a figure of nothing left out', () => {
		const figures = [
			figure('Sessions', 10, 'count'),
			figure('Files in Theater', 0, 'count'),
			figure('Rated', 4, 'count')
		];
		draw(card('theater', { figures }));
		expect([...host.querySelectorAll('.tally dt')].map((one) => one.textContent)).toEqual([
			'Sessions',
			'Rated'
		]);
		expect([...host.querySelectorAll('.tally dd')].map((one) => one.textContent)).toEqual([
			'10',
			'4'
		]);
	});
});

describe('the Theater wall', () => {
	it('is the Saved Layout in miniature, a file in each cell, a cell with none left dark', () => {
		const wall = {
			rows: 2,
			cols: 3,
			slots: [
				{ row: 0, col: 0, row_span: 2, col_span: 2 },
				{ row: 0, col: 2, row_span: 1, col_span: 1 },
				{ row: 1, col: 2, row_span: 1, col_span: 1 }
			]
		};
		draw(
			card('theater_files', {
				wall,
				rows: [row('a.mp4', 'a', 4), row('b.mp4', 'b', 2)]
			} as Partial<Card>)
		);
		expect(article().dataset.design).toBe('wall');
		const cells = [...host.querySelectorAll('.wall .cell')] as HTMLElement[];
		expect(cells).toHaveLength(3);
		expect(cells[0].style.gridArea).toBe('1 / 1 / span 2 / span 2');
		expect(cells[0].querySelector('.avatar')).not.toBeNull();
		expect(cells[2].querySelector('.avatar')).toBeNull();
	});
});

describe('the words', () => {
	it("leads with the voice's headline and context, the fact sentence under them small", () => {
		const voiced = {
			headline: [piece('10 PM was your hour')],
			context: [
				piece('Your evenings usually peak closer to 9 PM. '),
				piece('Name p1', { kind: 'person', id: 'p1', href: '/people/p1' }),
				piece(' had 3 of them.')
			]
		} as Partial<Card>;
		draw(card('when', { ...voiced, figure: figure('Viewed', 3_600_000, 'ms') }));
		expect(host.querySelector('.headline')?.textContent).toBe('10 PM was your hour');
		expect(host.querySelector('.context')?.textContent?.replace(/\s+/g, ' ')).toBe(
			'Your evenings usually peak closer to 9 PM. Name p1 had 3 of them.'
		);
		// The numbers are the key words, and a name is the way to the person.
		expect([...host.querySelectorAll('.context strong')].map((one) => one.textContent)).toEqual([
			'9 PM',
			'3'
		]);
		expect(host.querySelector('.context a[href="/people/p1"]')).not.toBeNull();
		expect(host.querySelector('.said.defines')?.textContent?.trim()).toBe('A sentence.');
	});

	it('sets a long number a step smaller, then another', () => {
		draw(card('headline', { figure: { ...figure('Viewed', 1, 'ms'), said: '21 h 54 min' } }));
		expect(host.querySelector('.number.far')).not.toBeNull();
		clear();
		draw(card('headline', { figure: { ...figure('Viewed', 1, 'ms'), said: '22 min' } }));
		expect(host.querySelector('.number.wide')).not.toBeNull();
		clear();
		draw(card('headline', { figure: { ...figure('Viewed', 1, 'ms'), said: '7 h' } }));
		expect(host.querySelector('.number.wide, .number.far')).toBeNull();
	});
});

describe('a tile', () => {
	it('fills its holder, says its shape, and wears its title, except the smallest', () => {
		draw(card('top_five', { rows: [person('A', 'p1'), person('B', 'p2')] }), {
			size: 'tile',
			shape: '2x2',
			heading: 'People'
		});
		expect(article().dataset.shape).toBe('2x2');
		expect(host.querySelector('.head')?.textContent).toBe('People');
		clear();
		draw(card('sift_did', { figure: figure('Imported', 5, 'count') }), {
			size: 'tile',
			shape: '1x1',
			heading: 'Imported'
		});
		expect(host.querySelector('.head')).toBeNull();
		expect(host.querySelector('.art')).toBeNull();
		expect(host.querySelector('.foot')).toBeNull();
	});
});

describe('the key words', () => {
	it('are the numbers with their unit or their half of the day, in order', () => {
		expect(keyWords('5 files imported. Your usual Wednesday brings 40.')).toEqual([
			{ text: '5 files', key: true },
			{ text: ' imported. Your usual Wednesday brings ', key: false },
			{ text: '40', key: true },
			{ text: '.', key: false }
		]);
		expect(keyWords('No numbers here')).toEqual([{ text: 'No numbers here', key: false }]);
	});

	it('are none of a voice left empty, which falls back to the fact sentence', () => {
		draw(card('headline', { headline: [], context: [] } as Partial<Card>));
		expect(host.querySelector('.headline, .context')).toBeNull();
		expect(host.querySelector('.said.defines')).toBeNull();
	});
});
