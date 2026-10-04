/*
 * The search bar, bigger, over a blurred page.
 *
 * Two rules decide everything this component does. Ctrl-F opens it, unless somebody is typing: a
 * keystroke belonging to a field is the field's. And it shuts when a search takes you somewhere,
 * and not when the same search is only re-ordered.
 *
 * The second is the interesting half: the search-by-meaning control keeps its state in the address,
 * so pressing it navigates, and a sheet that shut on every navigation would close and light the
 * control in the top bar instead.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

/* `afterNavigate` is a lifecycle hook rather than something a test can trigger, so the callbacks
   the components register are captured and called with the two addresses a navigation carries.

   EVERY callback, in a list. The sheet draws a search box that registers its own, so a double
   holding one would let the second registration replace the first, a navigation would reach only
   the box, and the tests that say the sheet closes would fail with nothing wrong in the sheet. A
   double that keeps one of something the real thing keeps many of stops describing it. */
const navigation = vi.hoisted(() => ({
	arrived: [] as ((detail: { from: unknown; to: unknown }) => void)[]
}));

vi.mock('$app/navigation', () => ({
	afterNavigate: (callback: (detail: { from: unknown; to: unknown }) => void) => {
		navigation.arrived.push(callback);
	},
	goto: vi.fn()
}));
vi.mock('$app/state', () => ({
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false },
	page: { url: new URL('http://localhost/browse') }
}));
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
/* The real module with one answer replaced. See SearchBox.svelte.test.ts for why. */
vi.mock('$lib/search/semantic.svelte', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/search/semantic.svelte')>()),
	semanticAvailable: async () => ({ available: false })
}));
/* Instant, so "it closed" is a question about the component rather than about how long a fade
   takes. A sheet on its way out is still on the page for the length of its transition, and this is
   the one place in the file where that would be mistaken for a sheet that stayed open. */
vi.mock('$lib/shell/motion.svelte', () => ({
	arrive: () => ({ duration: 0, css: () => '' }),
	veil: () => ({ duration: 0, css: () => '' }),
	motion: { reduced: true, duration: (ms: number) => ms }
}));

import SearchOverlay from './SearchOverlay.svelte';

let host: HTMLElement;
let standing: ReturnType<typeof mount> | null = null;

beforeEach(() => {
	navigation.arrived = [];
});

/* Unmounted rather than merely detached, and that is not tidiness.
 *
 * This component listens on the WINDOW, which outlives the element it was rendered into, so a
 * previous test's instance goes on answering Ctrl-F and Escape for the rest of the file. Taking the
 * host off the page hides its markup and leaves the listener, so a test would pass on its own and
 * fail in a full run.
 */
afterEach(() => {
	if (standing) unmount(standing);
	standing = null;
	host?.remove();
	document.body.innerHTML = '';
});

function render() {
	host = document.createElement('div');
	document.body.append(host);
	standing = mount(SearchOverlay, { target: host });
	flushSync();
}

const sheet = () => document.querySelector('[role="dialog"][aria-label="Search"]');

/** Press a key on the window, from whatever was focused. */
function press(key: string, options: KeyboardEventInit = {}) {
	const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...options });
	(options.view ? document.body : window).dispatchEvent(event);
	flushSync();
	return event;
}

/** A navigation from one address to another, as the router would report it. */
function navigate(from: string, to: string) {
	for (const arrived of navigation.arrived)
		arrived({ from: { url: new URL(from) }, to: { url: new URL(to) } });
	flushSync();
}

async function opened() {
	render();
	press('f', { ctrlKey: true });
	// The sheet is put on the page, then the caret is moved into it a tick later.
	await Promise.resolve();
	flushSync();
}

describe('opening it', () => {
	it('is not there until it is asked for', () => {
		render();

		expect(sheet()).toBeNull();
	});

	it('opens on Ctrl-F', async () => {
		await opened();

		expect(sheet()).not.toBeNull();
	});

	it('takes the keystroke off the browser, which is the whole trade', () => {
		/* On a page that is a grid of pictures, finding a word on it is rarely what was meant. The
		   worst outcome of NOT catching it is that the application's own search has no keyboard
		   route at all. */
		render();

		const event = press('f', { ctrlKey: true });

		expect(event.defaultPrevented).toBe(true);
	});

	it('leaves Ctrl-Shift-F and plain F alone', () => {
		render();

		press('f');
		press('f', { ctrlKey: true, shiftKey: true });

		expect(sheet()).toBeNull();
	});

	it('does not fire while somebody is typing into a field', () => {
		/* The search field already has its own Ctrl-A, and a person typing into a box expects the
		   box's shortcuts rather than the application's. */
		render();
		const field = document.createElement('input');
		document.body.append(field);

		field.dispatchEvent(
			new KeyboardEvent('keydown', { key: 'f', ctrlKey: true, bubbles: true, cancelable: true })
		);
		flushSync();

		expect(sheet()).toBeNull();
	});
});

describe('closing it', () => {
	/** Gone, once whatever was animating it has finished. */
	async function closed() {
		await vi.waitFor(() => expect(sheet()).toBeNull());
	}

	/** Still here, and stays here: asserted after the same wait, so it is not merely early. */
	async function stillOpen() {
		await new Promise((settle) => setTimeout(settle, 20));
		flushSync();
		expect(sheet()).not.toBeNull();
	}

	it('one press of Escape is enough', async () => {
		/* Taken on the way DOWN. The box inside keeps Escape for itself while its suggestion list is
		   open, which in here it always is, because the sheet opens with the caret in the field,
		   so heard after the field the first press would only shut the list. */
		await opened();

		press('Escape');

		await closed();
	});

	it('shuts when a search takes you to another screen', async () => {
		await opened();

		navigate('http://localhost/browse', 'http://localhost/search?q=dog');

		await closed();
	});

	it('shuts when the words change on the same screen', async () => {
		await opened();

		navigate('http://localhost/search?q=dog', 'http://localhost/search?q=cat');

		await closed();
	});

	it('STAYS OPEN when only the ordering changed', async () => {
		/* The same screen and the same words in a different order is somebody still standing here
		   deciding how to ask. This is the one the search-by-meaning control does. */
		await opened();

		navigate('http://localhost/search?q=dog', 'http://localhost/search?q=dog&sort=similarity');

		await stillOpen();
	});

	it('stays open when the ordering is taken back off again', async () => {
		await opened();

		navigate('http://localhost/search?q=dog&sort=similarity', 'http://localhost/search?q=dog');

		await stillOpen();
	});

	it('shuts when the screen changes even though the words did not', async () => {
		/* The test is what CHANGED rather than that anything did, and the screen is half of it. */
		await opened();

		navigate('http://localhost/search?q=dog', 'http://localhost/browse?q=dog');

		await closed();
	});

	it('shuts on a navigation the router cannot name a starting point for', async () => {
		/* A first load, or a reload. Nothing to compare, so it cannot be a re-ordering. */
		await opened();

		for (const arrived of navigation.arrived)
			arrived({ from: null, to: { url: new URL('http://localhost/search?q=dog') } });
		flushSync();

		await closed();
	});
});
