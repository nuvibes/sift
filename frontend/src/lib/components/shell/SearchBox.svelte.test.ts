/*
 * The search box as an object on screen: chips, the half-finished one, and Backspace.
 *
 * The logic that can be tested without a browser lives in `$lib/search/search.svelte` and is tested there.
 * What is here is the part that only exists once rendered (whether a chosen filter looks like a
 * decision, and whether one press of Backspace takes the whole of it off), because both can be
 * wrong while every unit test passes.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import { goto } from '$app/navigation';
import { availability } from '$lib/jobs/semantic-runs.svelte';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import SearchBox from './SearchBox.svelte';
import source from './SearchBox.svelte?raw';
import tabsSource from '$lib/components/common/Tabs.svelte?raw';
import { fromBar } from '$lib/shell/motion.svelte';
import { api } from '$lib/api/client';
import { FIELD_FLOOR, screenBar } from './screen-bar.svelte';
import { shiftInto } from './SearchSuggestions.svelte';
import { searchBox } from '$lib/search/search.svelte';

/* The bar's motion, watched rather than replaced, so the list can be seen asking for it. */
vi.mock('$lib/shell/motion.svelte', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/shell/motion.svelte')>();
	return { ...real, fromBar: vi.fn(real.fromBar) };
});

/*
 * The box asks the server two different questions, what a query means and what to suggest, and
 * neither is under test here. Both are answered, each with its own shape.
 *
 * One answer for both is what a fixture of the wrong shape looks like: the parse answer has no
 * `filters`, so a box put on an address carrying a query would fetch suggestions, get a parsed
 * query back, and throw while mapping over a list that is not there. Every test would still pass,
 * and the error would be reported against whichever file happened to be running.
 */
vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string) =>
			path === '/search/suggest'
				? { token: null, filters: [], matches: [], recent: [], replace_from: 0, for_query: '' }
				: { text: '', clauses: [], terms: {}, problems: [] }
		),
		del: vi.fn(async () => undefined)
	},
	ApiError: class extends Error {}
}));

/* The address the box is looking at. Held out here so a test can put the box on a page that
 * already asks for a different order, which is a state it reads and never sets. */
const at = vi.hoisted(() => ({ url: new URL('http://localhost/browse') }));

/* Whether this install can search by meaning is the server's answer, and the control is drawn only
 * when it is yes. Held out for the same reason. */
const meaning = vi.hoisted(() => ({ available: false }));

/* `afterNavigate` as well as `goto`: the box restores the caret after a navigation, and a
   stand-in missing it is not a wrong answer on screen: it is the whole file failing to
   mount. */
vi.mock('$app/navigation', () => ({ goto: vi.fn(), afterNavigate: vi.fn() }));
vi.mock('$app/state', () => ({
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false },
	page: {
		get url() {
			return at.url;
		}
	}
}));
/* The real module with ONE answer replaced, rather than a module of one export.
 *
 * A mock that lists what it provides is a second copy of the module's interface, and it goes stale
 * silently: an export moved elsewhere fails the whole file with "No export is defined on the
 * mock", which names the mock rather than the test. */
vi.mock('$lib/search/semantic.svelte', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/search/semantic.svelte')>()),
	semanticAvailable: async () => ({ available: meaning.available })
}));

let host: HTMLElement;
/** What `mount` handed back, so the box can be TAKEN DOWN rather than merely hidden. See below. */
let box: Record<string, unknown> | null = null;

afterEach(async () => {
	/*
	 * Unmounted, not removed, and this is not tidiness.
	 *
	 * Taking the element out of the document leaves the component running: its effects are still
	 * live and `onDestroy` has not been called, which is where this box stops the suggester's
	 * debounce (`searchBox.teardown()`). Left running, every test that typed would leave a 120ms
	 * timer behind, firing in whichever test came next and asking the mocked API for suggestions.
	 *
	 * It also keeps the client coverage figure stable: a leaked timer runs `#fetch` in
	 * `search.svelte.ts` when it happens to fire before the file finishes and not when it does not,
	 * and a number that changes on its own makes a coverage ratchet a coin toss.
	 */
	if (box) void unmount(box);
	box = null;
	host?.remove();
	at.url = new URL('http://localhost/browse');
	meaning.available = false;
	// The store keeps its answer for the life of the module, so a test that turned this on would
	// otherwise leave the control drawn in every test after it.
	await availability.refresh();
});

function render() {
	host = document.createElement('div');
	document.body.append(host);
	box = mount(SearchBox, { target: host });
	return host;
}

/*
 * Mount, and let the box's own start-up finish before anything is done to it.
 *
 * Without this the first `flushSync` of a test is where the mount effects run (including the one
 * that follows the address back into the box) so text put in beforehand is read as a box that
 * disagrees with the address and wiped a microtask later. That is the harness getting ahead of the
 * component, not something a person can do: in a browser the effects have run long before anybody
 * has typed.
 */
async function ready() {
	const box = render();
	flushSync();
	await Promise.resolve();
	flushSync();
	return box;
}

function field(): HTMLInputElement {
	return host.querySelector('input[type="search"]') as HTMLInputElement;
}

/** Choose a row from the dropdown the way the pointer does: mousedown, not click. */
function pick(row: HTMLElement) {
	row.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
	flushSync();
}

/** Put the dropdown into a known state, without waiting on a debounce or a request. */
function offering(box: HTMLElement, suggestions: Record<string, unknown>) {
	// The store is a module singleton shared with the component, which is what makes this possible.
	return import('$lib/search/search.svelte').then(({ searchBox }) => {
		searchBox.suggestions = {
			token: null,
			filters: [],
			matches: [],
			recent: [],
			replace_from: 0,
			for_query: '',
			...suggestions
		} as never;
		searchBox.open = true;
		flushSync();
		return box;
	});
}

const PEOPLE_FILTER = {
	field: 'people',
	label: 'Person',
	hint: 'who is in it',
	example: 'people:jane'
};

describe('choosing a filter from the dropdown', () => {
	it('says where a filter that takes an id is set, and shows no id', async () => {
		const box = render();
		const like = { field: 'like', label: 'Similar to this', hint: 'One file', example: 'like:' };
		await offering(box, { filters: [{ ...like, set_from: 'from a file' }], replace_from: 0 });

		const row = box.querySelector('[role="option"]') as HTMLElement;
		const details = [...row.querySelectorAll('.detail')].map((one) => one.textContent?.trim());
		expect(details).toEqual(['like:', 'from a file']);
	});

	it('draws it as a chip straight away, not as text in the box', async () => {
		/*
		 * Picking `people:` puts a chip in the box, not the characters: a plain word where somebody
		 * had just chosen a thing would not look like the decision it is.
		 */
		const box = render();
		await offering(box, { filters: [PEOPLE_FILTER], replace_from: 0 });

		pick(box.querySelector('[role="option"]') as HTMLElement);

		expect(box.querySelector('.chip.pending')?.textContent).toContain('people:');
		expect(field().value).toBe('');
	});

	it('takes the half-typed word it was made from out of the box', async () => {
		const box = render();
		field().value = 'peo';
		field().dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		await offering(box, { filters: [PEOPLE_FILTER], replace_from: 0 });

		pick(box.querySelector('[role="option"]') as HTMLElement);

		// `peo` became the chip. Left behind as well, it would sit under a chip already saying it.
		expect(field().value).toBe('');
	});

	it('does not eat a word that was already there', async () => {
		/*
		 * Picking the People row with `holiday peo` typed: the half-typed `peo` becomes the chip,
		 * and `holiday` must stay a word rather than becoming the filter's value, or the search
		 * answers a different question. Three things are asserted, because the first two alone pass
		 * against the fault: the word is not the value, the word is still on screen, and the filter
		 * is still waiting for a value.
		 *
		 * `ready()`, not `render()`: the box's start-up effect follows the address back into the
		 * field and wipes anything put there before it has run. See its own comment.
		 */
		const box = await ready();
		field().value = 'holiday peo';
		field().dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		// `for_query` has to match what is in the box: the component refuses an answer that describes
		// a line which has since changed. The token starts at 8: `holiday ` is eight characters.
		await offering(box, {
			filters: [PEOPLE_FILTER],
			replace_from: 8,
			for_query: 'holiday peo'
		});

		pick(box.querySelector('[role="option"]') as HTMLElement);

		expect(box.querySelector('.chip.pending')?.textContent).toContain('people:');
		expect(box.querySelector('.leading')?.textContent).toBe('holiday');
		expect(field().value, 'the word was swallowed as the filter value').toBe('');
	});

	it('and hands the word back when the filter is abandoned', async () => {
		// Held apart while the filter waits, and returned the moment it stops waiting: otherwise
		// backing out of a filter loses text that had nothing to do with it.
		// `ready()`, not `render()`: the box's start-up effect follows the address back into the field
		// and wipes anything put there before it has run. See its own comment.
		const box = await ready();
		field().value = 'holiday peo';
		field().dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		await offering(box, {
			filters: [PEOPLE_FILTER],
			replace_from: 8,
			for_query: 'holiday peo'
		});
		pick(box.querySelector('[role="option"]') as HTMLElement);

		// By its label rather than by a class: the pending chip is the shared component now, and its
		// remove control is the one that component draws. What has to keep working is "there is a
		// button that takes this filter back off", which is what the label says.
		(box.querySelector('[aria-label="Remove the people filter"]') as HTMLButtonElement).click();
		flushSync();

		expect(box.querySelector('.chip.pending')).toBeNull();
		expect(field().value).toBe('holiday ');
	});
});

describe('taking a filter back off with one press', () => {
	async function withPending() {
		const box = render();
		await offering(box, { filters: [PEOPLE_FILTER], replace_from: 0 });
		pick(box.querySelector('[role="option"]') as HTMLElement);
		return box;
	}

	function backspaceAtTheStart() {
		const input = field();
		input.setSelectionRange(0, 0);
		input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Backspace', bubbles: true }));
		flushSync();
	}

	it('removes the whole of a half-finished filter', async () => {
		const box = await withPending();
		expect(box.querySelector('.chip.pending')).not.toBeNull();

		backspaceAtTheStart();

		expect(box.querySelector('.chip.pending')).toBeNull();
	});

	it('has a button of its own for the pointer', async () => {
		const box = await withPending();

		(box.querySelector('[aria-label="Remove the people filter"]') as HTMLElement).click();
		flushSync();

		expect(box.querySelector('.chip.pending')).toBeNull();
	});

	it('leaves the text alone when the caret is not at the start', async () => {
		// A chip eaten while somebody is editing the text is a delete with no visible cause, which is
		// worse than one that needs the caret moved first.
		const box = await withPending();
		field().value = 'jane';
		field().dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();

		const input = field();
		input.setSelectionRange(4, 4);
		input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Backspace', bubbles: true }));
		flushSync();

		expect(box.querySelector('.chip.pending')).not.toBeNull();
	});
});

describe('typing a filter out by hand', () => {
	/* Put text in the box and have the server answer that the caret is inside a token, which is what
	 * really happens a debounce after the colon is typed. */
	async function typing(box: HTMLElement, text: string, answer: Record<string, unknown>) {
		field().value = text;
		field().dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		await offering(box, { for_query: text, ...answer });
	}

	it('turns into the same chip that clicking the filter makes', async () => {
		/*
		 * Clicking `tags:` gives a chip, and typing the identical thing must give the same, or
		 * nothing on screen explains which somebody is going to get.
		 */
		const box = await ready();
		await typing(box, 'tags:', { token: 'tags', replace_from: 0 });

		expect(box.querySelector('.chip.pending')?.textContent).toContain('tags:');
		// The characters became the chip, so they must not also stay behind as text.
		expect(field().value).toBe('');
	});

	it('does the same for a filter with nothing to suggest under it', async () => {
		// The point of widening the server's caret answer. `rating:` has no list of ratings to offer,
		// and that is not a reason to draw it differently from `tags:`.
		const box = await ready();
		await typing(box, 'rating:', { token: 'rating', replace_from: 0 });

		expect(box.querySelector('.chip.pending')?.textContent).toContain('rating:');
		expect(field().value).toBe('');
	});

	it('leaves a filter that already has a value being typed as it is', async () => {
		// Mid-value the field is settled and the caret is in the middle of the value. Promoting here
		// would take the text out from under somebody still typing it.
		const box = await ready();
		await typing(box, 'tags:bik', { token: 'tags', replace_from: 0 });

		expect(box.querySelector('.chip.pending')).toBeNull();
		expect(field().value).toBe('tags:bik');
	});

	it('keeps free text in front of the filter, and still makes the chip', async () => {
		/*
		 * `beach tags:` promotes: the word is held separately, so `beach` stays a word and `tags:`
		 * becomes the chip it looks like. Both are asserted, because either alone passes against a
		 * version that refuses the promotion or one that eats the word.
		 */
		const box = await ready();
		await typing(box, 'beach tags:', { token: 'tags', replace_from: 6 });

		expect(box.querySelector('.chip.pending')?.textContent).toContain('tags:');
		expect(field().value).toBe('');
		// And the word is still on screen, in front of the filter. Held apart from what is being
		// typed is not the same as held nowhere: text kept in a variable and drawn by nothing is
		// text that vanished.
		expect(box.querySelector('.leading')?.textContent).toBe('beach');
	});

	it('ignores an answer that describes a line which has since changed', async () => {
		// The dropdown is a debounce and a round trip behind the keyboard. Acting on a stale answer
		// would chip a filter out of text that is no longer there.
		const box = await ready();
		await typing(box, 'tags:', { token: 'tags', replace_from: 0, for_query: 'tag' });

		expect(box.querySelector('.chip.pending')).toBeNull();
	});
});

describe('backspacing into a finished chip', () => {
	async function withFinishedChip(value = 'bikini') {
		const box = await ready();
		await offering(box, { filters: [{ ...PEOPLE_FILTER, field: 'tags' }], replace_from: 0 });
		pick(box.querySelector('[role="option"]') as HTMLElement);
		await offering(box, {
			token: 'tags',
			matches: [{ value, detail: null, count: 1, field: 'tags' }],
			replace_from: 0,
			for_query: 'tags:'
		});
		pick(box.querySelector('[role="option"]') as HTMLElement);
		return box;
	}

	function backspaceAtTheStart() {
		const input = field();
		input.setSelectionRange(0, 0);
		input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Backspace', bubbles: true }));
		flushSync();
	}

	it('keeps the field and hands the value back as text', async () => {
		/* The wrong size of undo. Somebody backing into `tags:bikini` wants a different tag, not no
		 * tag: deleting both would mean retyping the half they had not changed their mind about. */
		const box = await withFinishedChip();
		expect(box.querySelector('.chip:not(.pending)')).not.toBeNull();

		backspaceAtTheStart();
		await tick();

		expect(box.querySelector('.chip:not(.pending)')).toBeNull();
		expect(box.querySelector('.chip.pending')?.textContent).toContain('tags:');
		expect(field().value).toBe('bikini');
	});

	it('leaves the caret at the end of the value, so the next press eats it', async () => {
		// At the start instead, the very next Backspace would meet the take-a-chip-off condition and
		// drop the field that was just deliberately kept.
		const box = await withFinishedChip();

		backspaceAtTheStart();
		await tick();

		expect(field().selectionStart).toBe('bikini'.length);
		expect(box.querySelector('.chip.pending')).not.toBeNull();
	});

	it('writes a multi-word value back quoted, so the query still means the same thing', async () => {
		/* Unquoted, `tags:"beach party"` comes back as the tag `beach` and a loose word `party`:
		 * a different search, produced by a key that is supposed to be an undo. */
		const box = await withFinishedChip('beach party');

		backspaceAtTheStart();
		await tick();

		expect(field().value).toBe('"beach party"');
		expect(box.querySelector('.chip.pending')?.textContent).toContain('tags:');
	});

	it('drops the field on the press after the value is gone', async () => {
		const box = await withFinishedChip();
		backspaceAtTheStart();
		await tick();
		// Stated rather than assumed: without it this test passes just as well against a Backspace
		// that deleted the whole chip in one, which is the behaviour it exists to rule out.
		expect(box.querySelector('.chip.pending')).not.toBeNull();

		// Everything the previous press handed back has been deleted.
		field().value = '';
		field().dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		backspaceAtTheStart();

		expect(box.querySelector('.chip.pending')).toBeNull();
		expect(box.querySelector('.chip')).toBeNull();
	});
});

describe('choosing a value for a filter already chosen', () => {
	it('turns the pair into one finished chip', async () => {
		const box = render();
		await offering(box, { filters: [PEOPLE_FILTER], replace_from: 0 });
		pick(box.querySelector('[role="option"]') as HTMLElement);

		await offering(box, {
			token: 'people',
			matches: [{ value: 'Jane Doe', detail: null, count: 2, field: 'people' }],
			replace_from: 0,
			for_query: 'people:'
		});
		pick(box.querySelector('[role="option"]') as HTMLElement);

		expect(box.querySelector('.chip.pending')).toBeNull();
		const chip = box.querySelector('.chip:not(.pending)');
		expect(chip?.textContent).toContain('people:');
		expect(chip?.textContent).toContain('Jane Doe');
	});
});

/*
 * Searching by meaning: the one control in the box that changes what a result set MEANS.
 *
 * Everything about it is decided in the markup from two facts (whether the install can answer
 * that way at all, and whether the address already asks for it) and neither is visible to the
 * tests behind the endpoint, so they are pinned here, in a rendered box.
 */
describe('the search-by-meaning control', () => {
	/* The two ways of searching are the two positions of one segmented tab row, each a glyph named
	   by its words. Two positions rather than one control that flips: with one control, the way back
	   out of meaning would be pressing again the control you had just pressed. */
	function control(): HTMLButtonElement | null {
		return host.querySelector('[role="tab"][aria-label="Search by what things look like"]');
	}

	function wordsControl(): HTMLButtonElement | null {
		return host.querySelector('[role="tab"][aria-label="Search for the words"]');
	}

	/* Mount, and wait for the one question the box asks about this on the way up.
	 *
	 * The answer is read ONCE per page load, by a store that outlives every box, so the second
	 * mount in this file asks nobody and keeps whatever the first one got. `refresh` is the way the
	 * answer changes after that, and it is what the settings pane calls when somebody flips the
	 * switch, so asking for it here is the same path rather than a test-only door.
	 */
	async function box() {
		await availability.refresh();
		render();
		flushSync();
		if (meaning.available) {
			await vi.waitFor(() => expect(control()).not.toBeNull());
		} else {
			await Promise.resolve();
			await Promise.resolve();
			flushSync();
		}
	}

	/** Where the last press asked to go. */
	function asked(): URL {
		return new URL(String(vi.mocked(goto).mock.calls.at(-1)?.[0]));
	}

	it('is not offered where pressing it would do nothing', async () => {
		await box();

		expect(control()).toBeNull();
	});

	it('appears once the install can answer that way', async () => {
		meaning.available = true;
		await box();

		expect(control()?.getAttribute('aria-selected')).toBe('false');
	});

	/* The two glyphs are one choice with two settings, so they are drawn as one control: the
	   segmented tab row, named as one choice, a sunk track with one small control's height. */
	it('draws the two ways of searching as one control', async () => {
		meaning.available = true;
		await box();

		const pair = host.querySelector('.modes [role="tablist"]') as HTMLElement;
		expect(pair.getAttribute('aria-label')).toBe('How to search');
		expect(pair.contains(control())).toBe(true);
		expect(pair.contains(wordsControl())).toBe(true);

		applyStyles(tabsSource, pair);
		try {
			expect(getComputedStyle(pair).getPropertyValue('block-size')).toBe(
				'var(--control-height-sm)'
			);
			expect(getComputedStyle(pair).background).toBe('var(--sift-surface-1)');
		} finally {
			removeStyles();
		}
	});

	/* In the box the track sits on the box's own tone: sunk to the rail's step it would be a dark
	   slab at the head of the field, the loudest thing in the bar. The one answering is still lifted. */
	it("sits the two on the box's own tone, the one answering still lifted", async () => {
		meaning.available = true;
		await box();

		const pair = host.querySelector('.modes [role="tablist"]') as HTMLElement;
		applyStyles(tabsSource, pair);
		applyStyles(source, host.querySelector('.modes'));
		try {
			expect(getComputedStyle(pair).background).toBe('rgba(0, 0, 0, 0)');
			const lift = pair.querySelector('.pill') as HTMLElement;
			expect(getComputedStyle(lift).background).toBe('var(--sift-surface-4)');
		} finally {
			removeStyles();
		}
	});

	/* The one answering is lifted: ONE segment slides between the two (the File info tabs'
	   movement) and sits under whichever is lit, never the accent, since neither of the two is
	   "off". And the sparkle, lit, carries its moving wave, on the glyph and nowhere else. Read with
	   the row's own stylesheet in place. */
	it('lifts the answering one, and lights the sparkle with its wave', async () => {
		meaning.available = true;
		at.url = new URL('http://localhost/search?q=dog&meaning=1');
		await box();

		const pair = host.querySelector('.modes [role="tablist"]') as HTMLElement;
		const segmented = pair.parentElement as HTMLElement;
		applyStyles(tabsSource, pair);
		try {
			const lift = pair.querySelector('.pill') as HTMLElement;
			expect(getComputedStyle(lift).background).toBe('var(--sift-surface-4)');
			// Meaning is the first of the two: the segment sits at the start.
			expect(segmented.style.getPropertyValue('--at')).toBe('0');

			const glyph = control()!.querySelector('.tab > .icon') as HTMLElement;
			expect(getComputedStyle(glyph).backgroundImage).toBe('var(--sift-wave)');
			const plain = wordsControl()!.querySelector('.tab > .icon') as HTMLElement;
			expect(getComputedStyle(plain).backgroundImage).not.toBe('var(--sift-wave)');
		} finally {
			removeStyles();
		}
	});

	/* A pointer press on a mode leaves the caret in the field: the next keystroke still lands in
	   the box, whichever of the two was pressed. */
	it('keeps the caret in the field when a mode is pressed', async () => {
		meaning.available = true;
		await box();

		const down = new MouseEvent('mousedown', { bubbles: true, cancelable: true });
		wordsControl()!.dispatchEvent(down);
		expect(down.defaultPrevented).toBe(true);
	});

	/* Along the row with the arrows, as every tab row: one tab stop, the one answering. */
	it('is one tab stop, the arrows moving along it', async () => {
		meaning.available = true;
		await box();

		expect(wordsControl()!.tabIndex).toBe(0);
		expect(control()!.tabIndex).toBe(-1);
		wordsControl()!.focus();
		wordsControl()!.dispatchEvent(
			new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true, cancelable: true })
		);
		expect(document.activeElement).toBe(control());
	});

	/* From the field itself: Ctrl + Left moves the switch to the sparkle and Ctrl + Right to the
	   glass, each the way it points, and the press is the box's rather than the caret's. With Shift
	   held it is the browser's own select-a-word, left alone. */
	function pressInField(key: string, held: KeyboardEventInit = {}): KeyboardEvent {
		const field = host.querySelector('input[type="search"]') as HTMLInputElement;
		const event = new KeyboardEvent('keydown', {
			key,
			ctrlKey: true,
			bubbles: true,
			cancelable: true,
			...held
		});
		field.dispatchEvent(event);
		flushSync();
		return event;
	}

	it('moves to the sparkle from the field with Ctrl + Left', async () => {
		meaning.available = true;
		at.url = new URL('http://localhost/search?q=dog');
		await box();
		vi.mocked(goto).mockClear();

		expect(pressInField('ArrowLeft').defaultPrevented).toBe(true);
		expect(asked().searchParams.get('meaning')).toBe('1');
	});

	it('and back to the glass with Ctrl + Right, leaving Ctrl + Shift to select words', async () => {
		meaning.available = true;
		at.url = new URL('http://localhost/search?q=dog&meaning=1');
		await box();
		vi.mocked(goto).mockClear();

		const selecting = pressInField('ArrowLeft', { shiftKey: true });
		expect(selecting.defaultPrevented).toBe(false);
		expect(goto).not.toHaveBeenCalled();

		expect(pressInField('ArrowRight').defaultPrevented).toBe(true);
		expect(asked().searchParams.has('meaning')).toBe(false);
	});

	it('leaves Ctrl and the arrows to the field where there is no switch', async () => {
		await box();
		vi.mocked(goto).mockClear();

		expect(pressInField('ArrowLeft').defaultPrevented).toBe(false);
		expect(goto).not.toHaveBeenCalled();
	});

	it('reads as on when the address already asks for that order', async () => {
		meaning.available = true;
		at.url = new URL('http://localhost/search?q=dog&sort=similarity');
		await box();

		expect(control()?.getAttribute('aria-selected')).toBe('true');
	});

	it('asks to search by meaning in its own parameter, from the first page of it', async () => {
		/*
		 * How to search and how to arrange what is found are two questions, and this control asks
		 * the first. Folded into one (`sort=similarity`), every order in the Sort panel would be
		 * dead on a search by meaning, because the only way to express Newest would be to take the
		 * sort away, which turns the model off.
		 */
		meaning.available = true;
		at.url = new URL('http://localhost/search?q=dog&offset=120');
		await box();

		control()!.click();
		flushSync();

		expect(asked().searchParams.get('meaning')).toBe('1');
		expect(asked().searchParams.has('sort')).toBe(false);
		// Landing on page three of a re-ordered set is not what pressing this meant.
		expect(asked().searchParams.has('offset')).toBe(false);
		expect(asked().searchParams.get('q')).toBe('dog');
	});

	it('takes the old spelling off the address, both switching on and switching off', async () => {
		/* An address that still said `sort=similarity` would go on searching by meaning after the
		 * control said it had stopped, and leaving it while switching ON pins the order to
		 * closest-first, which takes the Sort panel away again. It is still READ, so a link sent
		 * before the two were separated still opens a search by meaning; it is never written. */
		meaning.available = true;
		at.url = new URL('http://localhost/search?q=dog&sort=similarity');
		await box();

		// The address already asks for it by the legacy parameter, so the way out is the words
		// control.
		wordsControl()!.click();
		flushSync();

		expect(asked().searchParams.has('sort')).toBe(false);
		expect(asked().searchParams.has('meaning')).toBe(false);
		expect(asked().searchParams.get('q')).toBe('dog');
	});

	it('keeps the query when the box has not seeded itself yet', async () => {
		/*
		 * The box reads `q` out of the address asynchronously, so on every page load there is a
		 * moment where the address has a query and the box is still empty. Switching how to search
		 * during that moment must not write the empty box over the address and clear the search
		 * somebody is looking at, from a control that says nothing about clearing anything.
		 */
		meaning.available = true;
		at.url = new URL('http://localhost/search?q=dog');
		await box();

		control()!.click();
		flushSync();

		expect(asked().searchParams.get('q')).toBe('dog');
	});

	it('takes the order back off when the words control is pressed', async () => {
		meaning.available = true;
		at.url = new URL('http://localhost/search?q=dog&sort=similarity');
		await box();

		wordsControl()!.click();
		flushSync();

		expect(asked().searchParams.has('sort')).toBe(false);
		expect(asked().searchParams.get('q')).toBe('dog');
	});
});

describe('a joining word between two values picked from the list', () => {
	/*
	 * The chips serialise in front of the text, always, so a value picked off the list while an
	 * `OR` is typed must not land ahead of it: `tags:beach tags:sunset OR` reads as both tags and a
	 * loose word "or", narrower than either half, with nothing on screen saying so.
	 *
	 * Asserted on the query the box sends rather than the chips it draws: the chips are the
	 * server's answer to that query, and the string is what can be wrong.
	 *
	 * A value offered inside a token (`tags:be` half-typed): every row on such a list completes the
	 * token, so none navigates; an entity row leaves the page only when the box is not mid-way
	 * through writing a filter.
	 */
	function offer(box: HTMLElement, value: string, from: number, forQuery: string) {
		return offering(box, {
			token: 'tags',
			matches: [{ field: 'tags', value, detail: null, count: 1 }],
			replace_from: from,
			for_query: forQuery
		});
	}

	/*
	 * The row carrying a value, which is not the first one on the list.
	 *
	 * A list offering entities also offers the typed words as a row of their own, and that row is
	 * first on purpose: it is what the box does with no entity rows, so the row a hand lands on
	 * first behaves the same either way. Reaching for `[role="option"]` therefore picks "search for
	 * these words", which is a different act from completing a filter.
	 */
	function rowFor(box: HTMLElement, value: string): HTMLElement {
		const rows = [...box.querySelectorAll('[role="option"]')] as HTMLElement[];
		const found = rows.find((row) => row.textContent?.includes(value));
		if (!found) throw new Error(`no row offering ${value}; saw ${rows.map((r) => r.textContent)}`);
		return found;
	}

	it('joins the two rather than leaving the word behind them', async () => {
		const box = await ready();

		field().value = 'bea';
		field().dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		await offer(box, 'beach', 0, 'bea');
		pick(rowFor(box, 'beach'));

		field().value = 'OR sun';
		field().dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		// `tags:beach ` is eleven characters, and `OR ` is three more.
		await offer(box, 'sunset', 14, 'tags:beach OR sun');
		pick(rowFor(box, 'sunset'));

		(box.querySelector('form') as HTMLFormElement).dispatchEvent(
			new Event('submit', { bubbles: true, cancelable: true })
		);
		flushSync();

		const url = new URL(String(vi.mocked(goto).mock.calls.at(-1)?.[0]));
		expect(url.searchParams.get('q')).toBe('tags:beach OR tags:sunset');
	});

	/* Two tests, one mount each: a box left mounted keeps its window listener, and an orphan's
	   listener puts the NEXT test's list away (it hears a press that is not inside its own box). */
	it('carries the filters of Browse when Enter is pressed on Browse', async () => {
		at.url = new URL('http://localhost/browse?tags=beach&sort=newest&offset=120');
		const onBrowse = await ready();
		field().value = 'dog';
		field().dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		(onBrowse.querySelector('form') as HTMLFormElement).dispatchEvent(
			new Event('submit', { bubbles: true, cancelable: true })
		);
		flushSync();
		const fromBrowse = new URL(String(vi.mocked(goto).mock.calls.at(-1)?.[0]));
		expect([...fromBrowse.searchParams]).toEqual([
			['tags', 'beach'],
			['sort', 'newest'],
			['q', 'dog']
		]);
	});

	it('carries nothing of a wall when Enter is pressed there', async () => {
		at.url = new URL('http://localhost/people?sort=edited&from=01ABC&near=01DEF');
		const onWall = await ready();
		field().value = 'dog';
		field().dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		(onWall.querySelector('form') as HTMLFormElement).dispatchEvent(
			new Event('submit', { bubbles: true, cancelable: true })
		);
		flushSync();
		const fromWall = new URL(String(vi.mocked(goto).mock.calls.at(-1)?.[0]));
		expect(fromWall.pathname).toBe('/browse');
		expect([...fromWall.searchParams]).toEqual([['q', 'dog']]);
	});

	it('is not what a matching entity does when nothing is half-typed', async () => {
		/*
		 * The two tests above are about completing a FILTER, and they set a token to say so. Outside
		 * one, the same row does something else entirely: it leaves for the thing it names. Picking
		 * the first row on an entity list runs the words, and picking the entity navigates.
		 */
		const box = await ready();

		field().value = 'bea';
		field().dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		await offering(box, {
			/* With its id, because the server sends one: a tag row carries the tag's own id. Picking
			   it is what takes you to that tag's files, and a row with no id is nothing to go to, so a
			   fixture without one would assert a path the server cannot produce. */
			matches: [{ field: 'tags', value: 'beach', detail: null, count: 1, id: 'tag-1' }],
			replace_from: 0,
			for_query: 'bea'
		});

		// The words as typed, first on the list, ahead of anything the library matched.
		expect((box.querySelector('[role="option"]') as HTMLElement).textContent).toContain('bea');

		pick(rowFor(box, 'beach'));

		expect(String(vi.mocked(goto).mock.calls.at(-1)?.[0])).toContain('tags=beach');
		expect(box.querySelector('.chip')).toBeNull();
	});

	it('still puts a value with nothing in front of it at the front', async () => {
		// The ordinary path, so that the rule above cannot quietly become "always write into the
		// text", which would stop a picked value ever becoming a chip on its own.
		const box = await ready();

		field().value = 'bea';
		field().dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		await offer(box, 'beach', 0, 'bea');
		pick(rowFor(box, 'beach'));

		expect(box.querySelector('.chip')?.textContent).toContain('beach');
		expect(field().value).toBe('');
	});
});

describe('a press lands on the row that was drawn', () => {
	/*
	 * The window this closes.
	 *
	 * An answer from the server lands in the store synchronously; the markup catches up in a later
	 * flush. So there is a moment where the list on screen is one list and the store holds another,
	 * and a handler reading the row by its position at the moment of the press would act on the
	 * wrong one: a filter row and an entity row swap places, and pressing what looks like a filter
	 * runs a search or leaves for somebody's page instead.
	 *
	 * Reproduced here deterministically: the store is moved on without a flush, so the button under
	 * the press is the one the previous answer drew. In a browser it needs a slow answer to land at
	 * exactly the wrong instant, which is why it shows only under load.
	 */
	it('even when a newer answer has already reached the store', async () => {
		const box = await ready();
		vi.mocked(goto).mockClear();

		await offering(box, { filters: [PEOPLE_FILTER], replace_from: 0 });
		const drawn = box.querySelector('[role="option"]') as HTMLElement;
		expect(drawn.textContent, 'the filter row is not the one on screen').toContain('people');

		// A newer answer, NOT flushed: the store has moved on and the markup has not.
		const { searchBox } = await import('$lib/search/search.svelte');
		searchBox.suggestions = {
			token: null,
			filters: [],
			matches: [{ field: 'tags', value: 'beach', detail: null, count: 1 }],
			recent: [],
			replace_from: 0,
			for_query: ''
		} as never;

		pick(drawn);

		// The filter was applied, which is what the row said it would do...
		expect(box.querySelector('.chip')?.textContent ?? '').toContain('people:');
		// ...and nothing left for the page of a tag that was never on screen.
		expect(vi.mocked(goto)).not.toHaveBeenCalled();
	});
});

/*
 * What puts the list away: a press that BEGINS outside the box.
 *
 * The phone's Search square opens this box, focused and with its list up, from its own click, and
 * in a browser a listener the box adds during that click still hears the rest of it. Put away on
 * the click, the list opened and shut in one tap. jsdom does not run the page's work between one
 * listener and the next, so that exact order cannot be staged here; the rule it depends on is
 * what is pinned: a click with no press outside behind it leaves the list up.
 */
describe('putting the list away', () => {
	/* Read off the field rather than the list: the list takes its leaving motion out of the page. */
	const up = () => field().getAttribute('aria-expanded') === 'true';

	async function listUp(): Promise<HTMLElement> {
		const box = await ready();
		await offering(box, { filters: [PEOPLE_FILTER], replace_from: 0 });
		expect(up(), 'the list never came up').toBe(true);
		return box;
	}

	function outside(): HTMLElement {
		const door = document.createElement('button');
		document.body.append(door);
		return door;
	}

	it('keeps it up through the click of the tap that opened it', async () => {
		await listUp();
		const door = outside();

		// A pointer's click (`detail` 1) whose press began before the box was up.
		door.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 }));
		flushSync();

		expect(up()).toBe(true);
		door.remove();
	});

	it('puts it away on a press outside, on the way down', async () => {
		await listUp();
		const door = outside();

		door.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
		flushSync();

		expect(up()).toBe(false);
		door.remove();
	});

	it('puts it away when the keyboard presses something outside', async () => {
		await listUp();
		const door = outside();

		// Enter or Space on a button: a click with no pointer behind it.
		door.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 0 }));
		flushSync();

		expect(up()).toBe(false);
		door.remove();
	});

	it('keeps it up for a press inside the box', async () => {
		await listUp();

		field().dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
		flushSync();

		expect(up()).toBe(true);
	});
});

describe('the list coming out from under the bar', () => {
	/* Sort by and the Filter panel come out from under the top bar and go back up under it. The
	   list under the search box hangs from the same bar, so it moves the same way, by the same
	   transition, rather than by a drop of its own. */
	it('opens by the bar motion', async () => {
		vi.mocked(fromBar).mockClear();
		const box = await ready();
		await offering(box, { filters: [PEOPLE_FILTER], replace_from: 0 });
		await vi.waitFor(() => expect(box.querySelector('.popover')).not.toBeNull());
		// The intro itself is not observable here: under jsdom the framework asks the transition for
		// its shape only when it can animate, which a loaded run never reaches. What holds the list
		// to the bar's motion is the directive on the element, read from the component itself.
		const source = await import('$lib/components/shell/SearchSuggestions.svelte?raw');
		expect(source.default).toMatch(/class="popover" transition:fromBar/);
	});
});

describe('a wall with a search box of its own', () => {
	/*
	 * Search people, Search tags and the other walls' boxes keep their words in `q`, and this box
	 * follows `q`, so without the claim typing "nat" into the wall's box writes "nat" here too. The
	 * wall's words narrow that wall and nothing else; this box keeps its own text.
	 */
	function askedToParse(): string[] {
		return vi
			.mocked(api.get)
			.mock.calls.filter(([path]) => path === '/search/parse')
			.map(([, options]) => String((options as { query: { q: string } }).query.q));
	}

	it('reads nothing from the address while the wall draws its own box', async () => {
		vi.mocked(api.get).mockClear();
		at.url = new URL('http://localhost/people?q=nat');
		const release = screenBar.claimOwnBox(Symbol('a wall box'));
		try {
			await ready();
			expect(field().value).toBe('');
			expect(askedToParse()).not.toContain('nat');
		} finally {
			release();
		}
	});

	it('keeps what was typed into it when the wall box writes its words', async () => {
		const release = screenBar.claimOwnBox(Symbol('a wall box'));
		try {
			await ready();
			field().value = 'beach';
			field().dispatchEvent(new Event('input', { bubbles: true }));
			flushSync();
			at.url = new URL('http://localhost/people?q=nat');
			// The address moved under the box: the wall's own box wrote its words.
			release();
			const again = screenBar.claimOwnBox(Symbol('the same wall box'));
			flushSync();
			await Promise.resolve();
			flushSync();
			expect(field().value).toBe('beach');
			again();
		} finally {
			release();
		}
	});

	it('follows the address on a screen with no box of its own', async () => {
		vi.mocked(api.get).mockClear();
		at.url = new URL('http://localhost/browse?q=nat');
		await ready();
		expect(askedToParse()).toContain('nat');
	});
});

describe('the glyph at the head of every row', () => {
	/*
	 * A glyph on its own is a shape: an audio codec, a view status and a remembered search are
	 * shapes nobody is born knowing. Each says its kind's name, as the glyph's own name and in the
	 * shared tooltip on hover, from the one table in `search-kinds`.
	 */
	it('names its kind to a screen reader and on hover', async () => {
		const box = await ready();
		await offering(box, {
			matches: [{ value: 'Ada Lumen', field: 'people', count: 3 }],
			recent: [{ kind: 'query', subject: 'beach', label: 'beach' }]
		});
		const glyphs = [...box.querySelectorAll('[role="option"] [role="img"]')].map((one) =>
			one.getAttribute('aria-label')
		);
		expect(glyphs).toEqual(['Person', 'Recent search']);
		const glyph = box.querySelector('[role="option"] [role="img"]') as HTMLElement;
		glyph.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, pointerType: 'mouse' }));
		await new Promise((done) => setTimeout(done, 400));
		flushSync();
		expect(document.querySelector('[role="tooltip"]')?.textContent?.trim()).toBe('Person');
	});
});

describe('a recent search', () => {
	/* Clear takes the whole list; each row can also be taken off on its own, and the server is asked
	   by the subject it kept the row under, which is what it matches. */
	it('can be taken off the list on its own, leaving the others', async () => {
		const box = await ready();
		await offering(box, {
			recent: [
				{ kind: 'query', subject: 'beach', label: 'beach' },
				{ kind: 'query', subject: 'harbour', label: 'harbour' }
			]
		});
		const press = box.querySelector(
			'[aria-label="Remove beach from recent searches"]'
		) as HTMLElement;
		expect(press, 'no Remove on the row').not.toBeNull();
		pick(press);
		await Promise.resolve();
		flushSync();

		expect(vi.mocked(api.del)).toHaveBeenLastCalledWith('/search/history', {
			query: { q: 'beach' }
		});
		const left = [...box.querySelectorAll('[role="option"]')].map((one) => one.textContent);
		expect(left.some((words) => words?.includes('beach'))).toBe(false);
		expect(left.some((words) => words?.includes('harbour'))).toBe(true);
	});
});

describe("the Recent heading's Clear", () => {
	/* A word that acts on a heading, drawn as the design system draws one (`quiet`): no underline
	   at rest. `link` is a run inside a sentence and is underlined always. */
	it('is a quiet press, not a link in a sentence', async () => {
		const box = await ready();
		await offering(box, { recent: [{ kind: 'query', subject: 'beach', label: 'beach' }] });
		const clear = [...box.querySelectorAll<HTMLButtonElement>('.head button')].find(
			(one) => one.textContent?.trim() === 'Clear'
		);

		expect(clear, 'no Clear beside Recent').toBeDefined();
		expect(clear!.classList.contains('quiet')).toBe(true);
		expect(clear!.classList.contains('link')).toBe(false);
	});
});

/*
 * THE KEYBOARD HINT IS PART OF THE BOX. A press on it does what a press anywhere in the box does:
 * the field takes the keyboard and its list comes up. It is not a control of its own, so a screen
 * reader and the Tab key meet the field and never a second button beside it.
 */
describe('the keyboard hint', () => {
	it('is no control of its own', async () => {
		await ready();
		const hint = host.querySelector('.search .shortcut') as HTMLElement;
		expect(hint, 'no hint drawn').not.toBeNull();
		expect(hint.tagName).not.toBe('BUTTON');
		expect(hint.getAttribute('aria-hidden')).toBe('true');
		expect(hint.hasAttribute('tabindex')).toBe(false);
	});

	it('puts the field to work with its list up when pressed, as the box itself does', async () => {
		await ready();
		const hint = host.querySelector('.search .shortcut') as HTMLElement;
		/* The hint goes the moment the field has the keyboard, before the press reaches the window:
		   a browser runs the redraw between the two, so it is run here the same way, on the way up
		   past the document, after the box's own handler and before the window's. */
		const redraw = () => flushSync();
		document.addEventListener('pointerdown', redraw);
		const down = new PointerEvent('pointerdown', { bubbles: true, cancelable: true, button: 0 });
		hint.dispatchEvent(down);
		document.removeEventListener('pointerdown', redraw);
		flushSync();

		expect(down.defaultPrevented, 'the press would blur the field it focuses').toBe(true);
		expect(document.activeElement).toBe(field());
		expect(host.querySelector('.search .shortcut'), 'the hint stayed over a box in use').toBeNull();
		expect(searchBox.open, 'the press put the list away again on its way past').toBe(true);
	});
});

describe('the list as wide as its rows', () => {
	it('is sized by its rows under the bar', async () => {
		const box = await ready();
		await offering(box, { filters: [PEOPLE_FILTER], replace_from: 0 });
		await vi.waitFor(() => expect(box.querySelector('.popover.wide')).not.toBeNull());
	});

	it('moves back inside the window by what it would overhang, never past the start', () => {
		expect(shiftInto(448, 600, 224, 1008)).toBe(-40);
		expect(shiftInto(448, 200, 224, 1008)).toBe(0);
		expect(shiftInto(100, 2000, 224, 1008)).toBe(124);
		expect(shiftInto(200, 200, 224, 1008)).toBe(24);
	});

	it('keeps the key hint out until it fits beside the typing room the floor leaves', () => {
		const shown = Number(/@container search-field \(max-width: (\d+)px\)/.exec(source)?.[1]);
		expect(shown).toBeGreaterThanOrEqual(FIELD_FLOOR - 18 + 59);
	});
});
