/* The chart primitives: what each draws from its figures, the order and paint of its marks, the
 * words it says for them, and the looks that matter read off its compiled stylesheet. */

import { afterEach, describe, expect, it } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount, type Component } from 'svelte';

import { words } from '$lib/design/testing.svelte';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import { clock } from '$lib/shell/clock.svelte';
import { clockTime } from '$lib/shell/when';

import BarChart from './BarChart.svelte';
import barSource from './BarChart.svelte?raw';
import HeatMap from './HeatMap.svelte';
import heatSource from './HeatMap.svelte?raw';
import HourRing from './HourRing.svelte';
import ringSource from './HourRing.svelte?raw';
import RankedList from './RankedList.svelte';
import rankedSource from './RankedList.svelte?raw';
import ShareBar from './ShareBar.svelte';
import { BEHIND, LEVEL_PAINT, NEUTRAL, SHADES, levels, shares, type Series } from './series';

const said = (value: number) => `${value} min`;

const SERIES: Series[] = [
	{ id: 'video', label: 'Videos', paint: SHADES[0] },
	{ id: 'image', label: 'Pictures', paint: SHADES[1] },
	{ id: 'theater', label: 'Theater', paint: NEUTRAL }
];

let host: HTMLElement;
let drawn: Record<string, unknown> | undefined;

function draw<Props extends Record<string, unknown>>(
	component: Component<Props>,
	props: Props
): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(component as Component<Record<string, unknown>>, { target: host, props });
	flushSync();
	return host;
}

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	host?.remove();
	removeStyles();
});

describe('the palette', () => {
	it('is the three accent shades and one neutral, and never a status colour', () => {
		expect(SHADES).toEqual([
			'var(--sift-series-1)',
			'var(--sift-series-2)',
			'var(--sift-series-3)'
		]);
		for (const paint of [...SHADES, NEUTRAL, BEHIND, ...LEVEL_PAINT]) {
			expect(paint).not.toMatch(/--sift-(ok|warn|bad)|#/);
		}
	});

	it('shades a value against the person own figures, darkest for the least', () => {
		const level = levels([0, 10, 20, 30, 40, 50, 600]);
		expect([0, 10, 20, 40, 600].map(level)).toEqual([0, 1, 1, 2, 3]);
		expect(LEVEL_PAINT[1]).toBe(SHADES[2]);
		expect(LEVEL_PAINT[3]).toBe(SHADES[0]);
		/* One day alone is the most of what there is. */
		expect(levels([5])(5)).toBe(3);
	});

	it('says shares in whole percents that add up to exactly 100', () => {
		expect(shares([1, 1, 1])).toEqual([34, 33, 33]);
		expect(shares([29, 9, 3]).reduce((a, b) => a + b, 0)).toBe(100);
		expect(shares([0, 0])).toEqual([0, 0]);
	});
});

describe('a bar chart', () => {
	const bars = [
		{
			label: 'Mon',
			parts: [
				{ series: 'video', value: 20 },
				{ series: 'image', value: 10 }
			]
		},
		{
			label: 'Tue',
			parts: [
				{ series: 'theater', value: 60 },
				{ series: 'video', value: 0 }
			]
		},
		{ label: 'Wed', parts: [{ series: 'image', value: 15 }] }
	];

	it('stacks each bar in the series order, each part in its series paint', () => {
		const chart = draw(BarChart, { bars, series: SERIES, format: said, label: 'Viewed' });
		const columns = chart.querySelectorAll('.plot .column');
		expect(columns).toHaveLength(3);
		const parts = [...columns[0].querySelectorAll<HTMLElement>('.part')];
		expect(parts.map((part) => part.dataset.series)).toEqual(['video', 'image']);
		expect(parts[0].style.backgroundColor).toBe(SHADES[0]);
		expect((columns[1].querySelector('.stack') as HTMLElement).style.blockSize).toBe('100%');
		expect([...chart.querySelectorAll('.key li')].map((li) => words(li))).toEqual([
			'Videos',
			'Pictures',
			'Theater'
		]);
	});

	it('hatches the bar still counting, and says so in its table', () => {
		const chart = draw(BarChart, {
			bars,
			series: SERIES,
			today: 2,
			format: said,
			label: 'Viewed'
		});
		const today = chart.querySelector('.column.today');
		expect(today).toBe(chart.querySelectorAll('.plot .column')[2]);
		applyStyles(barSource, today);
		const part = today?.querySelector('.part') as HTMLElement;
		expect(getComputedStyle(part).backgroundImage).toContain('repeating-linear-gradient');
		const other = chart.querySelector('.column:not(.today) .part') as HTMLElement;
		expect(getComputedStyle(other).backgroundImage).not.toContain('repeating-linear-gradient');
		const rows = [...chart.querySelectorAll('tbody tr')].map((row) => words(row.children[0]));
		expect(rows).toEqual(['Mon', 'Tue', 'Wed, so far']);
	});

	it('stands every bar on one baseline, the ones under an unlabelled tick too', () => {
		/* Past sixteen bars only every other tick is labelled. An empty tick with no height of its
		   own would give its bar that room and stand it lower than its labelled neighbours. */
		const many = Array.from({ length: 24 }, (_one, hour) => ({
			label: String(hour).padStart(2, '0'),
			parts: [{ series: 'video', value: hour + 1 }]
		}));
		const chart = draw(BarChart, { bars: many, series: SERIES, format: said, label: 'Viewed' });
		const ticks = [...chart.querySelectorAll<HTMLElement>('.tick')];
		expect(ticks.filter((tick) => tick.textContent === '').length).toBeGreaterThan(0);
		applyStyles(barSource, ticks[0]);
		for (const tick of ticks) expect(getComputedStyle(tick).minBlockSize).toBe('1lh');
	});

	it('says the top of its scale, and hides its drawing behind the table', () => {
		const chart = draw(BarChart, { bars, series: SERIES, format: said, label: 'Viewed' });
		expect(words(chart.querySelector('.readout'))).toBe('Most 60 min');
		expect(chart.querySelector('.plot')?.getAttribute('aria-hidden')).toBe('true');
		expect(words(chart.querySelector('caption'))).toBe('Viewed');
	});
});

describe('a share bar', () => {
	it('draws one whole in its shares, leaves an empty part out, and says each in words', () => {
		const bar = draw(ShareBar, {
			parts: [
				{ series: SERIES[0], value: 30 },
				{ series: SERIES[1], value: 10 },
				{ series: SERIES[2], value: 0 }
			],
			format: said,
			label: 'Time viewed, by kind'
		});
		expect(bar.querySelectorAll('.whole .part')).toHaveLength(2);
		expect([...bar.querySelectorAll('.key li')].map((li) => words(li))).toEqual([
			'Videos 75% 30 min',
			'Pictures 25% 10 min'
		]);
	});
});

describe('a ranked list', () => {
	type NamedRow = { name: string; value: number; said: string };
	/* The list is generic over its row, so the test names the row it hands in. */
	const RankedPeople = RankedList<NamedRow>;
	const name = createRawSnippet((row: () => NamedRow) => ({
		render: () => `<a href="/people/x">${row().name}</a>`
	}));
	const rows: NamedRow[] = [
		{ name: 'Elina Sorrel', value: 60, said: '1 hour' },
		{ name: 'Cassia Lynn', value: 30, said: '30 minutes' },
		{ name: 'Ada Byron', value: 15, said: '15 minutes' },
		{ name: 'Bryn Calloway', value: 10, said: '10 minutes' },
		{ name: 'Dorian Halstead', value: 5, said: '5 minutes' },
		{ name: 'Esme Wrenfield', value: 1, said: '1 minute' }
	];

	it('draws the top five, each with a bar behind it as long as its share of the first', () => {
		const list = draw(RankedPeople, { title: 'People', rows, name });
		const items = list.querySelectorAll('li');
		expect(items).toHaveLength(5);
		const shareOf = (at: number) =>
			(items[at].querySelector('.share') as HTMLElement).style.inlineSize;
		expect(shareOf(0)).toBe('100%');
		expect(shareOf(1)).toBe('50%');
		applyStyles(rankedSource, items[0]);
		const share = getComputedStyle(items[0].querySelector('.share') as HTMLElement);
		expect(share.position).toBe('absolute');
		/* Painted from the palette, as every chart's marks are, never a colour of its own. */
		expect((items[0].querySelector('.share') as HTMLElement).style.backgroundColor).toBe(BEHIND);
		expect(words(items[0].querySelector('.figure'))).toBe('1 hour');
	});

	it('sets the name in full ink, the link colour only under the pointer', () => {
		const list = draw(RankedPeople, { title: 'People', rows, name });
		const item = list.querySelector('li') as HTMLElement;
		applyStyles(rankedSource, item);
		const link = item.querySelector('.name a') as HTMLElement;
		expect(getComputedStyle(link).color).toBe('var(--sift-ink)');
	});
});

describe('a heat-map', () => {
	it('puts each day in its weekday row and its week column, Monday at the top', () => {
		/* The first fixture day falls on a Wednesday. */
		const days = ['2026-09-02', '2026-09-03', '2026-09-07'].map((day, at) => ({
			day,
			value: at * 10
		}));
		const map = draw(HeatMap, { days, format: said, label: 'Viewed' });
		const cells = [...map.querySelectorAll<HTMLElement>('.grid .cell')];
		expect(cells.map((cell) => [cell.style.gridRow, cell.style.gridColumn])).toEqual([
			['3', '1'],
			['4', '1'],
			['1', '2']
		]);
		expect(cells[0].dataset.level).toBe('0');
		expect(cells[0].style.backgroundColor).toBe(LEVEL_PAINT[0]);
		expect(map.querySelectorAll('.key .cell')).toHaveLength(4);
		applyStyles(heatSource, map.querySelector('.heat-map'));
		expect(getComputedStyle(cells[0]).aspectRatio).toMatch(/^1( \/ 1)?$/);
	});
});

describe('the hour ring', () => {
	it('draws twenty-four segments from midnight and says every hour in its table', () => {
		const hours = Array.from({ length: 24 }, (_, hour) => (hour === 22 ? 60 : 0));
		const ring = draw(HourRing, { hours, format: said, label: 'Viewed' });
		const paint = (ring.querySelector('.ring') as HTMLElement).style.backgroundImage;
		expect(paint).toContain('conic-gradient');
		expect(paint).toContain(`${SHADES[0]} 330deg 344deg`);
		const rows = [...ring.querySelectorAll('tbody tr')];
		expect(rows).toHaveLength(24);
		expect([...rows[22].children].map((cell) => words(cell))).toEqual([
			clockTime('22:00'),
			'60 min'
		]);
	});

	it("marks the day's quarters and names each hour on the reader's clock", () => {
		const hours = Array.from({ length: 24 }, (_, hour) => (hour === 22 ? 60 : 0));
		const marks = () =>
			[...host.querySelectorAll('.mark')].map((one) => one.textContent?.trim() ?? '');
		try {
			clock.take('24');
			const ring = draw(HourRing, { hours, format: said, label: 'Viewed' });
			expect(marks()).toEqual(['00', '06', '12', '18']);
			expect(words(ring.querySelectorAll('tbody tr')[22].children[0])).toBe('22:00');
			unmount(drawn!);
			drawn = undefined;
			host.remove();

			clock.take('12');
			const twelve = draw(HourRing, { hours, format: said, label: 'Viewed' });
			expect(marks()).toEqual(['12 AM', '6 AM', '12 PM', '6 PM']);
			expect(words(twelve.querySelectorAll('tbody tr')[22].children[0])).toBe('10:00 PM');
		} finally {
			clock.reset();
		}
	});

	it('cuts its hole out of the ring, so the middle is whatever ground it stands on', () => {
		const ring = draw(HourRing, { hours: Array(24).fill(0), format: said, label: 'Viewed' });
		const middle = ring.querySelector('.middle') as HTMLElement;
		applyStyles(ringSource, middle);
		expect(getComputedStyle(middle).backgroundColor).toMatch(/^(|transparent|rgba\(0, 0, 0, 0\))$/);
		expect(ringSource).toMatch(/\.ring \{[^}]*mask-image: radial-gradient\(/);
	});
});
