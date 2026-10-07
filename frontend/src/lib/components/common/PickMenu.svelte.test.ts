/*
 * The flyout that puts a file on something without leaving the menu.
 *
 * Asserted: the row opens into the list rather than a sheet, this account's picks come first and
 * the rest is the server's order, the last row says how many did not fit, one press writes with the
 * row pressed, letters go into the box instead of the menu's typeahead, and the two arrows exist
 * only while there is something that way.
 *
 * Driven through `RowMenu`, with the clock stopped. A flyout only exists inside an open menu, and
 * `RowMenu` is the one door a test can open. In jsdom the submenu closes itself about twenty
 * milliseconds later whatever is in it (the library's focus handling with no layout under it),
 * while the list arrives a microtask later and filtering waits out a longer debounce. So timers are
 * faked for the whole file, stopping the library's closing timer and the debounce, and each test
 * advances the clock itself. Microtasks still run, so an answered page can be awaited.
 */
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import RowMenu from './RowMenu.svelte';
import Probe from './PickMenuInlineProbe.test.svelte';
import menuRow from './ContextMenuItem.svelte?raw';
import type { OnAlready, PickChoice, PickLanded, PickPage, Verb } from './verbs';
import { forgetUse, noteUse, remembered } from '$lib/search/frequent.svelte';

/* What the server says each remembered thing is called NOW (`/search/names-now`). A remembered row
   off the page is drawn under this, never under the record's copy of the name. */
const naming = vi.hoisted(() => ({ names: {} as Record<string, string> }));

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string, options?: { query?: Record<string, unknown> }) => {
			if (path !== '/search/names-now') return { state: {} };
			const ids = (options?.query?.id as string[] | undefined) ?? [];
			return {
				items: ids.filter((id) => id in naming.names).map((id) => ({ id, name: naming.names[id] }))
			};
		}),
		post: vi.fn(),
		put: vi.fn(async () => undefined),
		del: vi.fn()
	}
}));

let host: HTMLElement;
let instance: ReturnType<typeof mount> | null = null;

function takeDown() {
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
}

afterEach(() => {
	takeDown();
	vi.useRealTimers();
});

beforeEach(() => {
	vi.useFakeTimers();
	forgetUse('collection');
	forgetUse('tag');
	naming.names = { 'c-amber': 'Amber', 'c-blue': 'Blue hour', 'c-dune': 'Dune', 'c-far': 'Zephyr' };
});

/** Names invented for this file. Nobody real, which is the rule for a fixture in this repo. */
const CHOICES = [
	{ id: 'c-amber', name: 'Amber' },
	{ id: 'c-blue', name: 'Blue hour' },
	{ id: 'c-dune', name: 'Dune' }
];

const PICKED = vi.fn();
const MADE = vi.fn(async (name: string) => ({ id: 'c-made', name }));

/** What the server would answer: alphabetical, filtered by what was typed. */
function pageFor(typed: string, rows = CHOICES, more = 0): PickPage {
	const needle = typed.trim().toLowerCase();
	const matched =
		needle === '' ? rows : rows.filter((one) => one.name.toLowerCase().includes(needle));
	return { choices: [...matched], more };
}

const ASKED = vi.fn(async (typed: string) => pageFor(typed));
const UNPICKED = vi.fn();

function verbs(
	over: {
		ask?: (typed: string) => Promise<PickPage>;
		making?: boolean;
		/**
		 * What the files are already on, as the host would answer it. Absent leaves the rows
		 * unmarked, which is the header picker's case.
		 */
		already?: Record<string, OnAlready> | (() => Promise<Record<string, OnAlready>>);
		/** What the write answers. Absent is `PICKED` / `UNPICKED`, which say nothing. */
		pick?: (ids: string[], choice: PickChoice) => Promise<PickLanded>;
		unpick?: (ids: string[], choice: PickChoice) => Promise<PickLanded>;
	} = {}
): Verb[] {
	return [
		{
			id: 'collect',
			label: 'Collection',
			icon: 'box',
			run: () => {},
			pick: {
				kind: 'collection',
				plural: 'collections',
				ask: over.ask ?? ASKED,
				pick: over.pick ?? PICKED,
				...(over.already
					? {
							already:
								typeof over.already === 'function'
									? over.already
									: async () => (over.already as Record<string, OnAlready>) ?? {},
							unpick: over.unpick ?? UNPICKED
						}
					: {}),
				...(over.making === false ? {} : { create: MADE })
			}
		},
		{ id: 'hide', label: 'Hide', icon: 'visibility_off', run: () => {} }
	];
}

/** Everything queued as a microtask, drained, then the component re-rendered. */
async function settle(): Promise<void> {
	for (let turn = 0; turn < 8; turn += 1) await Promise.resolve();
	flushSync();
}

/** Open the three-dot menu, then open the row that holds the list, then let the page land. */
async function openTheList(list = verbs(), rowLabel = 'Collection'): Promise<void> {
	takeDown();
	host = document.createElement('div');
	document.body.appendChild(host);
	instance = mount(RowMenu, { target: host, props: { verbs: list, ids: ['f1'], label: 'More' } });
	flushSync();

	host.querySelector('button')?.click();
	flushSync();

	const trigger = [...document.querySelectorAll('[role="menuitem"]')].find((row) =>
		(row.textContent ?? '').includes(rowLabel)
	);
	if (!(trigger instanceof HTMLElement)) throw new Error('the row that opens out is not there');
	trigger.click();
	flushSync();
	await settle();
}

function box(): HTMLInputElement {
	const found = document.querySelector('.pick input');
	if (!(found instanceof HTMLInputElement)) throw new Error('there is no box to narrow with');
	return found;
}

/** Type, wait out the debounce, and let the answer land. */
async function type(text: string): Promise<void> {
	const input = box();
	// FOCUSED first, which is what a person typing has done and what keeps the flyout up: left on
	// the trigger, the library shuts the submenu on its own timer the moment the clock moves.
	input.focus();
	input.value = text;
	input.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
	await vi.advanceTimersByTimeAsync(200);
	await settle();
}

/*
 * Every row of the flyout, whatever its role: a row that can show a tick is a `menuitemcheckbox`,
 * and the one that creates a new thing is a plain `menuitem`. A selector naming only the first
 * would find nothing and read as the list having failed to arrive.
 */
const ANY_ROW = '.pick [role="menuitem"], .pick [role="menuitemcheckbox"]';

/** The words on every row of the flyout. The icon is a ligature, so its codepoint is stripped. */
function rows(): string[] {
	return [...document.querySelectorAll(ANY_ROW)].map((row) =>
		(row.textContent ?? '').replace(/[\uE000-\uF8FF]/g, '').trim()
	);
}

/** What a row says about this selection: ticked, half ticked, or neither. */
function marks(): (string | null)[] {
	return [...document.querySelectorAll(ANY_ROW)].map((row) => row.getAttribute('aria-checked'));
}

/** The quiet last line that says how many are not shown, or nothing at all. */
function restLine(): string | null {
	return document.querySelector('.pick .rest')?.textContent?.trim() ?? null;
}

function rowSaying(words: string): HTMLElement {
	const row = [...document.querySelectorAll(ANY_ROW)].find(
		(one) => (one.textContent ?? '').replace(/[\uE000-\uF8FF]/g, '').trim() === words
	);
	if (!(row instanceof HTMLElement)) throw new Error(`there is no row saying ${words}`);
	return row;
}

/** A left press: act on that one row, and the menu goes. */
function press(words: string): void {
	rowSaying(words).click();
	flushSync();
}

/**
 * A RIGHT press: act on that one row and stay up for the next one.
 *
 * Dispatched rather than clicked, because that is what a right button actually produces: no `click`
 * event at all, so nothing reaches the library's select and nothing closes. The event is handed
 * back so a test can read whether the browser's own menu was refused.
 */
function rightPress(words: string): MouseEvent {
	const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, button: 2 });
	rowSaying(words).dispatchEvent(event);
	flushSync();
	return event;
}

/**
 * Whether the menu holding this list is still up.
 *
 * Read off the DOOR and not off the rows, and that is the whole of what makes it a guard. jsdom
 * runs no animation, so a menu the library has closed keeps its `data-ending-style` node in the
 * document for ever and every row is still queryable: `rows()` after a close returns all three
 * and says nothing at all. The trigger's `aria-expanded` is the library's own answer to the
 * question, and it is the one a screen reader is given.
 */
function menuIsOpen(): boolean {
	return (
		document.querySelector('[data-dropdown-menu-trigger]')?.getAttribute('aria-expanded') === 'true'
	);
}

/** Shift and a left press on a row: the same act as the right button, for a trackpad. */
function shiftPress(words: string): void {
	rowSaying(words).dispatchEvent(
		new MouseEvent('click', { shiftKey: true, bubbles: true, cancelable: true, button: 0 })
	);
	flushSync();
}

describe('the row that opens out into the list', () => {
	it('is a row that opens out, not a row that does something', async () => {
		// "Add to > Collection" opens the list itself, not a sheet over the screen: a verb carrying
		// a list has to reach `PickMenu` and not the plain row beside it.
		await openTheList();

		const trigger = [...document.querySelectorAll('[role="menuitem"]')].find((row) =>
			(row.textContent ?? '').includes('Collection')
		);
		expect(trigger?.getAttribute('aria-haspopup')).toBe('menu');
	});

	it('leaves a verb with no list as an ordinary row', async () => {
		await openTheList();

		const hide = [...document.querySelectorAll('[role="menuitem"]')].find((row) =>
			(row.textContent ?? '').includes('Hide')
		);
		expect(hide?.getAttribute('aria-haspopup')).toBe(null);
	});

	it('asks for the page when it is opened, and not before', async () => {
		ASKED.mockClear();
		takeDown();
		host = document.createElement('div');
		document.body.appendChild(host);
		instance = mount(RowMenu, {
			target: host,
			props: { verbs: verbs(), ids: ['f1'], label: 'More' }
		});
		flushSync();
		host.querySelector('button')?.click();
		flushSync();
		// The menu is open and the row is drawn. Five of these on one menu, and asking every store
		// for a page on a right-click is five requests nobody asked for.
		expect(ASKED).not.toHaveBeenCalled();

		const trigger = [...document.querySelectorAll('[role="menuitem"]')].find((row) =>
			(row.textContent ?? '').includes('Collection')
		);
		(trigger as HTMLElement).click();
		flushSync();

		expect(ASKED).toHaveBeenCalledWith('');
	});

	it('asks immediately on opening, without waiting out the debounce', async () => {
		// A pause is for a RUN of keystrokes. Opening is one event, and a flyout that sits empty for
		// a tenth of a second before its first row reads as a slow application.
		ASKED.mockClear();
		await openTheList();

		expect(ASKED).toHaveBeenCalledTimes(1);
		expect(rows()).toEqual(['Amber', 'Blue hour', 'Dune']);
	});
});

describe('the picture on a row', () => {
	/* People, with the address the host has already resolved for each of them. The resolving is
	   `FileVerbs`' (which of an upload, a face track and a chosen frame a person's cover is) and
	   a row is handed the answer rather than the question. */
	const PEOPLE: Verb[] = [
		{
			id: 'assign',
			label: 'Person',
			icon: 'person',
			run: () => {},
			pick: {
				kind: 'person',
				plural: 'people',
				ask: async () => ({
					choices: [
						{ id: 'p-1', name: 'Marisol Vane', picture: { src: '/api/people/p-1/cover' } },
						{ id: 'p-2', name: 'Teodor Blyth', picture: { src: '/api/people/p-2/cover' } }
					],
					more: 0
				}),
				pick: async () => 'landed' as const
			}
		}
	];

	/** The same flyout, opened over any page the host cares to answer with. */
	async function openWith(
		choices: { id: string; name: string; picture?: { src: string } }[]
	): Promise<void> {
		await openTheList(verbs({ ask: async () => ({ choices, more: 0 }) }));
	}

	it('draws the face the host resolved, one per row, without asking for it', async () => {
		// A wall of people is read by the face before the name. The addresses come down with the
		// page, so a flyout of a thousand rows makes no request of its own to draw them.
		await openTheList(PEOPLE, 'Person');

		const faces = [...document.querySelectorAll('.pick .face img')].map((one) =>
			one.getAttribute('src')
		);
		expect(faces).toEqual(['/api/people/p-1/cover', '/api/people/p-2/cover']);
	});

	it("wears the verb's own glyph where nobody has chosen a cover", async () => {
		/*
		 * A collection and a photo set can have a cover (both records carry the fields and both
		 * walls draw them), so the glyph fallback is for a cover nobody has picked. It sits in the
		 * same box a picture would, so the names still line up down the flyout.
		 */
		await openTheList();

		expect(document.querySelectorAll('.pick .face img').length).toBe(0);
		expect(document.querySelectorAll('.pick .face.glyph').length).toBe(CHOICES.length);
	});

	it('draws a collection by its cover, the same way it draws a person by their face', async () => {
		// The row draws whatever picture the host resolved, and the host resolves one for a
		// collection as it does for a person, so no surface shows these as anonymous rows.
		await openWith([
			{ id: 'c-1', name: 'Late nights', picture: { src: '/api/collections/c-1/cover' } },
			{ id: 'c-2', name: 'Keep' }
		]);

		const drawn = [...document.querySelectorAll('.pick .face img')].map((one) =>
			one.getAttribute('src')
		);
		expect(drawn).toEqual(['/api/collections/c-1/cover']);
		// And the one with no cover still holds its place in the column.
		expect(document.querySelectorAll('.pick .face.glyph').length).toBe(1);
	});
});

describe('a list of tags', () => {
	/* The fifth kind, tags, uses the same row, order, ceiling and memory as every other kind. */
	const TAGS: Verb[] = [
		{
			id: 'tag',
			label: 'Tag',
			icon: 'shoppingmode',
			run: () => {},
			pick: {
				kind: 'tag',
				plural: 'tags',
				ask: async () => ({
					choices: [
						{ id: 't-1', name: 'daylight' },
						{ id: 't-2', name: 'portrait' }
					],
					more: 0
				}),
				pick: async () => 'landed' as const
			}
		}
	];

	it('opens out into the list, exactly as the other four do', async () => {
		await openTheList(TAGS, 'Tag');
		expect(rows()).toEqual(['daylight', 'portrait']);
	});

	it('wears the tag glyph on every row, because a tag has no picture and never will', async () => {
		await openTheList(TAGS, 'Tag');

		expect(document.querySelectorAll('.pick .face img').length).toBe(0);
		expect(document.querySelectorAll('.pick .face.glyph').length).toBe(2);
	});
});

describe('the list inside it', () => {
	it("is the server's own order until this account has picked something", async () => {
		await openTheList();
		expect(rows()).toEqual(['Amber', 'Blue hour', 'Dune']);
	});

	it('puts what gets picked in front, and leaves the rest alphabetical under it', async () => {
		noteUse('collection', { id: 'c-dune', name: 'Dune' });
		noteUse('collection', { id: 'c-dune', name: 'Dune' });

		await openTheList();

		expect(rows()).toEqual(['Dune', 'Amber', 'Blue hour']);
	});

	it('draws a remembered row that is not on the page at all', async () => {
		// The point of holding the NAME beside the id. A page is twenty-five rows of perhaps a
		// thousand, so a remembered row is very often not in it, and a picker that could only show
		// what the page happened to hold would have no memory worth having.
		noteUse('collection', { id: 'c-far', name: 'Zephyr' });

		await openTheList();

		expect(rows()).toEqual(['Zephyr', 'Amber', 'Blue hour', 'Dune']);
	});

	it('draws a remembered row under the name it has now, not the one it was picked under', async () => {
		// The record keeps the id; its copy of the name would go on offering a renamed collection
		// under its old name until it was picked again.
		noteUse('collection', { id: 'c-far', name: 'Zephyr' });
		naming.names['c-far'] = 'Zephyr Coast';

		await openTheList();

		expect(rows()).toEqual(['Zephyr Coast', 'Amber', 'Blue hour', 'Dune']);
	});

	it('draws nothing for a remembered row the library no longer has', async () => {
		noteUse('collection', { id: 'c-far', name: 'Zephyr' });
		delete naming.names['c-far'];

		await openTheList();

		expect(rows()).toEqual(['Amber', 'Blue hour', 'Dune']);
	});

	it('marks a row that is in front because it was picked lately, and no other', async () => {
		/*
		 * The front rows wear a History mark with a tooltip saying why, so the order reads as a
		 * rule.
		 */
		noteUse('collection', { id: 'c-far', name: 'Zephyr' });

		await openTheList();

		const marked = [...document.querySelectorAll('[role="menuitem"], [role="menuitemcheckbox"]')]
			.filter((row) => row.querySelector('[aria-label="Chosen recently"]'))
			.map((row) => row.querySelector('.name')?.textContent?.trim());
		expect(marked).toEqual(['Zephyr']);
	});

	it('puts the last FIVE picked in front, newest first, and the rest alphabetical', async () => {
		/*
		 * The memory keeps the last five picks, newest first, rather than ranking by count: a fresh
		 * pick must go to the top, and only those five sit above the alphabetical list. Blue hour
		 * was picked most often and longest ago, so it falls out of the five and back into its
		 * alphabetical place.
		 */
		for (let at = 0; at < 4; at += 1) noteUse('collection', { id: 'c-blue', name: 'Blue hour' });
		for (const name of ['Quartz', 'Rowan', 'Sable', 'Tundra', 'Umber']) {
			noteUse('collection', { id: `c-${name}`, name });
			naming.names[`c-${name}`] = name;
		}
		noteUse('collection', { id: 'c-dune', name: 'Dune' });

		await openTheList();

		expect(rows()).toEqual(['Dune', 'Umber', 'Tundra', 'Sable', 'Rowan', 'Amber', 'Blue hour']);
	});

	it('writes with the one that was pressed, whole, and remembers it', async () => {
		PICKED.mockClear();
		await openTheList();

		press('Amber');

		// The ids the menu was opened on, and the row itself, which carries the name, so the toast
		// can say what it did rather than counting.
		expect(PICKED).toHaveBeenCalledWith(['f1'], { id: 'c-amber', name: 'Amber' });

		await openTheList();
		expect(rows()[0]).toBe('Amber');
	});

	it('says so while the page is still on its way', async () => {
		takeDown();
		host = document.createElement('div');
		document.body.appendChild(host);
		instance = mount(RowMenu, {
			target: host,
			props: {
				verbs: verbs({ ask: () => new Promise<PickPage>(() => {}) }),
				ids: ['f1'],
				label: 'More'
			}
		});
		flushSync();
		host.querySelector('button')?.click();
		flushSync();
		const trigger = [...document.querySelectorAll('[role="menuitem"]')].find((row) =>
			(row.textContent ?? '').includes('Collection')
		);
		(trigger as HTMLElement).click();
		flushSync();

		expect(rows()).toEqual([]);
		expect(document.querySelector('.pick .waiting')).not.toBe(null);
	});

	it('says so when there is nothing to pick at all', async () => {
		await openTheList(verbs({ ask: async () => ({ choices: [], more: 0 }) }));

		expect(document.querySelector('.pick')?.textContent).toContain('No collections yet.');
	});
});

describe('the last row, which says what is not on the list', () => {
	/*
	 * A ceiling is fine; a silent one is not. A panel drawing sixty rows of a list ordered by size
	 * would leave a library with sixty-four tags with four that cannot be reached from it, and
	 * nothing saying so.
	 */
	it('says how many more there are and what to do about it', async () => {
		await openTheList(verbs({ ask: async (typed) => pageFor(typed, CHOICES, 4) }));

		expect(restLine()).toBe('4 more \u2014 keep typing to filter');
	});

	it('is not a row: there is nothing to press and nothing to arrow onto', async () => {
		await openTheList(verbs({ ask: async (typed) => pageFor(typed, CHOICES, 4) }));

		expect(rows()).toEqual(['Amber', 'Blue hour', 'Dune']);
	});

	it('is not drawn when the page is the whole list', async () => {
		await openTheList();

		expect(restLine()).toBe(null);
	});
});

describe('the box at the top', () => {
	/*
	 * Driven through the inline menu, not the flyout. A submenu inside `RowMenu` closes itself
	 * between ten and twenty milliseconds after opening in jsdom, and the box waits out a 120 ms
	 * pause before it asks, so a filtered flyout can never be looked at. The inline menu is the same
	 * component drawn as a menu's whole content, the shape a person's or site's header wears, so
	 * nothing here is a stand-in.
	 */
	const PICKED_INLINE = vi.fn();
	const MADE_INLINE = vi.fn(async (name: string) => ({ id: 't-made', name }));
	const TAGS = [
		{ id: 't-amber', name: 'amber' },
		{ id: 't-blue', name: 'blue hour' },
		{ id: 't-dune', name: 'dune' }
	];
	const ASKED_INLINE = vi.fn(async (typed: string) => pageFor(typed, TAGS));

	/** Open the button's menu, which IS the list, and let its first page land. */
	async function openTheBox(
		over: { ask?: (typed: string) => Promise<PickPage>; making?: boolean } = {}
	): Promise<void> {
		takeDown();
		host = document.createElement('div');
		document.body.appendChild(host);
		instance = mount(Probe, {
			target: host,
			props: {
				ask: over.ask ?? ASKED_INLINE,
				onpick: PICKED_INLINE,
				...(over.making === false ? {} : { oncreate: MADE_INLINE })
			}
		});
		flushSync();
		host.querySelector('button')?.click();
		flushSync();
		await settle();
	}

	it('is named for what it narrows', async () => {
		await openTheBox();
		expect(box().getAttribute('aria-label')).toBe('Filter the tags list');
	});

	it('asks the server again rather than filtering what is in hand', async () => {
		// Filtering here could only ever rearrange the page it was handed, so a name on page two
		// could not be found by typing it. The filtering belongs to the only thing holding the
		// whole list.
		await openTheBox();
		ASKED_INLINE.mockClear();

		await type('am');

		expect(ASKED_INLINE).toHaveBeenLastCalledWith('am');
		// "Make am" stays offered: nothing is called exactly that, and filtering to one row is not
		// the same as having found the row somebody meant.
		expect(rows()).toEqual(['amber', 'Create am']);
	});

	it('asks once for a run of keystrokes, not once per key', async () => {
		await openTheBox();
		ASKED_INLINE.mockClear();

		const input = box();
		for (const text of ['a', 'am', 'amb']) {
			input.value = text;
			input.dispatchEvent(new Event('input', { bubbles: true }));
			flushSync();
		}
		await vi.advanceTimersByTimeAsync(200);
		await settle();

		expect(ASKED_INLINE).toHaveBeenCalledTimes(1);
		expect(ASKED_INLINE).toHaveBeenCalledWith('amb');
	});

	/*
	 * Enter, which is also the only thing about this box's keyboard a test here can see.
	 *
	 * The box stops every printable key from reaching the menu, so a letter filters rather than
	 * jumping to a row. That half is unverified here and deliberately not faked: the library's
	 * typeahead does not run in jsdom at all, so asserting the guard would assert that nothing
	 * happened where nothing happens either way. What these prove is that the handler is reached
	 * and classifies the key, which is the handler the stopping lives in.
	 */
	function enter(): void {
		box().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		flushSync();
	}

	it('picks on Enter where exactly one thing is left', async () => {
		PICKED_INLINE.mockClear();
		await openTheBox();

		await type('dun');
		enter();

		expect(PICKED_INLINE).toHaveBeenCalledWith({ id: 't-dune', name: 'dune' });
	});

	it('picks nothing on Enter while several still match', async () => {
		// It must never guess. A box that writes to the library on whichever row sorted first is a
		// box that files things under the wrong person for a living; the rows are one key away.
		PICKED_INLINE.mockClear();
		await openTheBox({ making: false });

		await type('e');
		enter();

		expect(PICKED_INLINE).not.toHaveBeenCalled();
	});

	it('picks nothing on Enter over one row while the server is holding more back', async () => {
		// One row DRAWN is not one row MATCHING. The last line says there are others, so the one on
		// screen is a guess exactly like any other.
		PICKED_INLINE.mockClear();
		await openTheBox({
			ask: async (typed) => ({ choices: pageFor(typed, TAGS).choices.slice(0, 1), more: 2 }),
			making: false
		});

		await type('e');
		enter();

		expect(PICKED_INLINE).not.toHaveBeenCalled();
	});

	/*
	 * The same three cases again with creating offered, which is the arrangement a person is
	 * actually in: Enter must not create while several rows are on screen. Typing "be" with Beach
	 * and Bedroom showing, Enter must not make a tag called "be", since "no row is exactly this" is
	 * all the Create row knows, and that is the ordinary state of a list halfway through filtering.
	 * The tests above pass `making: false`, so they cannot see this branch.
	 */
	it('creates nothing on Enter while several rows still match', async () => {
		PICKED_INLINE.mockClear();
		MADE_INLINE.mockClear();
		await openTheBox();

		// Every one of the three carries an "e", and none of them is called "e".
		await type('e');
		expect(rows()).toContain('Create e');
		enter();

		expect(MADE_INLINE).not.toHaveBeenCalled();
		expect(PICKED_INLINE).not.toHaveBeenCalled();
	});

	it('creates nothing on Enter over one row while the server is holding more back', async () => {
		PICKED_INLINE.mockClear();
		MADE_INLINE.mockClear();
		await openTheBox({
			ask: async (typed) => ({ choices: pageFor(typed, TAGS).choices.slice(0, 1), more: 2 })
		});

		await type('e');
		enter();

		expect(MADE_INLINE).not.toHaveBeenCalled();
		expect(PICKED_INLINE).not.toHaveBeenCalled();
	});

	it('creates on Enter only where nothing here is called that at all', async () => {
		PICKED_INLINE.mockClear();
		MADE_INLINE.mockClear();
		await openTheBox();

		await type('Harbour lights');
		expect(rows()).toEqual(['Create Harbour lights']);
		enter();

		expect(MADE_INLINE).toHaveBeenCalledWith('Harbour lights');
	});

	it('moves the highlight onto the first row instead, so the next press lands somewhere', async () => {
		// The half that makes the refusal above usable rather than merely safe: the first Enter
		// over a filtered list does what the down arrow does, and creates nothing.
		await openTheBox();

		await type('e');
		enter();
		await settle();

		const first = document.querySelector(ANY_ROW);
		expect(first?.getAttribute('data-highlighted')).not.toBeNull();
		expect(document.activeElement).toBe(first);
	});

	it('offers to make one when nothing here is called that', async () => {
		await openTheBox();

		await type('Harbour lights');

		expect(rows()).toEqual(['Create Harbour lights']);
	});

	it('does not offer to make one that already exists', async () => {
		await openTheBox();

		await type('amber');

		expect(rows()).toEqual(['amber']);
	});

	it('makes it and picks it in one press', async () => {
		MADE_INLINE.mockClear();
		PICKED_INLINE.mockClear();
		await openTheBox();

		await type('Harbour lights');
		press('Create Harbour lights');
		await settle();

		expect(MADE_INLINE).toHaveBeenCalledWith('Harbour lights');
		expect(PICKED_INLINE).toHaveBeenCalledWith({ id: 't-made', name: 'Harbour lights' });
	});

	it('takes the last line away once typing has narrowed the list to what fits', async () => {
		// Written here rather than beside the other three, because it is the only one of them that
		// needs typing, and typing cannot be done to a flyout that has already closed itself.
		let more = 4;
		await openTheBox({ ask: async (typed) => pageFor(typed, TAGS, more) });
		expect(restLine()).not.toBe(null);

		more = 0;
		await type('am');

		expect(restLine()).toBe(null);
	});

	it('says how many did not fit here too, in the same words', async () => {
		// The header of a person or a site draws this same list. The row that says what is missing
		// has to be there on both surfaces, or one of them is a silent cap.
		await openTheBox({ ask: async (typed) => pageFor(typed, TAGS, 7) });

		expect(restLine()).toBe('7 more \u2014 keep typing to filter');
	});

	/*
	 * A press is not a new opening, and this form is the one that could tell the difference least.
	 *
	 * The flyout arranges itself from `opened`, an event handler, so nothing it reads is a
	 * dependency. Drawn inline there is no open event, so the arranging is an effect, and one thing
	 * it reads is the memory `choose` writes before moving the mark. That read must not re-run the
	 * arranging, or a press would empty the box and bring the filtered list back whole. The bulk
	 * sheets hold the same rule.
	 */
	it('leaves the narrowing standing when a row is pressed', async () => {
		PICKED_INLINE.mockClear();
		await openTheBox();

		await type('am');
		expect(rows()).toEqual(['amber', 'Create am']);

		// A RIGHT press, because a left one now finishes and takes the menu with it, and what this
		// test is about is the list SURVIVING a press, which is only a question while it is still up.
		rightPress('amber');

		expect(PICKED_INLINE).toHaveBeenCalledWith({ id: 't-amber', name: 'amber' });
		expect(box().value).toBe('am');
		expect(rows()).toEqual(['amber', 'Create am']);
	});

	/*
	 * Escape, twice, in this order. The first press empties the box, which asks the server for the
	 * whole page again exactly as deleting the words would, and the list stays up; the second finds
	 * nothing to empty and closes it.
	 */
	it('empties the box on the first Escape and closes on the second', async () => {
		await openTheBox();
		await type('am');
		expect(rows()).toEqual(['amber', 'Create am']);

		const escape = (): void => {
			box().dispatchEvent(
				new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
			);
			flushSync();
		};

		escape();
		await vi.advanceTimersByTimeAsync(200);
		await settle();
		expect(box().value).toBe('');
		expect(rows()).toEqual(['amber', 'blue hour', 'dune']);
		expect(menuIsOpen()).toBe(true);

		escape();
		expect(menuIsOpen()).toBe(false);
	});
});

describe('the arrows', () => {
	/*
	 * They belong to `Scroller`, which has an `arrows` prop: when each one
	 * exists, what a nudge is worth and the pace of a hold are all its business and are tested
	 * beside it. What is asserted here is only that this flyout ASKS for them, because a list that
	 * opens over the page has nothing else saying there are more rows past the edge.
	 *
	 * jsdom gives an element no layout at all, so the region has nothing to measure and neither
	 * arrow can appear on its own; these lie to it about one scrolling box and then tell it to look
	 * again.
	 */
	function pretendItScrolls(height: number, showing: number, at: number): HTMLElement {
		const boxes = [...document.querySelectorAll('[data-scroll-area-viewport]')];
		const viewport = boxes[boxes.length - 1];
		if (!(viewport instanceof HTMLElement)) throw new Error('there is no region to scroll');
		Object.defineProperty(viewport, 'scrollHeight', { value: height, configurable: true });
		Object.defineProperty(viewport, 'clientHeight', { value: showing, configurable: true });
		viewport.scrollTop = at;
		viewport.dispatchEvent(new Event('scroll'));
		flushSync();
		return viewport;
	}

	function arrows(): number {
		return document.querySelectorAll('.pick .scroll-nudge').length;
	}

	it('are not there while the list fits', async () => {
		// An arrow that cannot do anything is a control somebody presses twice before deciding the
		// application is broken.
		await openTheList();

		expect(arrows()).toBe(0);
	});

	it('appear at the end there is something past', async () => {
		await openTheList();
		pretendItScrolls(600, 100, 0);

		expect(arrows()).toBe(1);
	});

	it('appear at both ends in the middle of a long list', async () => {
		await openTheList();
		pretendItScrolls(600, 100, 250);

		expect(arrows()).toBe(2);
	});
});

describe('what this selection is already on', () => {
	/*
	 * The tick is the whole of what makes several picks in one opening safe.
	 *
	 * Without it the flyout is a list that can be pressed repeatedly with nothing on screen saying
	 * what each press did, and since a press now toggles, pressing the same row twice would put a
	 * file on and take it off again with no way to tell from the list which of those had happened.
	 */
	beforeEach(() => {
		PICKED.mockClear();
		UNPICKED.mockClear();
	});

	it('ticks a row every file is on, and half-ticks one only some are', async () => {
		await openTheList(verbs({ already: { 'c-amber': 'all', 'c-blue': 'some' } }));

		// Amber, Blue hour, Dune, and the create row is absent with nothing typed.
		expect(rows()).toEqual(['Amber', 'Blue hour', 'Dune']);
		expect(marks()).toEqual(['true', 'mixed', 'false']);
	});

	it('draws no marks at all where nothing was asked', async () => {
		// The header pickers open on a person rather than on a selection of files, so there is no
		// set to compare against. A flyout of empty boxes there would invent a question nobody
		// asked, so the rows stay plain menu rows, which is also what keeps one keyboard
		// behaviour for one list.
		await openTheList();

		expect(rows()).toEqual(['Amber', 'Blue hour', 'Dune']);
		expect(marks()).toEqual([null, null, null]);
	});

	it('takes the files off a row that is already ticked, and clears the tick', async () => {
		await openTheList(verbs({ already: { 'c-amber': 'all' } }));

		rightPress('Amber');

		expect(UNPICKED).toHaveBeenCalledTimes(1);
		// The whole set, and the row that was pressed. The set is bound by `VerbMenuItems`, which is
		// the one thing that knows whether a menu is about its own row or about a whole selection.
		expect(UNPICKED.mock.calls[0]).toEqual([['f1'], { id: 'c-amber', name: 'Amber' }]);
		expect(PICKED).not.toHaveBeenCalled();
		expect(marks()).toEqual(['false', 'false', 'false']);
	});

	it('puts them all on a row only some are on, rather than taking the rest off', async () => {
		// The other direction has no sentence behind it: it would take twelve files out of a
		// collection and leave twenty-eight in, which is not what anybody pressed for.
		await openTheList(verbs({ already: { 'c-blue': 'some' } }));

		rightPress('Blue hour');

		expect(PICKED).toHaveBeenCalledTimes(1);
		expect(UNPICKED).not.toHaveBeenCalled();
		expect(marks()).toEqual(['false', 'true', 'false']);
	});

	it('gives the half tick a label somebody can point at, and the other two none', async () => {
		/*
		 * The half tick has a tooltip, and it can be reached: the bar is eight pixels by two, so
		 * the cell around it is the target. A full tick and a blank say what they mean, so neither
		 * carries a label; a tooltip on every row would be three labels where one fact needed
		 * explaining.
		 */
		await openTheList(verbs({ already: { 'c-amber': 'all', 'c-blue': 'some' } }));

		/* `Tooltip` wraps what it labels in `.wrap > .target`, so a cell whose parent is the ROW
		   itself carries no label and one sitting inside a target does. */
		const labelled = [...document.querySelectorAll(ANY_ROW)].map((row) => {
			const cell = row.querySelector('.on-cell');
			return cell?.parentElement?.classList.contains('target') ? 'labelled' : null;
		});
		expect(rows()).toEqual(['Amber', 'Blue hour', 'Dune']);
		expect(labelled).toEqual([null, 'labelled', null]);

		// The mark still sits in a cell on every row, labelled or not, so the names line up.
		expect(document.querySelectorAll('.pick .on-cell')).toHaveLength(3);
	});

	it('stays open after a RIGHT press, so the next one is one more press', async () => {
		/*
		 * A right press keeps the flyout up for more picks; a left press finishes. The common case
		 * is one pick, which should not leave a menu sitting over the thing just filed.
		 */
		await openTheList(verbs({ already: {} }));

		rightPress('Amber');
		rightPress('Dune');

		expect(PICKED).toHaveBeenCalledTimes(2);
		expect(menuIsOpen()).toBe(true);
		expect(rows()).toEqual(['Amber', 'Blue hour', 'Dune']);
		expect(marks()).toEqual(['true', 'false', 'true']);
	});

	/*
	 * A refused write puts the tick back.
	 *
	 * The tick moves on the press, since a row waiting for the round trip reads as a menu ignoring
	 * clicks, and the write answers with what landed; a refusal (a locked vault, a file somebody
	 * else owns) must not leave the row saying the file is on something it is not.
	 */
	it('puts the tick back where the server refused the pick', async () => {
		await openTheList(verbs({ already: {}, pick: async () => 'refused' }));

		rightPress('Amber');
		expect(marks()).toEqual(['true', 'false', 'false']);
		await settle();

		expect(marks()).toEqual(['false', 'false', 'false']);
	});

	it('puts the tick back where the server refused to take them off', async () => {
		await openTheList(verbs({ already: { 'c-amber': 'all' }, unpick: async () => 'refused' }));

		rightPress('Amber');
		await settle();

		expect(marks()).toEqual(['true', 'false', 'false']);
	});

	it('remembers a pick that landed and not one the server refused', async () => {
		/*
		 * Right-presses refused by the server (a hidden file in a locked vault) must not move a
		 * collection to the head of the list: only a reach that happened is remembered.
		 */
		await openTheList(verbs({ already: {}, pick: async () => 'refused' }));
		rightPress('Amber');
		await settle();
		expect(remembered('collection').map((one) => one.name)).toEqual([]);

		takeDown();
		await openTheList(verbs({ already: {}, pick: async () => 'landed' }));
		rightPress('Amber');
		await settle();
		expect(remembered('collection').map((one) => one.name)).toEqual(['Amber']);
	});

	it('keeps the tick where the pick landed', async () => {
		await openTheList(verbs({ already: {}, pick: async () => 'landed' }));

		rightPress('Amber');
		await settle();

		expect(marks()).toEqual(['true', 'false', 'false']);
	});

	it('asks again what the files are on where only some of them went', async () => {
		/* The server counts what it left out, not which files, so "some" is the only honest mark,
		   and the exact one is the host's answer to the same question the flyout opened with. */
		let asked = 0;
		const already = async (): Promise<Record<string, OnAlready>> => {
			asked += 1;
			return asked === 1 ? {} : { 'c-amber': 'some' };
		};
		await openTheList(verbs({ already, pick: async () => 'partly' }));

		rightPress('Amber');
		await settle();
		await settle();

		expect(asked).toBe(2);
		expect(marks()).toEqual(['mixed', 'false', 'false']);
	});

	it('takes the picked mark off a plain row whose pick was refused', async () => {
		/* The header pickers have no membership question, so the mark is "picked in this opening",
		   and a pick that did not happen was not picked. */
		const PLAIN: Verb[] = [
			{
				id: 'collect',
				label: 'Collection',
				icon: 'box',
				run: () => {},
				pick: {
					kind: 'collection',
					plural: 'collections',
					ask: ASKED,
					pick: async () => 'refused'
				}
			}
		];
		await openTheList(PLAIN);

		rightPress('Amber');
		await settle();

		const filled = [...document.querySelectorAll(ANY_ROW)].map(
			(row) => row.querySelector('.on-cell .bare')?.classList.contains('on') ?? false
		);
		expect(filled).toEqual([false, false, false]);
	});

	it('refuses the browser its own menu over a row', async () => {
		// Without this the app's menu and the browser's are on screen at the same time, and the one
		// somebody right-clicked with is behind the one they did not ask for.
		await openTheList(verbs({ already: {} }));

		expect(rightPress('Amber').defaultPrevented).toBe(true);
	});
});

describe('the two gestures', () => {
	beforeEach(() => {
		PICKED.mockClear();
	});

	it('acts on the one row and closes the menu on a LEFT press', async () => {
		await openTheList(verbs({ already: {} }));

		press('Amber');

		expect(PICKED).toHaveBeenCalledWith(['f1'], { id: 'c-amber', name: 'Amber' });
		// The whole menu, not only the flyout: a menu whose work is done goes away, which is what a
		// menu row has meant everywhere else in this application since there have been menus.
		expect(menuIsOpen()).toBe(false);
	});

	it('acts and stays up on Shift and a left press, which is the right press for a trackpad', async () => {
		// Taken before the library sees the press: a plain left press selects and closes, and this
		// one must do the first and not the second.
		await openTheList(verbs({ already: {} }));

		shiftPress('Amber');

		expect(PICKED).toHaveBeenCalledWith(['f1'], { id: 'c-amber', name: 'Amber' });
		expect(menuIsOpen()).toBe(true);
	});

	it('marks a plain row picked in this opening, where there is nothing to compare against', async () => {
		/* The header pickers have no membership question to ask, so this mark is all they can show
		   while a right press keeps the flyout up. Picking four people out of a list of a thousand
		   with no mark on any of them is counting in your head. */
		await openTheList();

		rightPress('Amber');

		const filled = [...document.querySelectorAll(ANY_ROW)].map(
			(row) => row.querySelector('.on-cell .bare')?.classList.contains('on') ?? false
		);
		expect(filled).toEqual([true, false, false]);
	});

	it('says at the foot what the second gesture is', async () => {
		// An affordance nothing on screen mentions is an affordance nobody has.
		await openTheList();

		expect(document.querySelector('.pick .hint')?.textContent?.trim()).toBe(
			'Right-click or Shift+click to select more than one'
		);
	});
});

/*
 * THE BOX AT THE OTHER END.
 *
 * A menu that opens UPWARDS puts its first row against the trigger and its last furthest away, so
 * a box at the top is at the far end of the flyout from the thing that was pressed. The faces
 * screen's naming control is exactly that (its bar is at the foot of the window) and one prop
 * moves the box rather than a second component being written against a bar.
 *
 * Two things are asserted and the second is the one worth having: the box is genuinely LAST in the
 * markup, so what a screen reader walks and what the eye walks are the same list; and Enter over a
 * filtered list walks UP into the rows, because down out of a box at the foot of a menu walks off
 * the end of it and nothing moves at all.
 */
describe('the box at the bottom', () => {
	const ROWS = [
		{ id: 't-amber', name: 'amber' },
		{ id: 't-blue', name: 'blue hour' },
		{ id: 't-dune', name: 'dune' }
	];

	/** Enough rows that the list must scroll, which is when the box's place stops being free. */
	const MANY = Array.from({ length: 61 }, (_, at) => ({
		id: `t${at}`,
		name: `person ${String(at).padStart(2, '0')}`
	}));

	/** The same inline probe the header wears, at whichever end the box was asked for. */
	async function openInline(
		over: { filterAt?: 'top' | 'bottom'; rows?: typeof ROWS } = {}
	): Promise<void> {
		takeDown();
		host = document.createElement('div');
		document.body.appendChild(host);
		instance = mount(Probe, {
			target: host,
			props: {
				ask: async (typed: string) => {
					const all = over.rows ?? ROWS;
					const page = pageFor(typed, all);
					// The server's own shape: one page, and how many it is holding back.
					return {
						choices: page.choices.slice(0, 10),
						more: Math.max(0, page.choices.length - 10)
					};
				},
				onpick: vi.fn(),
				oncreate: async (name: string) => ({ id: 't-made', name }),
				...(over.filterAt ? { filterAt: over.filterAt } : {})
			}
		});
		flushSync();
		host.querySelector('button')?.click();
		flushSync();
		await settle();
	}

	it('puts the box after the rows in the markup, not only below them', async () => {
		await openInline({ filterAt: 'bottom' });

		const pick = document.querySelector('.pick');
		const parts = [...(pick?.children ?? [])].map((one) => one.className.split(' ')[0]);
		// The foot line is last either way round: it is about the LIST and not a member of it, so it
		// sits under whichever end the box is at. See `.hint`.
		expect(parts).toEqual(['list', 'narrowing', 'hint']);
		expect(document.querySelector('.narrowing')?.classList.contains('under')).toBe(true);
	});

	it('leaves the box at the top by default', async () => {
		await openInline();

		const pick = document.querySelector('.pick');
		const parts = [...(pick?.children ?? [])].map((one) => one.className.split(' ')[0]);
		expect(parts).toEqual(['narrowing', 'list', 'hint']);
	});

	it('keeps the box OUT of the scrolling region, so it is at the menu edge and not past the rows', async () => {
		/*
		 * The box must not end up inside a scrolling region of its own. Two nested scrolling
		 * regions hand the menu's height to the outer one, so the list has nothing to shrink
		 * against, is laid out at full content height, and the box after it lands far off screen.
		 *
		 * So the assertion is structural and needs no layout: the box is not inside any scrolling
		 * viewport, which makes it the menu's own last row. See `MenuButton`'s `scrolls`.
		 */
		await openInline({ filterAt: 'bottom', rows: MANY });

		const input = box();
		expect(input.closest('[data-scroll-area-viewport]')).toBeNull();
		expect(input.closest('.list')).toBeNull();
		// And the rows it stands under ARE in one, which is the half that proves the first half.
		const row = document.querySelector(ANY_ROW);
		expect(row?.closest('[data-scroll-area-viewport]')).not.toBeNull();
	});

	it('puts the ceiling line and the create row nearest the box, reading bottom-up', async () => {
		/* Reading up from the box: the row that makes one under what was typed, then the line saying
		   how many are not shown, then the list. The same order as the top placement: the list is
		   not reversed, because a-z downward is what a list is. */
		await openInline({ filterAt: 'bottom', rows: MANY });
		await type('person');

		const tail = [...document.querySelectorAll(`${ANY_ROW}, .pick .rest`)]
			.slice(-2)
			.map((one) => (one.textContent ?? '').replace(/[\uE000-\uF8FF]/g, '').trim());
		expect(tail[0]).toBe('51 more \u2014 keep typing to filter');
		expect(tail[1]).toBe('Create person');
	});

	it('walks UP into the rows when the box is under them', async () => {
		await openInline({ filterAt: 'bottom' });
		await type('b');

		const input = box();
		const keys: string[] = [];
		input.addEventListener('keydown', (event) => keys.push(event.key));
		input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		flushSync();

		// The Enter itself, then the arrow this component sends to move the highlight into the rows.
		expect(keys).toEqual(['Enter', 'ArrowUp']);
	});
});

describe('the row that opens out', () => {
	afterEach(removeStyles);

	it("wears the menu row's own chevron, at the far end", async () => {
		await openTheList();
		const opener = [...document.querySelectorAll('[role="menuitem"]')].find((row) =>
			(row.textContent ?? '').includes('Collection')
		);
		const chevron = opener?.querySelector('.arrow') as HTMLElement;
		applyStyles(menuRow);

		expect(getComputedStyle(chevron).display).toBe('inline-flex');
		expect(getComputedStyle(chevron).marginInlineStart).toBe('auto');
	});
});
