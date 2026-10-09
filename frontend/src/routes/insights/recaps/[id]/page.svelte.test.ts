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

const server = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: server.get, post: server.post, put: server.put }
}));
vi.mock('$app/navigation', () => ({ goto: vi.fn(), replaceState: vi.fn() }));

const saving = vi.hoisted(() => ({
	deliver: vi.fn(),
	cardPicture: vi.fn(),
	filmCard: vi.fn(),
	encodeFilm: vi.fn(),
	keepAsCollections: vi.fn(),
	admin: { isAdmin: true }
}));
vi.mock('$lib/player/snapshot', () => ({ deliver: saving.deliver }));
vi.mock('$lib/shell/session.svelte', () => ({ session: saving.admin }));
vi.mock('$lib/components/insights/share-card', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/components/insights/share-card')>()),
	cardPicture: saving.cardPicture
}));
vi.mock('$lib/components/insights/deck-video', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/components/insights/deck-video')>()),
	filmCard: saving.filmCard,
	encodeFilm: saving.encodeFilm
}));
vi.mock('$lib/components/insights/cards/kinds', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/components/insights/cards/kinds')>()),
	keepAsCollections: saving.keepAsCollections
}));
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
		calendar: null,
		figures: [],
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

	it('draws each card through the one card, in order', async () => {
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
		// Each through the one card, which draws its own picture (its own tests hold the designs).
		expect(inView().querySelector('.recap-card')?.getAttribute('data-card')).toBe('top_five');
		expect(inView().textContent).toContain('Elina Sorrel');
		await later();
		expect(inView().querySelector('.recap-card')?.getAttribute('data-card')).toBe('when');
	});

	it('gives each card its span at the foot, so a card saved as a picture says when', async () => {
		await draw();
		expect(inView().querySelector('.foot')?.textContent).toContain('September 2026');
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

const press = (words: string) =>
	(
		[...document.querySelectorAll('button')].find((button) =>
			button.textContent?.trim().endsWith(words)
		) as HTMLButtonElement | undefined
	)?.click();

async function settle(): Promise<void> {
	for (let turn = 0; turn < 40; turn += 1) {
		await vi.advanceTimersByTimeAsync(1200);
		flushSync();
	}
}

describe('saving the deck', () => {
	beforeEach(() => {
		vi.useFakeTimers({ toFake: ['setTimeout'] });
		for (const one of [saving.deliver, saving.cardPicture, saving.filmCard, saving.encodeFilm])
			one.mockReset();
		saving.cardPicture.mockResolvedValue(new Blob(['png'], { type: 'image/png' }));
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it('saves the ticked cards as pictures, one file each, never a card that may not be', async () => {
		server.get.mockResolvedValue(
			recap({
				cards: [
					card('headline', 'You viewed 41 hours in September.'),
					card('top_person', '', true),
					card('o', 'You pressed O 3 times in September.'),
					card('closing', 'That was September.')
				]
			})
		);
		await draw();
		expect(host.querySelector('.left-out')?.textContent?.replace(/\s+/g, ' ').trim()).toBe(
			'3 of 4; 1 names something hidden'
		);
		// The first card out of the set.
		(host.querySelector('.deck-saves [role="checkbox"]') as HTMLElement).click();
		flushSync();
		press('Save 2 as pictures');
		await settle();
		expect(saving.deliver.mock.calls.map(([, name]) => name)).toEqual([
			'recap-month-2026-09-3.png',
			'recap-month-2026-09-4.png'
		]);
		// Untyped, so the clipboard refuses each and it lands as a file.
		expect(saving.deliver.mock.calls.every(([picture]) => (picture as Blob).type === '')).toBe(
			true
		);
		expect(host.querySelector('.stage .recap-card')).toBeNull();
	});

	it("films a month's savable cards and saves the video through the same door", async () => {
		saving.filmCard.mockResolvedValue([{ picture: new Blob(['f']), held: 90 }]);
		saving.encodeFilm.mockResolvedValue(new Blob(['mp4'], { type: 'video/mp4' }));
		await draw();
		press('Save as video');
		await settle();
		expect(saving.filmCard).toHaveBeenCalledTimes(3);
		expect(saving.encodeFilm.mock.calls[0][0]).toBe('r-sep');
		expect(saving.deliver).toHaveBeenCalledWith(expect.any(Blob), 'recap-month-2026-09.mp4');
		expect(host.querySelector('[role="progressbar"]')).toBeNull();
	});

	it('says so when the video cannot be saved', async () => {
		saving.filmCard.mockResolvedValue([{ picture: new Blob(['f']), held: 90 }]);
		saving.encodeFilm.mockRejectedValue(new ApiError(500, 'no'));
		await draw();
		press('Save as video');
		await settle();
		expect(saving.deliver).not.toHaveBeenCalled();
	});

	it('saves a square picture as the square tile of the same card, named for its cut', async () => {
		const laid: string[] = [];
		saving.cardPicture.mockImplementation(async (drawn: HTMLElement) => {
			laid.push(`${drawn.className} ${drawn.parentElement?.className}`);
			return new Blob(['png'], { type: 'image/png' });
		});
		await draw();
		const [, square] = host.querySelectorAll<HTMLElement>('.deck-saves [role="checkbox"]');
		square.click();
		flushSync();
		press('Save as picture');
		await settle();
		expect(saving.deliver.mock.calls.map(([, name]) => name)).toEqual([
			'recap-month-2026-09-1-square.png'
		]);
		expect(laid[0]).toMatch(/recap-card tile/);
		expect(laid[0]).toMatch(/\bstage\b.*\bsquare\b/);
		expect(host.querySelector('.stage .recap-card')).toBeNull();
		// Off again, the picture is the card on screen, 9:16.
		square.click();
		flushSync();
		press('Save as picture');
		await settle();
		expect(saving.deliver.mock.calls.at(-1)?.[1]).toBe('recap-month-2026-09-1.png');
		expect(laid.at(-1)).toMatch(/recap-card story/);
	});

	it("stands every card on the deck's own pictures", async () => {
		server.get.mockResolvedValue(
			recap({
				cards: [
					card('headline', 'You viewed 41 hours in September.'),
					card('top_file', 'Your most viewed file.', false, { cover: '/api/assets/f9/thumb' })
				]
			})
		);
		await draw();
		expect(
			[...inView().querySelectorAll('.recap-card img')].map((one) => one.getAttribute('src'))
		).toContain('/api/assets/f9/thumb');
	});

	it("offers no video of a week's deck", async () => {
		server.get.mockResolvedValue(recap({ period: 'week:2026-W39' }));
		await draw();
		expect(host.textContent).not.toContain('Save as video');
	});
});

describe('leaving something out', () => {
	it('takes what the card names out of the recap, then reads the deck again', async () => {
		const her = {
			text: 'Elina Sorrel',
			kind: 'person',
			id: 'p1',
			href: '/people/p1',
			gone: false,
			rest: [],
			lead: ''
		};
		server.get.mockResolvedValue(
			recap({
				cards: [
					card('top_person', '', false, { statement: [her, ...said(' was the one.')] }),
					card('closing', 'That was September.')
				]
			})
		);
		server.put.mockReset();
		server.put.mockResolvedValue({});
		await draw();
		const reads = server.get.mock.calls.length;
		press('Leave out');
		flushSync();
		const tick = [...document.querySelectorAll<HTMLElement>('[role="dialog"] [role="checkbox"]')];
		expect(tick).toHaveLength(1);
		tick[0].click();
		flushSync();
		press('Save');
		for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();
		expect(server.put).toHaveBeenCalledWith('/insights/recaps/r-sep/left-out', {
			body: { ids: ['p1'] }
		});
		expect(server.get.mock.calls.length).toBeGreaterThan(reads);
	});

	it('offers nothing to leave out on a card that names nobody', async () => {
		await draw();
		const button = [...host.querySelectorAll('button')].find((one) =>
			one.textContent?.includes('Leave out')
		);
		expect(button?.disabled).toBe(true);
	});
});

describe("a year's deck", () => {
	const year = () =>
		recap({
			id: 'r-year',
			period: 'year:2025',
			cards: [
				card('session', 'Your longest visit in 2025 ran 2 hours.'),
				card('closing', 'That was 2025.')
			]
		});
	const sheet = {
		lists: [
			{
				key: 'most_viewed',
				name: 'Your 2025, most viewed',
				asset_ids: ['a1', 'a2'],
				ticked: true,
				kept: null
			},
			{
				key: 'rediscovered',
				name: 'Rediscoveries of 2025',
				asset_ids: ['a2'],
				ticked: true,
				kept: 'c1'
			},
			{
				key: 'new_favourites',
				name: 'New favorites of 2025',
				asset_ids: ['a3'],
				ticked: false,
				kept: null
			}
		]
	};

	beforeEach(() => {
		server.get.mockImplementation(async (path: string) => {
			if (path.endsWith('/session'))
				return {
					steps: [
						{ piece: said('Browse')[0], after_ms: 0 },
						{ piece: said('Theater')[0], after_ms: 60_000 }
					],
					more: 0
				};
			if (path.endsWith('/keep')) return sheet;
			return year();
		});
		saving.keepAsCollections.mockReset();
		saving.keepAsCollections.mockResolvedValue([{ id: 'c2', name: 'Your 2025, most viewed' }]);
	});

	it('draws how one session went from its pages', async () => {
		await draw();
		expect(server.get).toHaveBeenCalledWith('/insights/recaps/r-year/session');
		expect(inView().querySelector('.session .path')?.textContent).toContain('Theater');
	});

	it('keeps the ticked lists as Collections from its closing card, never one kept already', async () => {
		await draw();
		expect(host.textContent).not.toContain('Keep as Collections');
		await later();
		press('Keep as Collections');
		for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();
		flushSync();
		const body = document.body.textContent ?? '';
		expect(body).toContain('Already kept');
		expect(body).toContain('2 files');
		press('Keep');
		for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();
		const keptNow = saving.keepAsCollections.mock.calls[0][0] as { key: string }[];
		expect(keptNow.map((one) => one.key)).toEqual(['most_viewed']);
	});

	it('offers Keep as Collections to an admin only', async () => {
		saving.admin.isAdmin = false;
		try {
			await draw();
			await later();
			expect(host.textContent).not.toContain('Keep as Collections');
		} finally {
			saving.admin.isAdmin = true;
		}
	});
});
