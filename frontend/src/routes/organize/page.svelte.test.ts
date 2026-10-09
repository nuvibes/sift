/* The board: what it draws, and the two states that must never share a code path. */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';

import { goto } from '$app/navigation';

import { ApiError } from '$lib/api/client';
import { session, type Viewer } from '$lib/shell/session.svelte';
import Board from './+page.svelte';
import wallSource from '$lib/components/organize/CardWall.svelte?raw';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import { words } from '$lib/design/testing.svelte';
import codepoints from '$lib/generated/icon-codepoints.json';
import { noServerAt } from '../../test-setup';

/* Left unanswered on purpose: the interface settings, read by the frame on the way past. */
noServerAt('/api/settings/interface');

const mocks = vi.hoisted(() => ({
	board: vi.fn(),
	post: vi.fn(),
	undo: vi.fn(),
	waiting: { count: 0, check: vi.fn(), forget: vi.fn() },
	/* Replaced by the factory below, which is the only place state the component can subscribe
	   to can be declared. */
	held: { found: null } as { found: unknown }
}));

vi.mock('$lib/organize/organize.svelte', async (importOriginal) => {
	/* Built ON the real module rather than instead of it, so an export nobody here thought about
	   is still the real one instead of being absent. */
	const real = await importOriginal<Record<string, unknown>>();
	/* The board is HELD in a store, so this stands in for the store rather than for the request:
	   `found` is what the screen draws, `refresh` is what fills it. */
	const { reactiveProps } = await import('$lib/design/testing.svelte');
	const held = reactiveProps<{ found: unknown }>({ found: null });
	mocks.held = held;
	return {
		...real,
		board: mocks.board,
		heldBoard: {
			get found() {
				return held.found;
			},
			async refresh() {
				const answer = await mocks.board();
				held.found = answer;
				return answer;
			}
		},
		waiting: mocks.waiting,
		undo: mocks.undo,
		decided: vi.fn(),
		/* The screen reloads when this moves. A double missing it leaves the component reading
		   `undefined.stamp`, which is not a wrong answer on screen. */
		answered: { stamp: 0, changed: vi.fn() }
	};
});
vi.mock('$lib/library/changes.svelte', async () => ({
	...(await vi.importActual<typeof import('$lib/library/changes.svelte')>(
		'$lib/library/changes.svelte'
	)),
	reloadOnLibraryChange: () => {}
}));
vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
/* The API itself, so the one thing worth asserting about the board's writes can be: that it
   makes none. */
vi.mock('$lib/api/client', async (importOriginal) => {
	const real = await importOriginal<Record<string, unknown>>();
	return { ...real, api: { ...(real.api as object), post: mocks.post } };
});

let host: HTMLElement;

/** The board's cards, one per pile: the list items of the one list the board draws. */
function cards(): HTMLElement[] {
	return [...host.querySelectorAll<HTMLElement>('ul[aria-label="Organize"] > li')];
}

/** The way into a pile: the card's one button, Review. */
function wayIn(): HTMLButtonElement | null {
	return host.querySelector<HTMLButtonElement>('.board-card button');
}

/** The name each card wears, in the board's order. */
function names(): string[] {
	return cards().map((one) => words(one.querySelector('.section-heading h3') as HTMLElement));
}

/** Every way off the board, by where it leads: the cards' and the header's alike. */
function hrefs(): string[] {
	return [...host.querySelectorAll<HTMLAnchorElement>('a')].map(
		(one) => one.getAttribute('href') ?? ''
	);
}

function queue(over: Record<string, unknown> = {}) {
	return {
		name: 'folders',
		title: 'Folders that look like somebody',
		decision: 'Say whether a folder is the person its name suggests.',
		/* The phrase after the count. The card leads with the number, so this is the half that
		   says what was counted: a fixture without one draws a bare figure. */
		verb: 'folders to name',
		verb_one: 'folder to name',
		icon: 'folder',
		count: 3,
		/* What the card says the pile is for: one sentence, declared by the queue. */
		purpose: 'Folders whose names look like a person, for you to confirm.',
		/* The band, which is what the board sorts by. */
		band: 'decision',
		group: null,
		pending: true,
		preview: [],
		...over
	};
}

function answer(over: Record<string, unknown> = {}) {
	return { queues: [], waiting: 0, ...over };
}

/** Draw the screen and let its first load settle. */
async function render() {
	host = document.createElement('div');
	document.body.append(host);
	mount(Board, { target: host });
	flushSync();
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
	return host;
}

beforeEach(() => {
	mocks.board.mockReset();
	mocks.post.mockReset();
	mocks.undo.mockReset();
	mocks.waiting.count = 0;
	/* The real store is one object for the life of the tab, so a board left over from the last
	   test would be on screen before this one's request is answered, which is the behaviour the
	   store exists for, and useless as a starting point for a test. */
	mocks.held.found = null;
	/* The card body's press navigates, and the stand-in is one function for the whole file: left
	   alone it would carry the last test's call into the next one's assertion. */
	vi.mocked(goto).mockClear();
});

afterEach(() => {
	removeStyles();
	host?.remove();
});

describe('before the answer arrives', () => {
	it('draws the skeleton rather than saying nothing needs you', async () => {
		/* The failure this is for: an empty board and a board that has not answered yet look the
		 * same on screen unless they are told apart, and the wrong one of the two is a screen that
		 * cheerfully reports success while the request is still in flight. */
		mocks.board.mockReturnValue(new Promise(() => {}));

		host = document.createElement('div');
		document.body.append(host);
		mount(Board, { target: host });
		flushSync();

		// The in-flight state is `Skeleton`: its bones, rather than a word the screen writes for
		// itself.
		expect(host.querySelector('.bone')).not.toBeNull();
		expect(host.textContent).not.toContain('Nothing to review');
	});
});

describe('when nothing is waiting', () => {
	it('says so over the cards, and every pile keeps its card, each saying it has nothing', async () => {
		/* A pile at nought stays on the wall: a pile that left could not be told from one never
		   turned on. */
		mocks.board.mockResolvedValue(
			answer({
				queues: [
					queue({ count: 0 }),
					queue({
						name: 'skipped',
						title: 'Skipped files',
						count: 0,
						band: 'log',
						verb: 'skipped files',
						verb_one: 'skipped file'
					}),
					queue({
						name: 'shoots',
						title: 'Shoots',
						count: 0,
						verb: 'shoots to agree',
						verb_one: 'shoot to agree'
					})
				]
			})
		);

		await render();

		expect(names()).toEqual(['Folders that look like somebody', 'Shoots', 'Skipped files']);
		expect(host.querySelectorAll('.caught-up')).toHaveLength(3);
		const empty = host.querySelector('.empty') as HTMLElement;
		expect(empty.classList.contains('block')).toBe(true);
		expect(words(empty)).toContain('Nothing needs you right now.');
		expect(host.textContent).not.toContain('Nothing waiting');
		expect(host.querySelector('.bone')).toBeNull();
	});

	it('and is the whole screen only where there is no pile at all', async () => {
		/* Every feature that makes a pile turned off: nothing to draw, so the board's own empty
		   state is the screen. */
		mocks.board.mockResolvedValue(answer({ queues: [] }));

		await render();

		expect(cards()).toHaveLength(0);
		const empty = host.querySelector('.empty') as HTMLElement;
		expect(empty.classList.contains('block')).toBe(false);
		expect(words(empty.querySelector('.title') as HTMLElement)).toBe('Nothing to review');
	});

	it('and says it as a sentence over the cards that remain', async () => {
		/* Files Sift could not read are a report, not work: the board has reached its goal and
		   says so, and the card for them stays under the sentence. */
		mocks.board.mockResolvedValue(
			answer({
				queues: [
					queue({ count: 0 }),
					queue({ name: 'skipped', title: 'Skipped files', count: 412, band: 'log' })
				]
			})
		);

		await render();

		const empty = host.querySelector('.empty') as HTMLElement;
		expect(empty.classList.contains('block')).toBe(true);
		expect(empty.querySelector('.title')).toBeNull();
		expect(words(empty)).toContain('Nothing needs you right now.');
		expect(names()).toEqual(['Folders that look like somebody', 'Skipped files']);
	});
});

describe('when something is waiting', () => {
	it('leads with the count and says what it counts', async () => {
		/* "4 folders to name" rather than "Folders 4": the two facts read as one sentence, and
		   the join is not always the obvious one: the Duplicates card counts GROUPS. */
		mocks.board.mockResolvedValue(answer({ queues: [queue()], waiting: 3 }));

		await render();

		expect(host.textContent).toContain('Folders that look like somebody');
		expect(host.querySelector('.n')?.textContent).toBe('3');
		expect(host.querySelector('.verb')?.textContent).toBe('folders to name');
		expect(host.textContent).not.toContain('Nothing to review');
	});

	it('and a pile down to its last one reads in the singular', async () => {
		/* The state every emptying pile passes through, and with one wording the card would read
		   "1 folders to name" in it ("1 shoots to agree", "1 groups that look alike"). */
		mocks.board.mockResolvedValue(answer({ queues: [queue({ count: 1 })], waiting: 1 }));

		await render();

		expect(host.querySelector('.n')?.textContent).toBe('1');
		expect(host.querySelector('.verb')?.textContent).toBe('folder to name');
	});

	it('and two of them read in the plural, which is what makes the pick a pick', async () => {
		/* Without this the singular could be drawn at every count and the test above would pass:
		   the fault would only have swapped ends. */
		mocks.board.mockResolvedValue(answer({ queues: [queue({ count: 2 })], waiting: 2 }));

		await render();

		expect(host.querySelector('.verb')?.textContent).toBe('folders to name');
	});

	it('and a card whose queue this version cannot open cannot be opened', async () => {
		/* The way in is a real control at the end of the row, and it is what refuses when this
		   build has no drawing for the queue. */
		mocks.board.mockResolvedValue(answer({ queues: [queue({ name: 'from-a-later-version' })] }));

		await render();

		expect(cards()).toHaveLength(1);
		expect(wayIn()).toBeNull();
		expect(host.querySelector('.card.opens')).toBeNull();
	});
});

describe('the piles that are a record rather than a question', () => {
	it('are not cards at all: they are reached through their own page', async () => {
		/* A count that never goes down cannot sit on a screen whose promise is that it empties. */
		mocks.board.mockResolvedValue(
			answer({
				queues: [
					queue(),
					queue({
						name: 'ignored',
						title: 'Faces you set aside',
						band: 'record',
						group: 'faces',
						pending: false
					})
				],
				waiting: 3
			})
		);

		await render();

		expect(cards()).toHaveLength(1);
		expect(host.textContent).not.toContain('Faces you set aside');
	});

	it('and do not stop the screen reading as finished', async () => {
		/* A record queue holds things that have been dealt with. */
		mocks.board.mockResolvedValue(
			answer({
				queues: [
					queue({ count: 0 }),
					queue({
						name: 'ignored',
						title: 'Faces you set aside',
						count: 90,
						band: 'record',
						group: 'faces',
						pending: false
					})
				]
			})
		);

		await render();

		const empty = host.querySelector('.empty') as HTMLElement;
		expect(empty.classList.contains('block')).toBe(true);
		expect(words(empty)).toContain('Nothing needs you right now.');
		expect(cards()).toHaveLength(1);
	});
});

describe('the bands', () => {
	it('draws one list in band order and no heading over any band', async () => {
		/* No headings between bands: the bands ORDER the rows, and nothing sits between them. */
		mocks.board.mockResolvedValue(
			answer({
				queues: [
					queue(),
					queue({ name: 'copies', title: 'Exact duplicates', band: 'cleanup' }),
					queue({ name: 'skipped', title: 'Skipped files', band: 'log' })
				]
			})
		);

		await render();

		expect(host.querySelectorAll('.band h2')).toHaveLength(0);
		expect(host.querySelectorAll('ul[aria-label="Organize"]')).toHaveLength(1);
		expect(names()).toEqual([
			'Folders that look like somebody',
			'Exact duplicates',
			'Skipped files'
		]);
	});

	it('puts a band it has never heard of at the end rather than dropping it', async () => {
		/* A newer server can send a band this build does not know. */
		mocks.board.mockResolvedValue(
			answer({ queues: [queue({ name: 'later', title: 'Something newer', band: 'invented' })] })
		);

		await render();

		expect(cards()).toHaveLength(1);
		expect(host.textContent).toContain('Something newer');
	});
});

describe('the piles with nothing waiting', () => {
	it('keep their place on the wall, each saying it has nothing to review', async () => {
		mocks.board.mockResolvedValue(
			answer({
				queues: [
					queue(),
					queue({ name: 'shoots', title: 'Shoots', count: 0 }),
					queue({ name: 'filenames', title: 'Enriched from filenames', count: 0, band: 'log' }),
					queue({
						name: 'duplicates',
						title: 'Near duplicates',
						group: 'duplicates',
						group_title: 'Duplicates',
						count: 0
					})
				]
			})
		);

		await render();

		expect(names()).toEqual([
			'Folders that look like somebody',
			'Shoots',
			'Near duplicates',
			'Enriched from filenames'
		]);
		expect(host.querySelectorAll('.caught-up')).toHaveLength(3);
		expect(host.textContent).not.toContain('Nothing waiting');
		// Something is waiting, so the board does not say it is finished.
		expect(host.querySelector('.empty')).toBeNull();
	});

	it('and a pile at nought asks nothing even where a question still comes with it', async () => {
		/* Nothing on a card decides anything, so a question sent beside a pile at nought (an
		   older server's Shoots offering to look now) has nowhere to be asked: the card says it
		   has nothing to review and offers its page. */
		mocks.board.mockResolvedValue(
			answer({
				queues: [queue({ name: 'shoots', title: 'Shoots', count: 0, first: { id: 'look-now' } })]
			})
		);

		await render();

		expect(cards()).toHaveLength(1);
		expect(host.querySelectorAll('.caught-up')).toHaveLength(1);
		expect(host.textContent).not.toContain('look-now');
	});
});

describe('what it will not do', () => {
	it('offers no way to sort, resize or filter what is on screen', async () => {
		/* Browse answers "what do I have". This answers "what needs me", and building a place to
		 * look at files here is the failure this screen is most likely to drift into. */
		mocks.board.mockResolvedValue(answer({ queues: [queue()] }));

		await render();

		expect(host.textContent).not.toMatch(/\bsort\b/i);
		expect(host.textContent).not.toMatch(/\bfilter/i);
		expect(host.querySelector('input[type="range"]')).toBeNull();
	});
});

describe('what was lately decided', () => {
	it("is History's Decisions, one link away in the header, and not a list here", async () => {
		/* Every answer given here and every filing Sift made by itself is a line of History,
		 * each with its Undo, so the record is History narrowed to Decisions rather than a
		 * screen of its own: one place to look. */
		mocks.board.mockResolvedValue(answer({ queues: [queue()] }));
		session.viewer = { role: 'admin' } as Viewer;

		await render();

		const door = [...host.querySelectorAll<HTMLAnchorElement>('a')].find(
			(one) => words(one) === 'Decisions'
		);
		session.viewer = undefined;
		expect(door?.getAttribute('href')).toBe('/settings/tasks#activity.decisions');
		expect(hrefs()).not.toContain('/organize/decisions');
	});
	it('tells a guest it is refused rather than that something went wrong', async () => {
		/* A 403 is not a transient failure, and collapsing it into one would tell a guest to "try
		 * again in a moment" for a screen that will never open for them, while still describing
		 * what the feature does, which is the disclosure the hidden rail entry exists to avoid. */
		mocks.board.mockRejectedValue(new ApiError(403, 'That is not allowed.'));

		await render();

		expect(host.textContent).toContain('administrator');
		expect(host.textContent).not.toContain('Try again in a moment');
		expect(host.textContent).not.toContain('could not decide on its own');
		// Nor a way into a record the guest may not open.
		expect(host.textContent).not.toContain('Decisions');
	});
});

describe('the stand-in for the store', () => {
	it('answers for everything the real one exports', async () => {
		/* What holds this is the stand-in being built on the real module. */
		const real = await vi.importActual<Record<string, unknown>>('$lib/organize/organize.svelte');
		const stand = await import('$lib/organize/organize.svelte');

		const missing = Object.keys(real).filter((name) => !(name in stand));
		expect(missing).toEqual([]);
	});
});

it('draws a strip where two folders share the same picture', async () => {
	/* One still per folder, and two folders holding the same file have the same still, which is
	   ordinary enough that a whole feature exists for finding it. */
	mocks.board.mockResolvedValue(
		answer({
			queues: [
				queue({
					count: 2,
					preview: [
						{ kind: 'asset', id: 'same-asset', href: null },
						{ kind: 'asset', id: 'same-asset', href: null }
					]
				})
			]
		})
	);

	await render();

	expect(host.querySelector('.bone')).toBeNull();
	expect(host.querySelectorAll('.strip img, .strip [data-thumb], .strip *').length).toBeGreaterThan(
		0
	);
});

describe('how a card is laid out', () => {
	/* Every card the same order: the name, what the pile is for, the count, the stills, then the
	   one button at the foot. */
	function blocks(): string[] {
		return [...(host.querySelector('.board-card .panel')?.children ?? [])].map(
			(one) => one.classList[0] ?? ''
		);
	}

	it('stands the name, what it is for, the count and the stills, and Review at the foot', async () => {
		mocks.board.mockResolvedValue(
			answer({ queues: [queue({ preview: [{ kind: 'asset', id: 'a1', href: null }] })] })
		);

		await render();

		expect(names()).toEqual(['Folders that look like somebody']);
		expect(host.querySelector('.n')?.textContent).toBe('3');
		expect(blocks()).toEqual(['section-heading', 'purpose', 'lead', 'strip', 'foot']);
		expect(words(host.querySelector('.purpose') as HTMLElement)).toBe(
			'Folders whose names look like a person, for you to confirm.'
		);
		expect(words(wayIn() as HTMLElement)).toContain('Review');
	});

	it('draws two rows of stills, twelve at most, each a picture and never a link', async () => {
		/* Two rows of six on every card: enough to recognize a pile, and the same on every
		   queue, because this file knows which queue it is holding only by name. */
		mocks.board.mockResolvedValue(
			answer({
				queues: [
					queue({
						preview: Array.from({ length: 20 }, (_unused, at) => ({
							kind: 'asset',
							id: `a${at}`,
							href: `/organize/duplicates#group-phash:a${at}`
						}))
					})
				]
			})
		);

		await render();

		const strips = host.querySelectorAll('.strip');
		expect(strips).toHaveLength(1);
		expect(strips[0].querySelectorAll('img')).toHaveLength(12);
		expect(host.querySelector('.strip a')).toBeNull();
	});

	it('and a still that cannot be drawn leaves the strip for the next one', async () => {
		/* A picture not made yet, or refused, drawn as a blank square reads as a row of hidden
		   or broken files. */
		mocks.board.mockResolvedValue(
			answer({
				queues: [
					queue({
						preview: Array.from({ length: 14 }, (_unused, at) => ({
							kind: 'asset',
							id: `a${at}`,
							href: null
						}))
					})
				]
			})
		);

		await render();

		const first = host.querySelector<HTMLImageElement>('.strip img');
		first?.dispatchEvent(new Event('error'));
		flushSync();

		const drawn = [...host.querySelectorAll<HTMLImageElement>('.strip img')];
		expect(drawn).toHaveLength(12);
		expect(drawn.some((one) => one.src.includes('/a0/'))).toBe(false);
		expect(drawn.at(-1)?.src).toContain('/a12/');
	});

	it('and no strip at all where none of the stills can be drawn', async () => {
		mocks.board.mockResolvedValue(
			answer({ queues: [queue({ preview: [{ kind: 'asset', id: 'a1', href: null }] })] })
		);

		await render();

		host.querySelector('.strip img')?.dispatchEvent(new Event('error'));
		flushSync();

		expect(host.querySelector('.strip')).toBeNull();
	});

	it('and only as many stills as the queue sent', async () => {
		/* No room held for stills that are not there. */
		mocks.board.mockResolvedValue(
			answer({ queues: [queue({ preview: [{ kind: 'asset', id: 'a1', href: null }] })] })
		);

		await render();

		expect(host.querySelectorAll('.strip img')).toHaveLength(1);
		expect(host.querySelectorAll('.strip .stand-in')).toHaveLength(0);
		// No address, so it stays a picture. A card wants stills, not links saying nothing.
		expect(host.querySelector('.strip a')).toBeNull();
	});

	it('makes the card itself a press that does what the way in does', async () => {
		/* Press a card anywhere that is not a control and reach what its name reaches. */
		mocks.board.mockResolvedValue(answer({ queues: [queue({ name: 'folders', count: 3 })] }));

		await render();

		const press = host.querySelector<HTMLElement>('.board-card .purpose');
		expect(press).not.toBeNull();

		press?.click();
		flushSync();
		expect(goto).toHaveBeenCalledWith('/organize/folders');

		vi.mocked(goto).mockClear();
		wayIn()?.click();
		expect(goto).toHaveBeenCalledWith('/organize/folders');
	});

	it('and cannot be pressed at all where this version cannot open the queue', async () => {
		/* The same rule the way in follows. A press that silently did nothing would read as the
		   card being broken rather than as a screen this build has no drawing for. */
		mocks.board.mockResolvedValue(
			answer({ queues: [queue({ name: 'from-a-later-version', title: 'Something newer' })] })
		);

		await render();

		expect(host.querySelector('.card.opens')).toBeNull();
		host.querySelector<HTMLElement>('.board-card .purpose')?.click();
		expect(goto).not.toHaveBeenCalled();
	});

	it('names the way in with the GROUP, because the card is the page and not the lead', async () => {
		/* The card wears the group's title as its name, the way in, never the first queue's own
		   name. */
		mocks.board.mockResolvedValue(
			answer({
				queues: [
					queue({
						name: 'duplicates',
						title: 'Near Duplicates',
						group: 'duplicates',
						group_title: 'Duplicates',
						count: 40
					}),
					queue({
						name: 'copies',
						title: 'Exact Duplicates',
						group: 'duplicates',
						band: 'cleanup',
						count: 12
					})
				]
			})
		);

		await render();

		expect(names()).toEqual(['Duplicates']);
		expect(wayIn()?.getAttribute('aria-label')).toBe('Review Duplicates');
		wayIn()?.click();
		expect(goto).toHaveBeenCalledWith('/organize/duplicates');
		expect(host.querySelector('.n')?.textContent).toBe('52');
		// The parts count different things, so the sum says what they share: they are waiting.
		expect(host.querySelector('.verb')?.textContent).toBe('waiting');
		expect(host.querySelector('.parts, .chip')).toBeNull();
	});
});

describe('nothing on the board decides', () => {
	it('asks no question and offers no answer, even where the server still sends one', async () => {
		/* An older server sends the first question of each pile. */
		mocks.board.mockResolvedValue(
			answer({
				queues: [
					queue({
						first: {
							id: 'claim-1',
							kind: 'claim',
							question: 'Is Wren Halloway the person in this folder?',
							detail: 'Downloads/wren_halloway \u2014 212 files',
							preview: null,
							choices: [
								{ label: 'Yes, file them', send: { method: 'POST', path: '/x' }, tone: 'primary' }
							]
						}
					})
				]
			})
		);

		await render();

		expect(host.textContent).not.toContain('Wren Halloway');
		expect(host.textContent).not.toContain('Yes, file them');
		expect(host.querySelector('.answers')).toBeNull();
		const buttons = [...host.querySelectorAll<HTMLButtonElement>('.board-card button')];
		expect(buttons.map((one) => one.getAttribute('aria-label'))).toEqual([
			'Review Folders that look like somebody'
		]);
		buttons[0].click();
		expect(mocks.post).not.toHaveBeenCalled();
		expect(goto).toHaveBeenCalledWith('/organize/folders');
	});
});

/* THE BOARD FILLS THE FRAME, as many columns as fit, one on a phone: the wall's rules
 * (`CardWall`, the board's narrower column), read with its stylesheet in place. */
describe('the wall', () => {
	const PHONE = '@media (max-width: 767px)';
	const RULE = 'repeat(auto-fill, minmax(min(var(--wall-column), 100%), 1fr))';
	/* The frame's content box at each window width, measured (frame 209 to width less 9, inset 24). */
	const CONTENT = { 1280: 1014, 1600: 1334, 1920: 1654 } as const;

	/* Read from the file: a stylesheet imported `?raw` arrives empty in this environment. */
	const tokens = readFileSync(resolve('src/app.css'), 'utf8');

	function px(token: string): number {
		const found = tokens.match(new RegExp(`--${token}:\\s*(\\d+)px;`));
		if (!found) throw new Error(`no --${token} in app.css`);
		return Number(found[1]);
	}

	it("takes the frame's whole width, at the inset a wall has, not the reading measure", async () => {
		mocks.board.mockResolvedValue(answer({ queues: [queue()] }));

		await render();

		const body = host.querySelector('.frame-body-inner') as HTMLElement;
		expect(body).not.toBeNull();
		expect(body.contains(host.querySelector('ul.wall'))).toBe(true);
		expect(body.classList.contains('measure')).toBe(false);
		expect(body.classList.contains('bleed')).toBe(false);
	});

	it('lays three columns at 1280, four at 1600 and five at 1920', async () => {
		mocks.board.mockResolvedValue(answer({ queues: [queue()] }));

		await render();

		const wall = host.querySelector('ul.wall') as HTMLElement;
		applyStyles(wallSource, wall);
		expect(wall.classList.contains('board')).toBe(true);
		expect(getComputedStyle(wall).getPropertyValue('--wall-column')).toBe(
			'var(--board-column-min)'
		);
		expect(getComputedStyle(wall).display).toBe('grid');
		expect(getComputedStyle(wall).gridTemplateColumns.replace(/\s+/g, ' ')).toBe(RULE);
		expect(getComputedStyle(wall).gap).toBe('var(--space-4)');

		const least = px('board-column-min');
		const gap = px('space-4');
		const columns = (width: number) => Math.floor((width + gap) / (least + gap));
		expect(columns(CONTENT[1280])).toBe(3);
		expect(columns(CONTENT[1600])).toBe(4);
		expect(columns(CONTENT[1920])).toBe(5);
	});

	it('lays one column on a phone', async () => {
		mocks.board.mockResolvedValue(answer({ queues: [queue()] }));

		await render();

		const wall = host.querySelector('ul.wall') as HTMLElement;
		expect(wallSource).toContain(PHONE);
		applyStyles(wallSource.replace(PHONE, '@media screen'), wall);
		expect(getComputedStyle(wall).gridTemplateColumns).toBe('minmax(0, 1fr)');
	});
});

/* THE CARD'S LEAD: the figure is the sum of the chips that carry a number, and a record riding
 * on a grouped card is named without one. */
describe('every card one size', () => {
	it('gives every row the height of the tallest card, the last row of one included', async () => {
		mocks.board.mockResolvedValue(answer({ queues: [queue()] }));

		await render();

		const wall = host.querySelector('ul.wall') as HTMLElement;
		applyStyles(wallSource, wall);
		expect(getComputedStyle(wall).gridAutoRows).toBe('1fr');
	});

	it('draws every card in the one shape, whichever band it is in', async () => {
		mocks.board.mockResolvedValue(
			answer({
				queues: [
					queue({ preview: [{ kind: 'asset', id: 'a1', href: null }] }),
					queue({ name: 'skipped', title: 'Skipped files', band: 'log' }),
					queue({ name: 'copies', title: 'Exact duplicates', band: 'cleanup' })
				]
			})
		);

		await render();

		for (const card of cards()) {
			const panel = card.querySelector('.panel') as HTMLElement;
			const shape = [...panel.children]
				.map((one) => one.classList[0])
				.filter((one) => one !== 'strip');
			expect(shape).toEqual(['section-heading', 'purpose', 'lead', 'foot']);
			expect(card.querySelectorAll('button')).toHaveLength(1);
		}
	});
});
