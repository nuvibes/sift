/* The Insights screen, drawn from fixture answers of the contracts' shape.
 *
 * What is held here is what the screen decides rather than what a tile draws: it asks for the
 * period its address names, it lays the answer out as the board's tiles and no others (so What
 * Sift did is on an admin's board and absent from a guest's because the server left it out), the
 * first screen stands before the answer lands, the tabs run All, Day, Week, Month, Year, and the
 * arrows move the address.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

import { goto } from '$app/navigation';

import { words } from '$lib/design/testing.svelte';
import { calendarDay } from '$lib/shell/when';
import { toasts } from '$lib/shell/toasts.svelte';
import { DECK_WORDS } from '$lib/components/insights/words';
import type { InsightsBlock, InsightsPage, Place } from '$lib/components/insights/period';
import Insights from './+page.svelte';

const NO_RECAPS = { recaps: [], announced: null };
const mocks = vi.hoisted(() => ({
	insights: vi.fn(),
	recaps: vi.fn(),
	post: vi.fn(async () => undefined)
}));

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
				if (path === '/insights/recaps') return mocks.recaps();
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
			block('arrived', 'What was imported', '142 files imported this month.'),
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

/* The board's tiles, in its order. */
const slots = (root: HTMLElement) =>
	[...root.querySelectorAll('.board [data-slot]')].map((one) => one.getAttribute('data-slot'));
const tile = (root: HTMLElement, slot: string) =>
	root.querySelector<HTMLElement>(`.board [data-slot="${slot}"]`);

beforeEach(() => {
	mocks.insights.mockReset();
	mocks.recaps.mockReset();
	mocks.recaps.mockResolvedValue(NO_RECAPS);
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

	it('draws the head line and the first screen, then the families, with no section headings', async () => {
		mocks.insights.mockResolvedValue(page());
		const screen = await open({ period: 'month', at: null });
		expect(words(screen.querySelector('.lede'))).toBe(
			'Your library, your viewing and your organizing, in numbers. Nothing here leaves this device.'
		);
		expect(slots(screen)).toEqual([
			'headline',
			'viewed',
			'people',
			'top_file',
			'sites',
			'days',
			'imported',
			'visits',
			'sessions',
			'third',
			'by_kind',
			'when',
			'organizing',
			'opinions',
			'theater',
			'library'
		]);
		expect(words(tile(screen, 'headline'))).toContain('You viewed 41 hours this month so far.');
		expect(screen.querySelector('.board h2, .board h3')).toBeNull();
	});

	it('stands the first screen as skeletons before the answer, in the same places', async () => {
		mocks.insights.mockReturnValue(new Promise(() => {}));
		const screen = await open({ period: 'day', at: null });
		expect(slots(screen)).toEqual([
			'headline',
			'viewed',
			'people',
			'top_file',
			'sites',
			'when',
			'imported',
			'visits',
			'sessions',
			'third'
		]);
		expect(screen.querySelectorAll('.board .bone')).toHaveLength(10);
		expect(screen.querySelector('.board')?.getAttribute('aria-busy')).toBe('true');
	});

	it("opens Stats at the tile's own table, from anywhere on the tile and from its link", async () => {
		mocks.insights.mockResolvedValue(page());
		const screen = await open({ period: 'month', at: '2026-09-14' });
		const theater = tile(screen, 'theater');
		const link = theater?.querySelector('a.open');
		expect(link?.getAttribute('href')).toBe('/insights/stats?period=month&at=2026-09-14#theater');
		expect(words(link)).toBe('Theater in Stats');
		const pressed = vi.fn((event: Event) => event.preventDefault());
		link?.addEventListener('click', pressed);
		theater?.querySelector('article')?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		expect(pressed).toHaveBeenCalledTimes(1);
	});

	it('has no learning paths of its own: they are under Settings, in Get to know Sift', async () => {
		mocks.insights.mockResolvedValue(page());
		const screen = await open({ period: 'month', at: null });
		expect(screen.querySelector('[data-slot="path"]')).toBeNull();
		expect(screen.querySelector('a.open[href$="#path"]')).toBeNull();
	});

	it("draws What Sift did on an admin's answer and no such tile on a guest's", async () => {
		mocks.insights.mockResolvedValue(page({}, { admin: true }));
		const admin = await open({ period: 'month', at: null });
		expect(slots(admin)).toContain('machine');
		unmount(drawn!);
		drawn = undefined;
		host.remove();

		mocks.insights.mockResolvedValue(page());
		const guest = await open({ period: 'month', at: null });
		expect(slots(guest)).not.toContain('machine');
	});

	it('leads with the first sentences while the viewing is below its floor, and draws no empty tile', async () => {
		mocks.insights.mockResolvedValue(
			page({
				period: 'day',
				first_sentences: [[plain('142 files imported today.')]],
				blocks: [
					block('overview', 'Overview', 'Not enough yet to say.', false),
					block('when', 'When', 'Not enough yet to say.', false),
					block('arrived', 'What was imported', '142 files imported today.')
				]
			})
		);
		const screen = await open({ period: 'day', at: null });
		expect(words(tile(screen, 'headline'))).toContain('142 files imported today.');
		expect(words(tile(screen, 'viewed'))).toContain('Not enough yet to say.');
		expect(slots(screen)).toEqual([
			'headline',
			'viewed',
			'people',
			'top_file',
			'sites',
			'when',
			'imported',
			'visits',
			'sessions',
			'third',
			'library'
		]);
	});

	it("draws the empty library's sentence the server sends, as the board's headline", async () => {
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
		expect(words(tile(screen, 'headline'))).toContain(
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

	it('says Alongside under its floor while the viewing is past its own', async () => {
		const answer = page();
		answer.blocks.push(block('alongside', 'Alongside', 'Not enough yet to say.', false));
		mocks.insights.mockResolvedValue(answer);
		const screen = await open({ period: 'month', at: null });
		expect(words(tile(screen, 'alongside'))).toContain('Not enough yet to say.');
		expect(slots(screen)).toContain('opinions');
	});

	it('draws no Alongside while the whole viewing is below its floor', async () => {
		mocks.insights.mockResolvedValue(
			page({
				blocks: [
					block('overview', 'Overview', 'Not enough yet to say.', false),
					block('alongside', 'Alongside', 'Not enough yet to say.', false)
				]
			})
		);
		const screen = await open({ period: 'month', at: null });
		expect(slots(screen)).not.toContain('alongside');
	});
});

describe("the period's recap as cards", () => {
	const deckPress = (screen: HTMLElement, said: string) =>
		[...screen.querySelectorAll<HTMLButtonElement>('.periods button')].find(
			(one) => words(one) === said
		);

	it("opens the period's recap from beside Stats", async () => {
		mocks.insights.mockResolvedValue(page({ today_is_live: false }));
		mocks.recaps.mockResolvedValue({
			recaps: [
				{
					id: 'r-aug',
					period: 'month:2026-08',
					title: 'Your August',
					span: '',
					cards: 9,
					made_at: 1,
					seen_at: null
				},
				{
					id: 'r-sep',
					period: 'month:2026-09',
					title: 'Your September',
					span: '',
					cards: 9,
					made_at: 2,
					seen_at: null
				}
			],
			announced: null
		});
		const screen = await open({ period: 'month', at: '2026-09-14' });
		const press = deckPress(screen, DECK_WORDS.month.see);
		expect(press, 'no press for the deck').toBeDefined();
		expect(press?.parentElement?.lastElementChild?.textContent?.trim()).toBe('Stats');
		press?.click();
		await vi.waitFor(() => expect(goto).toHaveBeenCalledWith('/insights/recaps/r-sep'));
	});

	it('says there is no recap of the period yet, and when one is created', async () => {
		const shown = vi.spyOn(toasts, 'show');
		mocks.insights.mockResolvedValue(
			page({ period: 'week', from: '2026-09-14', to: '2026-09-20' })
		);
		const screen = await open({ period: 'week', at: null });
		deckPress(screen, DECK_WORDS.week.see)?.click();
		await vi.waitFor(() => expect(shown).toHaveBeenCalledWith(DECK_WORDS.week.none));
		expect(goto).not.toHaveBeenCalled();
		shown.mockRestore();
	});

	it("says today's recap on the day that holds today, and this day's on another", async () => {
		mocks.insights.mockResolvedValue(page({ period: 'day', from: '2026-09-14', to: '2026-09-14' }));
		const today = await open({ period: 'day', at: null });
		expect(deckPress(today, "See today's recap")).toBeDefined();
		unmount(drawn!);
		drawn = undefined;
		host.remove();

		mocks.insights.mockResolvedValue(
			page({ period: 'day', from: '2026-09-13', to: '2026-09-13', today_is_live: false })
		);
		const before = await open({ period: 'day', at: '2026-09-13' });
		expect(deckPress(before, "See this day's recap")).toBeDefined();
	});

	it('has no deck press on All, which has no recap', async () => {
		mocks.insights.mockResolvedValue(page({ period: 'all', from: '2026-01-01', to: '2026-09-30' }));
		const screen = await open({ period: 'all', at: null });
		expect(words(screen.querySelector('.periods .after'))).toBe('Stats');
	});
});
