/* The Insights screen, drawn from fixture answers of the contracts' shape.
 *
 * What is held here is what the screen decides rather than what a block draws: it asks for the
 * period its address names, it lays out the blocks the answer carries and no others (so What Sift
 * did is on an admin's page and absent from a guest's because the server left it out, not because
 * anything here knows who is looking), it opens on the first sentences only while the viewing is
 * below its floor, the tabs run All, Day, Week, Month, Year, and the arrows move the address.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

import { goto } from '$app/navigation';

import { words } from '$lib/design/testing.svelte';
import { calendarDay } from '$lib/shell/when';
import type { InsightsBlock, InsightsPage, Place } from '$lib/components/insights/period';
import Insights from './+page.svelte';

const mocks = vi.hoisted(() => ({ insights: vi.fn(), post: vi.fn(async () => undefined) }));

/* Only the one read this screen makes is the fixture; everything the recaps and the hint read
   answers "nothing yet", which each of them draws as nothing or as its own empty line. */
vi.mock('$lib/api/client', async (importOriginal) => {
	const real = await importOriginal<Record<string, unknown>>();
	return {
		...real,
		api: {
			...(real.api as object),
			get: vi.fn(async (path: string, options?: { query?: Record<string, unknown> }) => {
				if (path === '/insights') return mocks.insights(options?.query);
				if (path === '/insights/recaps') return { recaps: [], announced: null };
				if (path === '/settings/interface') return { state: {} };
				throw new Error(`not in this test: ${path}`);
			}),
			post: mocks.post
		}
	};
});

function plain(text: string) {
	return { text, kind: null, id: null, href: null, gone: false, rest: [], lead: '' };
}

function block(id: string, title: string, line: string, floor = true): InsightsBlock {
	return {
		id,
		title,
		floor_reached: floor,
		statements: [[plain(line)]],
		figures: [],
		chart: null,
		calendar: null,
		lists: [],
		notes: []
	};
}

const VIEWING: [string, string][] = [
	['overview', 'Overview'],
	['most_viewed', 'Most viewed'],
	['by_kind', 'By kind'],
	['theater', 'Theater'],
	['sittings', 'Visits'],
	['when', 'When'],
	['opinions', 'Opinions']
];

/** An answer for September, in the fixed order, with or without the block only an admin is sent. */
function page(over: Partial<InsightsPage> = {}, { admin = false } = {}): InsightsPage {
	return {
		period: 'month',
		from: '2026-09-01',
		to: '2026-09-30',
		today_is_live: true,
		first_sentences: [[plain('You viewed 41 hours this month so far.')]],
		blocks: [
			...VIEWING.map(([id, title]) => block(id, title, `${title} says so.`)),
			block('organizing', 'Organizing', 'You answered 120 questions on Organize this month.'),
			block('arrived', 'What arrived', '142 files arrived this month.'),
			...(admin
				? [block('machine', 'What Sift did', 'Sift worked on tasks for 14 hours this month.')]
				: []),
			{ ...block('recaps', 'Recaps', ''), statements: [] },
			block('path', 'Learning paths', "You've reached 4 of the 9 goals.")
		],
		...over
	};
}

let host: HTMLElement;
let drawn: Record<string, unknown> | undefined;

async function open(data: Place): Promise<HTMLElement> {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(Insights, { target: host, props: { data } });
	flushSync();
	for (let turn = 0; turn < 5; turn += 1) await tick();
	flushSync();
	return host;
}

/* Every block's heading and the recaps': a section's is an h2, a card's the band heading inside it. */
const headings = (root: HTMLElement) =>
	[...root.querySelectorAll('h2, h3')]
		.filter((h) => h.id.startsWith('insights-'))
		.map((h) => words(h));

beforeEach(() => {
	mocks.insights.mockReset();
	vi.mocked(goto).mockClear();
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	host?.remove();
});

describe('the Insights screen', () => {
	it('asks for the period its address names', async () => {
		mocks.insights.mockResolvedValue(page());
		await open({ period: 'month', at: '2026-09-14' });
		expect(mocks.insights).toHaveBeenCalledWith({ period: 'month', at: '2026-09-14' });
	});

	it('draws the head line and lays the blocks out as the story: the pair, the lists, the grid', async () => {
		mocks.insights.mockResolvedValue(page());
		const screen = await open({ period: 'month', at: null });
		expect(words(screen.querySelector('.lede'))).toBe(
			'Your library, your viewing and your organizing, in numbers. Nothing here leaves this device.'
		);
		/* The first sentences are each a figure on a card, so past the floor they are not repeated. */
		expect(screen.querySelector('.statements.lead')).toBeNull();
		expect(headings(screen).slice(0, 9)).toEqual([
			'Overview',
			'By kind',
			'When',
			'Most viewed',
			'Theater',
			'Visits',
			'Opinions',
			'Organizing',
			'What arrived'
		]);
		const inGroup = (selector: string) =>
			[...screen.querySelectorAll(`${selector} [data-block]`)].map((one) =>
				one.getAttribute('data-block')
			);
		expect(inGroup('.group.pair')).toEqual(['by_kind', 'when']);
		expect(inGroup('.group.row').slice(0, 5)).toEqual([
			'theater',
			'sittings',
			'opinions',
			'organizing',
			'arrived'
		]);
	});

	it('has no learning paths of its own: they are under Settings, in Get to know Sift', async () => {
		mocks.insights.mockResolvedValue(page());
		const screen = await open({ period: 'month', at: null });
		expect(headings(screen)).not.toContain('Learning paths');
		expect(screen.querySelector('[data-block="path"]')).toBeNull();
	});

	it("draws What Sift did on an admin's answer and has no such heading on a guest's", async () => {
		mocks.insights.mockResolvedValue(page({}, { admin: true }));
		const admin = await open({ period: 'month', at: null });
		expect(headings(admin)).toContain('What Sift did');
		unmount(drawn!);
		drawn = undefined;
		host.remove();

		mocks.insights.mockResolvedValue(page());
		const guest = await open({ period: 'month', at: null });
		expect(headings(guest)).not.toContain('What Sift did');
		expect(guest.querySelector('[data-block="machine"]')).toBeNull();
	});

	it('opens on the first sentences while the viewing is below its floor, and draws no empty block', async () => {
		mocks.insights.mockResolvedValue(
			page({
				first_sentences: [[plain('142 files arrived today.')]],
				blocks: [
					block('overview', 'Overview', 'Not enough yet to say.', false),
					block('when', 'When', 'Not enough yet to say.', false),
					block('arrived', 'What arrived', '142 files arrived today.')
				]
			})
		);
		const screen = await open({ period: 'day', at: null });
		expect(words(screen.querySelector('.statements.lead'))).toBe('142 files arrived today.');
		expect(screen.querySelector('[data-block="when"]')).toBeNull();
		expect(headings(screen)).toContain('What arrived');
	});

	it("draws the empty library's sentence the server sends, as the page's first sentence", async () => {
		mocks.insights.mockResolvedValue(
			page({
				first_sentences: [
					[
						plain(
							'Insights start once you have viewed a few things. Sift is keeping count from today.'
						)
					]
				],
				blocks: [block('overview', 'Overview', 'Not enough yet to say.', false)]
			})
		);
		const screen = await open({ period: 'week', at: null });
		expect(words(screen.querySelector('.statements.lead'))).toBe(
			'Insights start once you have viewed a few things. Sift is keeping count from today.'
		);
	});

	it('draws the period tabs as links, the current one marked', async () => {
		mocks.insights.mockResolvedValue(page());
		const screen = await open({ period: 'month', at: '2026-09-14' });
		const tabs = [...screen.querySelectorAll('.periods a')];
		expect(tabs.map((tab) => words(tab))).toEqual(['All', 'Day', 'Week', 'Month', 'Year']);
		expect(tabs[2].getAttribute('href')).toBe('/insights?period=week&at=2026-09-14');
		expect(screen.querySelector('.periods a[aria-current="page"]')?.textContent).toContain('Month');
	});

	it('moves the address a period back, and has no later while today is inside this one', async () => {
		mocks.insights.mockResolvedValue(page());
		const screen = await open({ period: 'month', at: null });
		const earlier = screen.querySelector<HTMLButtonElement>('button[aria-label="Earlier"]');
		const later = screen.querySelector<HTMLButtonElement>('button[aria-label="Later"]');
		expect(later?.disabled).toBe(true);
		earlier?.click();
		expect(goto).toHaveBeenCalledWith('/insights?period=month&at=2026-08-31');
	});

	it('moves the address a period on from a period that has ended', async () => {
		mocks.insights.mockResolvedValue(page({ today_is_live: false }));
		const screen = await open({ period: 'month', at: '2026-09-14' });
		screen.querySelector<HTMLButtonElement>('button[aria-label="Later"]')?.click();
		expect(goto).toHaveBeenCalledWith('/insights?period=month&at=2026-10-01');
	});

	it('says the days of All with no arrows, since All is the whole record', async () => {
		mocks.insights.mockResolvedValue(page({ period: 'all', from: '2026-01-01', to: '2026-09-30' }));
		const screen = await open({ period: 'all', at: null });
		expect(screen.querySelector('button[aria-label="Earlier"]')).toBeNull();
		expect(words(screen.querySelector('.periods .days'))).toBe(
			`${calendarDay('2026-01-01')} \u2014 ${calendarDay('2026-09-30')}`
		);
	});

	it('opens Stats at the same period from the end of the row, after the arrows', async () => {
		mocks.insights.mockResolvedValue(page());
		const screen = await open({ period: 'month', at: '2026-09-14' });
		const row = screen.querySelector('.periods') as HTMLElement;
		const stats = [...row.querySelectorAll('button')].find((one) => words(one) === 'Stats');
		expect(stats, 'no Stats press').toBeDefined();
		expect(row.lastElementChild?.contains(stats as Node), 'Stats is not last').toBe(true);
		stats?.click();
		expect(goto).toHaveBeenCalledWith('/insights/stats?period=month&at=2026-09-14');
	});
});
