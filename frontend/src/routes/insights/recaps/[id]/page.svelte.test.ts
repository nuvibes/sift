/* One recap, a run of cards down the page.
 *
 * What is held: the cards are a story read one at a time in the order sent, the closing card last,
 * Later and Earlier turning them; under it is the way to the same period on Insights; a card's
 * figure is drawn large with its sentence under it; a
 * locked tile is drawn as one; the placeholder mode's one line is said; and an address with no recap
 * behind it says so without saying why (the same answer for a recap a locked vault leaves out).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import { ApiError } from '$lib/api/client';

import source from './+page.svelte?raw';

const server = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: server.get, post: server.post }
}));
vi.mock('$app/navigation', () => ({ goto: vi.fn(), replaceState: vi.fn() }));
vi.mock('$app/state', () => ({
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false },
	page: {
		state: {},
		params: { id: 'r-sep' },
		url: new URL('http://sift.local/insights/recaps/r-sep')
	}
}));

import Page from './+page.svelte';

const HOUR = 3_600_000;

function said(text: string) {
	return [{ text, kind: null, id: null, href: null, gone: false, rest: [], lead: '' }];
}

function card(kind: string, text: string, hidden = false, over: Record<string, unknown> = {}) {
	return {
		id: kind,
		kind,
		statement: hidden ? [] : said(text),
		figure: null,
		cover: null,
		rows: [],
		chart: null,
		hidden_things: [],
		hidden,
		...over
	};
}

function recap(over: Record<string, unknown> = {}) {
	return {
		id: 'r-sep',
		period: 'month:2026-09',
		title: 'Your September',
		span: 'September 2026',
		heading: 'September, in 3 cards',
		made_at: 1,
		hidden_line: null,
		first_day: '2026-09-01',
		cards: [
			card('headline', 'You viewed 41 hours in September.', false, {
				figure: { label: 'Viewed', value: 41 * HOUR, unit: 'ms', hidden_part: 0, caption: [] }
			}),
			card('o', 'You pressed O 3 times in September.'),
			card('closing', 'That was September.')
		],
		...over
	};
}

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

beforeEach(() => {
	server.get.mockReset();
	server.get.mockResolvedValue(recap());
});

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
});

async function draw(): Promise<HTMLElement> {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(Page, { target: host });
	for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();
	flushSync();
	return host;
}

const cards = () => [...host.querySelectorAll('.cards > li')];
const inView = () => host.querySelector('.cards > li:not([hidden])') as HTMLElement;

async function later(): Promise<void> {
	(host.querySelector('button[aria-label="Later"]') as HTMLButtonElement).click();
	for (let turn = 0; turn < 3; turn += 1) await Promise.resolve();
	flushSync();
}

describe('the cards', () => {
	it('are headed by the recap, and read one at a time in the order sent', async () => {
		await draw();
		expect(server.get).toHaveBeenCalledWith('/insights/recaps/r-sep');
		expect(host.querySelector('h1')?.textContent).toContain('September, in 3 cards');
		expect(host.querySelector('[aria-roledescription="carousel"]')).not.toBeNull();
		expect(cards()).toHaveLength(3);
		expect(inView().getAttribute('aria-label')).toBe('1 of 3');
		expect(inView().textContent).toContain('You viewed 41 hours in September.');
		await later();
		await later();
		expect(inView().getAttribute('aria-label')).toBe('3 of 3');
		expect(inView().textContent).toContain('That was September.');
		expect((host.querySelector('button[aria-label="Later"]') as HTMLButtonElement).disabled).toBe(
			true
		);
	});

	it("draws a card's figure large, with its sentence as the caption under it", async () => {
		await draw();
		const first = inView();
		expect(first.querySelector('.figure-card .value')?.getAttribute('data-final')).toBe('1 d 17 h');
		expect(first.querySelector('.said')?.classList.contains('captioned')).toBe(true);
		await later();
		await later();
		expect(inView().querySelector('.said')?.classList.contains('captioned')).toBe(false);
	});

	it('ends on the way to the same period on Insights', async () => {
		await draw();
		const see = host.querySelector('.recap > a.see-it');
		expect(see?.textContent).toBe('See this month in Insights');
		expect(see?.getAttribute('href')).toBe('/insights?period=month&at=2026-09-01');
	});

	it('draws the top five as a ranked list and the favourite time as a ring', async () => {
		const person = (name: string, value: number) => ({
			piece: { text: name, kind: 'person', id: name, href: null, gone: false, rest: [], lead: '' },
			value,
			unit: 'ms',
			cover: null
		});
		const hours = Array.from({ length: 24 }, (_, hour) => ({
			label: String(hour).padStart(2, '0'),
			parts: [{ kind: 'all', value: hour === 22 ? 5 * HOUR : 0 }]
		}));
		server.get.mockResolvedValue(
			recap({
				cards: [
					card('top_five', 'The people you viewed most in September.', false, {
						rows: [person('Elina Sorrel', 6 * HOUR), person('Cassia Lynn', 3 * HOUR)]
					}),
					card('when', 'You viewed the most on Thursdays: 12 hours.', false, {
						chart: { kind: 'bars', unit: 'ms', bars: hours, today: null, caption: [] }
					})
				]
			})
		);
		await draw();
		const five = inView();
		expect(five.querySelector('.ranked-list .first')?.textContent).toContain('Elina Sorrel');
		expect([...five.querySelectorAll('.ranked-list li')].map((li) => li.textContent)).toEqual([
			expect.stringContaining('Cassia Lynn')
		]);
		await later();
		const when = inView();
		expect(when.querySelector('.hour-ring .ring')).not.toBeNull();
	});

	it('gives each card its span at the foot, so a card saved as a picture says when', async () => {
		await draw();
		expect(inView().querySelector('.foot')?.textContent).toBe('September 2026');
	});

	/* Every card one size so Later stands still: the story is one definite width, its list one
	   column the whole of it, and the card that width (its own test holds 9:16). Centring the list's
	   items would let each card's words decide its width again. A layout this runner cannot
	   measure; the pinned browser suite reads the sizes. */
	it('stands every card in one width, whatever it says', () => {
		const rule = (name: string) =>
			new RegExp(`\\n\\t\\.${name} \\{([^}]*)\\}`).exec(source)?.[1] ?? '';
		expect(rule('story')).toMatch(/inline-size: min\(\s*100%,\s*var\(--story-width\)/);
		expect(rule('cards')).toContain('grid-template-columns: minmax(0, 1fr);');
		expect(rule('cards')).not.toContain('justify-items');
		expect(rule('turned')).not.toContain('justify-items');
		expect(rule('story')).not.toContain('align-items: center');
	});
});

describe('what the vault leaves', () => {
	it('draws a locked tile as one, and says the placeholder line once', async () => {
		server.get.mockResolvedValue(
			recap({
				hidden_line: 'Some of September is hidden. Unlock to include it.',
				cards: [card('top_person', 'Elina Sorrel', true), card('closing', 'That was September.')]
			})
		);
		await draw();
		expect(host.textContent).toContain('Some of September is hidden. Unlock to include it.');
		expect(inView().querySelector('[role="img"][aria-label="Hidden"]')).not.toBeNull();
		expect(host.querySelector('.cards')?.textContent).not.toContain('Elina Sorrel');
		const save = [...host.querySelectorAll('button')].find((button) =>
			button.textContent?.includes('Save as picture')
		) as HTMLButtonElement;
		expect(save.disabled).toBe(true);
		await later();
		expect(save.disabled).toBe(false);
	});

	it('says there is no recap at an address with none behind it, and nothing about why', async () => {
		server.get.mockRejectedValue(new ApiError(404, 'not found'));
		await draw();
		expect(host.textContent).toContain("There's no recap here.");
		expect(host.textContent).not.toMatch(/hidden|vault|locked/i);
	});
});
