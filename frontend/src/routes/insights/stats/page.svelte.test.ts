/* The Stats view, drawn from a fixture answer of the contracts' shape: the index of families, a panel
 * per table (figures, bars, days, each list whole) in its board family, a block under its floor as its
 * one line, the way back, the address a tile opens lit, the tabs and arrows kept on this screen, and
 * Copy putting a table on the clipboard as tab-separated text. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

import { goto } from '$app/navigation';
import { page } from '$app/state';

import { words } from '$lib/design/testing.svelte';
import type { InsightsBlock, InsightsPage, Place } from '$lib/components/insights/period';
import { toasts } from '$lib/shell/toasts.svelte';

const mocks = vi.hoisted(() => ({
	insights: vi.fn(),
	copyText: vi.fn(async (_text: string) => true as boolean)
}));

vi.mock('$lib/shell/clipboard', () => ({ copyText: mocks.copyText }));
vi.mock('$lib/api/client', async (importOriginal) => {
	const real = await importOriginal<Record<string, unknown>>();
	return {
		...real,
		api: {
			...(real.api as object),
			get: vi.fn(async (path: string, options?: { query?: Record<string, unknown> }) => {
				if (path === '/insights') return mocks.insights(options?.query);
				throw new Error(`not in this test: ${path}`);
			})
		}
	};
});

import Stats from './+page.svelte';

const HOUR = 3_600_000;

function plain(text: string) {
	return { text, kind: null, id: null, href: null, gone: false, rest: [], lead: '' };
}

type Figure = InsightsBlock['figures'][number];

function figure(label: string, value: number, said: string, over: Partial<Figure> = {}): Figure {
	return {
		label,
		value,
		unit: 'ms',
		said,
		hidden_part: 0,
		hidden_said: '',
		caption: [],
		defines: [],
		trend: [],
		...over
	};
}

function sent(fields: Pick<InsightsBlock, 'id' | 'title'> & Partial<InsightsBlock>): InsightsBlock {
	return {
		floor_reached: true,
		statements: [],
		figures: [],
		chart: null,
		calendar: null,
		lists: [],
		notes: [],
		...fields
	};
}

function answer(): InsightsPage {
	return {
		period: 'week',
		from: '2026-10-05',
		to: '2026-10-11',
		today_is_live: false,
		first_sentences: [],
		blocks: [
			sent({
				id: 'overview',
				title: 'Overview',
				statements: [[plain('You viewed 3 hours this week.')]],
				figures: [
					figure('Viewed', 3 * HOUR, '3 h', {
						trend: [HOUR, 2 * HOUR],
						defines: [plain('Time a file was in front of you.')]
					}),
					figure('Daily average', 30 * 60_000, '30 min', {
						hidden_part: 10 * 60_000,
						hidden_said: '10 min'
					})
				],
				chart: {
					kind: 'bars',
					unit: 'ms',
					today: null,
					caption: [],
					bars: [
						{
							label: 'Mon',
							said: '1 h',
							parts: [
								{ kind: 'video', value: HOUR, said: '1 h' },
								{ kind: 'image', value: 0, said: '' }
							]
						},
						{
							label: 'Tue',
							said: '2 h',
							parts: [
								{ kind: 'video', value: HOUR, said: '1 h' },
								{ kind: 'image', value: HOUR, said: '1 h' }
							]
						}
					]
				}
			}),
			sent({
				id: 'most_viewed',
				title: 'Most viewed',
				lists: [
					{
						title: 'People',
						rows: [
							{
								piece: { ...plain('Neve Alder'), kind: 'person', id: 'p1' },
								value: 2 * HOUR,
								unit: 'ms',
								cover: '/api/people/p1/cover',
								said: '2 h'
							},
							{
								piece: { ...plain('Oren Vale'), kind: 'person', id: 'p2' },
								value: HOUR,
								unit: 'ms',
								cover: null,
								said: '1 h'
							}
						]
					}
				]
			}),
			sent({
				id: 'opinions',
				title: 'Opinions',
				floor_reached: false,
				statements: [[plain('Not enough yet to say.')]]
			}),
			sent({ id: 'recaps', title: 'Recaps' })
		]
	};
}

let host: HTMLElement;
let drawn: Record<string, unknown> | undefined;

async function open(data: Place): Promise<HTMLElement> {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(Stats, { target: host, props: { data } });
	flushSync();
	for (let turn = 0; turn < 5; turn += 1) await tick();
	flushSync();
	return host;
}

const rows = (table: Element) =>
	[...table.querySelectorAll('tbody tr')].map((row) =>
		[...row.children].map((cell) => words(cell))
	);

const tables = (block: Element) => [...block.querySelectorAll('table')];

beforeEach(() => {
	mocks.insights.mockReset();
	mocks.copyText.mockClear();
	vi.mocked(goto).mockClear();
});

afterEach(() => {
	page.url = new URL('http://localhost/') as typeof page.url;
	if (drawn) unmount(drawn);
	drawn = undefined;
	host?.remove();
});

const panel = (screen: Element, id: string) => screen.querySelector(`article#${id}`) as HTMLElement;

describe('the Stats view', () => {
	it('asks for the period its address names, and draws a panel per table in its board family', async () => {
		mocks.insights.mockResolvedValue(answer());
		const screen = await open({ period: 'week', at: '2026-10-07' });
		expect(mocks.insights).toHaveBeenCalledWith({ period: 'week', at: '2026-10-07' });
		expect(
			[...screen.querySelectorAll('section.family')].map((one) => [
				one.id,
				[...one.querySelectorAll('article')].map((article) => article.id)
			])
		).toEqual([
			['family-viewing', ['overview-figures', 'overview-bars']],
			['family-people', ['most_viewed-person']],
			['family-organizing', ['opinions-words']]
		]);
		expect(panel(screen, 'most_viewed-person').dataset.family).toBe('people');
		expect(screen.querySelector('[data-block="recaps"]')).toBeNull();
	});

	it('indexes the families down the side, each with its count of tables', async () => {
		mocks.insights.mockResolvedValue(answer());
		const screen = await open({ period: 'week', at: null });
		const index = screen.querySelector('nav[aria-label="Stats sections"]') as HTMLElement;
		expect(
			[...index.querySelectorAll('a')].map((link) => [link.getAttribute('href'), words(link)])
		).toEqual([
			['#family-viewing', 'Viewing 2 tables'],
			['#family-people', 'People 1 table'],
			['#family-organizing', 'Organizing']
		]);
		expect(index.querySelector('[aria-current]')).toBeNull();
	});

	it('goes back to Insights at the period on screen, and keeps the crumb', async () => {
		mocks.insights.mockResolvedValue(answer());
		const screen = await open({ period: 'week', at: '2026-10-07' });
		const back = screen.querySelector('a.back') as HTMLAnchorElement;
		expect(words(back)).toBe('Insights');
		expect(back.getAttribute('href')).toBe('/insights?period=week&at=2026-10-07');
	});

	it("draws a block's figures large, their trend a bar per mark, what they count on the title's mark", async () => {
		mocks.insights.mockResolvedValue(answer());
		const screen = await open({ period: 'week', at: null });
		const figures = panel(screen, 'overview-figures');
		expect(words(figures.querySelector('h3'))).toBe('Overview');
		expect(words(figures.querySelector('.sentence'))).toBe('You viewed 3 hours this week.');
		const table = figures.querySelector('table') as HTMLTableElement;
		expect([...table.querySelectorAll('thead th')].map((th) => words(th))).toEqual([
			'What',
			'Figure',
			'Hidden',
			'Trend'
		]);
		expect(rows(table).map((row) => row.slice(0, 3))).toEqual([
			['Viewed', '3 h', ''],
			['Daily average', '30 min', '10 min']
		]);
		/* The trend: a bar per mark, named by Overview's own bars, walked by the arrows. */
		const spark = table.querySelector('[role="group"]') as HTMLElement;
		expect(spark.querySelectorAll('.column')).toHaveLength(2);
		spark.dispatchEvent(new FocusEvent('focus'));
		flushSync();
		expect(spark.getAttribute('aria-label')).toBe('Viewed, Trend: Mon, 1 h');
		spark.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
		flushSync();
		expect(spark.getAttribute('aria-label')).toBe('Viewed, Trend: Tue, 2 h');
		spark.querySelectorAll('.column')[0].dispatchEvent(new PointerEvent('pointerenter'));
		flushSync();
		expect(spark.querySelector('.pointed')).toBe(spark.querySelectorAll('.column')[0]);
		spark.querySelectorAll('.column')[0].dispatchEvent(new PointerEvent('pointerleave'));
		spark.dispatchEvent(new FocusEvent('blur'));
		flushSync();
		expect(spark.getAttribute('aria-label')).toBe('Viewed, Trend');

		/* What counts: held by a press, let go by Escape and by leaving. */
		const press = figures.querySelector<HTMLButtonElement>('button[aria-label="What counts"]')!;
		const bubble = () => document.querySelector('[role="tooltip"]');
		press.click();
		flushSync();
		await vi.waitFor(() => expect(bubble()).not.toBeNull());
		expect(words(bubble())).toContain('Viewed Time a file was in front of you.');
		press.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		flushSync();
		await vi.waitFor(() => expect(bubble()).toBeNull());
		press.click();
		flushSync();
		press.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		press.dispatchEvent(new FocusEvent('blur'));
		flushSync();
		await vi.waitFor(() => expect(bubble()).toBeNull());
		/* The definition is on the panel, not a column of the table. */
		expect(words(table)).not.toContain('Time a file was in front of you.');
	});

	it("draws a chart's bars over their table, under its block's name", async () => {
		mocks.insights.mockResolvedValue(answer());
		const screen = await open({ period: 'week', at: null });
		const bars = panel(screen, 'overview-bars');
		expect(words(bars.querySelector('.eyebrow'))).toBe('Overview');
		expect(words(bars.querySelector('h3'))).toBe('Each bar');
		expect(bars.querySelector('.chart .bar-chart')).not.toBeNull();
		expect(rows(bars.querySelector('table')!)).toEqual([
			['Mon', '1 h', '0 min'],
			['Tue', '1 h', '1 h']
		]);
	});

	it('draws each list whole and ranked, its picture and a bar as long as its share beside each name', async () => {
		mocks.insights.mockResolvedValue(answer());
		const screen = await open({ period: 'week', at: null });
		const people = panel(screen, 'most_viewed-person');
		const table = people.querySelector('table') as HTMLTableElement;
		expect(rows(table).map(([rank, , figure]) => [rank, figure])).toEqual([
			['1', '2 h'],
			['2', '1 h']
		]);
		expect([...table.querySelectorAll('tbody th a')].map((name) => words(name))).toEqual([
			'Neve Alder',
			'Oren Vale'
		]);
		expect(table.querySelector('tbody a')?.getAttribute('href')).toContain('p1');
		expect(
			[...table.querySelectorAll<HTMLElement>('.share')].map((bar) => bar.style.inlineSize)
		).toEqual(['100%', '50%']);
		/* A picture the browser could not draw is drawn as the letter. */
		const cover = table.querySelector('.cover') as HTMLElement;
		expect(cover).not.toBeNull();
		cover.dispatchEvent(new Event('error'));
		flushSync();
		expect(table.querySelector('.cover img')).toBeNull();
	});

	it('says a block under its floor in its one line, with no table and no Copy', async () => {
		mocks.insights.mockResolvedValue(answer());
		const screen = await open({ period: 'week', at: null });
		const opinions = panel(screen, 'opinions-words');
		expect(tables(opinions)).toHaveLength(0);
		expect(opinions.querySelector('button')).toBeNull();
		expect(words(opinions)).toContain('Not enough yet to say.');
	});

	it('copies a table as tab-separated text and says so, or says it could not', async () => {
		mocks.insights.mockResolvedValue(answer());
		const screen = await open({ period: 'week', at: null });
		const copy = panel(screen, 'most_viewed-person').querySelector<HTMLButtonElement>(
			'button[aria-label="Copy this table"]'
		)!;
		copy.click();
		await vi.waitFor(() => expect(mocks.copyText).toHaveBeenCalled());
		expect(mocks.copyText).toHaveBeenCalledWith(
			'Rank\tName\tFigure\n1\tNeve Alder\t2 h\n2\tOren Vale\t1 h'
		);
		await vi.waitFor(() => expect(toasts.items.map((one) => one.message)).toContain('Copied'));
		mocks.copyText.mockResolvedValueOnce(false);
		panel(screen, 'overview-figures')
			.querySelector<HTMLButtonElement>('button[aria-label="Copy this table"]')!
			.click();
		await vi.waitFor(() =>
			expect(toasts.items.map((one) => one.message)).toContain("That table couldn't be copied")
		);
		expect(mocks.copyText).toHaveBeenLastCalledWith(
			'What\tFigure\tHidden\tTrend\nViewed\t3 h\t\t1 h, 2 h\nDaily average\t30 min\t10 min\t'
		);
	});

	it('lights every panel of the block a tile opened it at, and hands the first focus', async () => {
		page.url = new URL('http://localhost/insights/stats?period=week#overview') as typeof page.url;
		mocks.insights.mockResolvedValue(answer());
		const screen = await open({ period: 'week', at: null });
		await vi.waitFor(() =>
			expect([...screen.querySelectorAll('article.lit')].map((one) => one.id)).toEqual([
				'overview-figures',
				'overview-bars'
			])
		);
		await vi.waitFor(() => expect(document.activeElement?.id).toBe('overview-figures'));
		expect(screen.querySelector('nav a[aria-current="location"]')?.getAttribute('href')).toBe(
			'#family-viewing'
		);
	});

	it('lights only the panel an address names, and a family by its own address', async () => {
		page.url = new URL('http://localhost/insights/stats#most_viewed-person') as typeof page.url;
		mocks.insights.mockResolvedValue(answer());
		let screen = await open({ period: 'week', at: null });
		await vi.waitFor(() => expect(document.activeElement?.id).toBe('most_viewed-person'));
		expect([...screen.querySelectorAll('article.lit')].map((one) => one.id)).toEqual([
			'most_viewed-person'
		]);
		unmount(drawn!);
		host.remove();
		page.url = new URL('http://localhost/insights/stats#family-organizing') as typeof page.url;
		screen = await open({ period: 'week', at: null });
		await tick();
		expect(screen.querySelectorAll('article.lit')).toHaveLength(0);
		expect(screen.querySelector('nav a[aria-current="location"]')?.getAttribute('href')).toBe(
			'#family-organizing'
		);
	});

	it('keeps its tabs and arrows on this screen', async () => {
		mocks.insights.mockResolvedValue(answer());
		const screen = await open({ period: 'week', at: '2026-10-07' });
		const tabs = [...screen.querySelectorAll('.periods a')];
		expect(tabs[3].getAttribute('href')).toBe('/insights/stats?period=month&at=2026-10-07');
		screen.querySelector<HTMLButtonElement>('button[aria-label="Earlier"]')?.click();
		expect(goto).toHaveBeenCalledWith('/insights/stats?period=week&at=2026-10-04');
	});
});
