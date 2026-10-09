/* What arms the drop-anywhere overlay, and what must not: it covers the whole window. */

import { afterEach, describe, expect, it, vi } from 'vitest';

import { forgetDragOrigin } from '$lib/components/common/drag-origin.svelte';
import { flushSync, mount, unmount } from 'svelte';
import DropOverlay from './DropOverlay.svelte';
import { session } from '$lib/shell/session.svelte';
import { ASSIGN_TYPE } from '$lib/components/common/drag-assign.svelte';

vi.mock('$lib/shell/session.svelte', () => ({ session: { isAdmin: true } }));

// The real one queues a download.
const handleDrop = vi.fn();
vi.mock('$lib/capture/capture.svelte', () => ({
	capture: { handleDrop: (data: unknown) => handleDrop(data) }
}));

let host: HTMLElement;
// Unmounted, not removed: its listeners are on the window and would pile up across tests.
let mounted: Record<string, unknown> | null = null;

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
	handleDrop.mockReset();
	// Whether a drag began here is `drag-origin`'s; a lone `dragstart` would latch it.
	forgetDragOrigin();
});

function render() {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(DropOverlay, { target: host });
	// Flushed, or the `onMount` listeners are not attached and every drag lands on nobody.
	flushSync();
	return host;
}

/** A drag entering the window; jsdom has no `DataTransfer`, and only `types` is read. */
function dragIn(types: string[], from: EventTarget = window) {
	const event = new Event('dragenter', { bubbles: true });
	Object.defineProperty(event, 'dataTransfer', { value: { types } });
	from.dispatchEvent(event);
	// Flushed so the markup reflects the drag.
	flushSync();
}

/** The other half of the crossing, with no types: a real `dragleave` is not asked. */
function leave(from: EventTarget = window) {
	from.dispatchEvent(new Event('dragleave', { bubbles: true }));
	flushSync();
}

function showing(): boolean {
	return host.querySelector('.overlay') !== null;
}

describe('what the window offers to take', () => {
	it('arms for files', () => {
		render();
		dragIn(['Files']);
		expect(showing()).toBe(true);
	});

	it('arms for a link dragged in from another tab', () => {
		// Without this a dropped link falls through to the browser, which navigates away to it.
		render();
		dragIn(['text/uri-list', 'text/plain']);
		expect(showing()).toBe(true);
	});

	it('ignores a drag of the app own, whatever else it carries', () => {
		// A drag begun on a tile's <img> carries the browser's own link types too.
		render();
		dragIn([ASSIGN_TYPE, 'text/uri-list', 'text/plain']);
		expect(showing()).toBe(false);
	});

	it('ignores a drag carrying nothing it can use', () => {
		render();
		dragIn(['application/x-something-else']);
		expect(showing()).toBe(false);
	});
});

describe('a drag that started in here, and one that only looked like it did', () => {
	// A prevented default is what a native drag out of the window does.
	function dragStarted(prevented: boolean) {
		const event = new Event('dragstart', { bubbles: true, cancelable: true });
		if (prevented) event.preventDefault();
		window.dispatchEvent(event);
		flushSync();
	}

	it('ignores a drag it started itself, even when the browser fills it with a link', () => {
		render();
		dragStarted(false);
		dragIn(['text/uri-list', 'text/plain']);
		expect(showing()).toBe(false);
	});

	// A prevented `dragstart` never ends in a `dragend`, and must not leave the window deaf.
	it('is not deafened by a drag that was cancelled before it began', () => {
		render();
		dragStarted(true);
		dragIn(['Files']);
		expect(showing()).toBe(true);
	});
});

describe('a box on the page that takes its own drops', () => {
	// A box taking its own drops (the tunnel importer) is counted, never taken.
	function zone(takes = ''): HTMLElement {
		const box = document.createElement('div');
		box.setAttribute('data-drop-zone', takes);
		document.body.append(box);
		return box;
	}

	it('does not arm the window overlay', () => {
		render();
		const box = zone();
		dragIn(['Files'], box);
		expect(showing()).toBe(false);
		box.remove();
	});

	it('takes nothing from a drop that landed inside it', () => {
		render();
		const box = zone();
		// Armed first from elsewhere, then let go inside the box.
		dragIn(['Files']);
		expect(showing()).toBe(true);

		const dropped = new Event('drop', { bubbles: true });
		Object.defineProperty(dropped, 'dataTransfer', { value: { types: ['Files'], files: [] } });
		box.dispatchEvent(dropped);
		flushSync();

		expect(handleDrop).not.toHaveBeenCalled();

		// The counter is still cleared: the next drop on the window is taken.
		box.remove();
		dragIn(['Files']);
		const onWindow = new Event('drop', { bubbles: true });
		Object.defineProperty(onWindow, 'dataTransfer', { value: { types: ['Files'], files: [] } });
		window.dispatchEvent(onWindow);
		flushSync();
		expect(handleDrop).toHaveBeenCalledTimes(1);
	});

	// A card takes a link and refuses a file, which falls through to the window.
	it('stands the window down for the drag it says it takes', () => {
		render();
		const box = zone('link');
		dragIn(['text/uri-list'], box);
		expect(showing()).toBe(false);
		box.remove();
	});

	it('and leaves the window armed for one it does not', () => {
		render();
		const box = zone('link');
		// A file over a card that takes only links: the window imports it, so the offer stays.
		dragIn(['Files'], box);
		expect(showing()).toBe(true);
		box.remove();
	});

	// Enters are counted unconditionally (they fire before leaves). Asserted by a drop, which reads
	// the counter, since the fading markup cannot tell armed from leaving.
	it('comes back and STAYS when the pointer leaves a card', () => {
		render();
		const card = zone('link');

		dragIn(['text/uri-list']);
		expect(showing()).toBe(true);

		// onto the card: the new target is entered, THEN the old one is left
		dragIn(['text/uri-list'], card);
		leave(window);

		// and back off it, in the same order
		dragIn(['text/uri-list']);
		leave(card);

		// crossing three more elements must not wear it down either
		for (const _ of [1, 2, 3]) {
			dragIn(['text/uri-list']);
			leave(window);
		}

		const dropped = new Event('drop', { bubbles: true });
		Object.defineProperty(dropped, 'dataTransfer', { value: { types: ['text/uri-list'] } });
		window.dispatchEvent(dropped);
		flushSync();
		expect(handleDrop).toHaveBeenCalledTimes(1);
		card.remove();
	});

	// A browser drag carries a link and a file; the link zone takes it, so the window must not.
	it('stands down over a link zone for a drag carrying a link AND a file', () => {
		render();
		const card = zone('link');
		const both = ['Files', 'text/uri-list', 'text/plain'];

		dragIn(both, card);
		expect(showing()).toBe(false);

		const dropped = new Event('drop', { bubbles: true });
		Object.defineProperty(dropped, 'dataTransfer', { value: { types: both, files: [] } });
		card.dispatchEvent(dropped);
		flushSync();
		expect(handleDrop).not.toHaveBeenCalled();
		card.remove();
	});

	it('a zone that names nothing still takes everything', () => {
		// The tunnel importer: an unvalued attribute must keep meaning "all of it".
		render();
		const box = zone();
		dragIn(['text/uri-list'], box);
		expect(showing()).toBe(false);
		box.remove();
	});
});

describe('a guest dragging a link in', () => {
	it("is told from the first frame that adding is an admin's, never offered the drop", () => {
		/* The double is a plain object; the real session derives this from the sign-in. */
		const mocked = session as { isAdmin: boolean };
		mocked.isAdmin = false;
		try {
			render();
			dragIn(['text/uri-list']);
			expect(host.textContent).toContain('Adding media is available to admins');
			expect(host.textContent).not.toContain('Drop to add');
		} finally {
			mocked.isAdmin = true;
		}
	});
});
