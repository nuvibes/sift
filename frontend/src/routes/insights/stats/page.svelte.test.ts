/* The Stats view, drawn from a fixture answer of the contracts' shape: every block in the page's
 * order as tables (figures, bars, days, each list whole), a block under its floor as its one line,
 * the tabs and arrows kept on this screen, and Copy putting a table on the clipboard as tab-separated
 * text. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

import { goto } from '$app/navigation';

import { words } from '$lib/design/testing.svelte';
import type { InsightsBlock, InsightsPage, Place } from '$lib/components/insights/period';
import { toasts } from '$lib/shell/toasts.svelte';

const mocks = vi.hoisted(() => ({
	insights: vi.fn(),
	copyText: vi.fn(async (_text: string) => true)
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
								cover: null,
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
	if (drawn) unmount(drawn);
	drawn = undefined;
	host?.remove();
});

describe('the Stats view', () => {
	it('asks for the period its address names, and draws every block in order but the recaps', async () => {
		mocks.insights.mockResolvedValue(answer());
		const screen = await open({ period: 'week', at: '2026-10-07' });
		expect(mocks.insights).toHaveBeenCalledWith({ period: 'week', at: '2026-10-07' });
		expect(
			[...screen.querySelectorAll('section[data-block]')].map((one) =>
				one.getAttribute('data-block')
			)
		).toEqual(['overview', 'most_viewed', 'opinions']);
	});

	it("draws a block's figures as rows: the server's words, the hidden part, the trend, what counts", async () => {
		mocks.insights.mockResolvedValue(answer());
		const screen = await open({ period: 'week', at: null });
		const overview = screen.querySelector('[data-block="overview"]') as HTMLElement;
		const [figures, bars] = tables(overview);
		expect([...figures.querySelectorAll('thead th')].map((th) => words(th))).toEqual([
			'What',
			'Figure',
			'Hidden',
			'Trend',
			'What counts'
		]);
		expect(rows(figures)).toEqual([
			['Viewed', '3 h', '', '1 h, 2 h', 'Time a file was in front of you.'],
			['Daily average', '30 min', '10 min', '', '']
		]);
		/* The chart's bars open, a kind to a column. */
		expect(words(figures.querySelector('tbody td:last-child p')), 'not a sentence').toBe(
			'Time a file was in front of you.'
		);
		expect(rows(bars)).toEqual([
			['Mon', '1 h', '0 min'],
			['Tue', '1 h', '1 h']
		]);
		/* No sentence past the floor: the story is Insights'. */
		expect(overview.textContent).not.toContain('You viewed 3 hours this week.');
	});

	it('draws each list whole, ranked, the name the way to the thing', async () => {
		mocks.insights.mockResolvedValue(answer());
		const screen = await open({ period: 'week', at: null });
		const [people] = tables(screen.querySelector('[data-block="most_viewed"]') as HTMLElement);
		expect(rows(people)).toEqual([
			['1', 'Neve Alder', '2 h'],
			['2', 'Oren Vale', '1 h']
		]);
		expect(people.querySelector('tbody a')?.getAttribute('href')).toContain('p1');
	});

	it('says a block under its floor in its one line, with no table', async () => {
		mocks.insights.mockResolvedValue(answer());
		const screen = await open({ period: 'week', at: null });
		const opinions = screen.querySelector('[data-block="opinions"]') as HTMLElement;
		expect(tables(opinions)).toHaveLength(0);
		expect(words(opinions)).toContain('Not enough yet to say.');
	});

	it('copies a table as tab-separated text and says so', async () => {
		mocks.insights.mockResolvedValue(answer());
		const screen = await open({ period: 'week', at: null });
		const [people] = tables(screen.querySelector('[data-block="most_viewed"]') as HTMLElement);
		(people.querySelector('caption button') as HTMLButtonElement).click();
		await vi.waitFor(() => expect(mocks.copyText).toHaveBeenCalled());
		expect(mocks.copyText).toHaveBeenCalledWith(
			'Rank\tName\tFigure\n1\tNeve Alder\t2 h\n2\tOren Vale\t1 h'
		);
		await vi.waitFor(() => expect(toasts.items.map((one) => one.message)).toContain('Copied'));
	});

	it('keeps its tabs and arrows on this screen, and Insights a crumb away at the same period', async () => {
		mocks.insights.mockResolvedValue(answer());
		const screen = await open({ period: 'week', at: '2026-10-07' });
		const tabs = [...screen.querySelectorAll('.periods a')];
		expect(tabs[3].getAttribute('href')).toBe('/insights/stats?period=month&at=2026-10-07');
		screen.querySelector<HTMLButtonElement>('button[aria-label="Earlier"]')?.click();
		expect(goto).toHaveBeenCalledWith('/insights/stats?period=week&at=2026-10-04');
	});
});
