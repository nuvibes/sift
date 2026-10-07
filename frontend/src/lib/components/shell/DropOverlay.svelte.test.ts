/* What arms the drop-anywhere overlay, and, the part easy to get wrong, what must not.
 *
 * The overlay covers the whole window while something is being dragged over it, so arming it on the
 * wrong drag puts a full-screen "Drop to add" in front of somebody in the middle of a gesture that
 * has nothing to do with importing.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import { forgetDragOrigin } from '$lib/components/common/drag-origin.svelte';
import { flushSync, mount, unmount } from 'svelte';
import DropOverlay from './DropOverlay.svelte';
import { session } from '$lib/shell/session.svelte';
import { ASSIGN_TYPE } from '$lib/components/common/drag-assign.svelte';

vi.mock('$lib/shell/session.svelte', () => ({ session: { isAdmin: true } }));

/* What a taken drop calls. The test double is what makes "did the window take this" answerable at
 * all: the real one queues a download. */
const handleDrop = vi.fn();
vi.mock('$lib/capture/capture.svelte', () => ({
	capture: { handleDrop: (data: unknown) => handleDrop(data) }
}));

let host: HTMLElement;
/* Unmounted, not just removed from the page.
 *
 * This component's listeners are on the WINDOW, and removing its host element leaves every one of
 * them attached. Six tests each leaving one behind would handle a single drop six times, which
 * looks exactly like the component taking a drop it should have ignored.
 */
let mounted: Record<string, unknown> | null = null;

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
	handleDrop.mockReset();
	/* Whether a drag began in here is the APPLICATION's state, not this component's: an entity
	   card needs the identical answer, so it lives in `drag-origin`. In a browser it is cleared by
	   the `dragend` or `drop` that ends every real drag; a test that dispatches a `dragstart` and
	   nothing after it leaves it latched, and the next test then reads the last one's drag. */
	forgetDragOrigin();
});

function render() {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(DropOverlay, { target: host });
	/* Flushed, or this component is mounted and DEAF.
	 *
	 * Its window listeners are attached from `onMount`, which Svelte runs inside an effect, so
	 * until the effects run there is nothing listening, and every `dragenter` dispatched before this
	 * line lands on nobody. It reads as the component refusing a drag it should have taken, which is
	 * indistinguishable from the bug these tests exist to catch. They are in the CAPTURE phase, not
	 * on `<svelte:window>`, because a target that takes a drop for itself stops the bubble and the
	 * counter would never be cleared. See the component. */
	flushSync();
	return host;
}

/* A drag carrying these types, entering the window.
 *
 * `DataTransfer` is not implemented in jsdom, so the event carries a stand-in with the one field
 * the component reads. What is under test is which type lists arm the overlay, and that decision is
 * made from `types` alone: the real object cannot be read during a drag either.
 */
function dragIn(types: string[], from: EventTarget = window) {
	const event = new Event('dragenter', { bubbles: true });
	Object.defineProperty(event, 'dataTransfer', { value: { types } });
	from.dispatchEvent(event);
	// The overlay appears as a result of state changing, and the DOM is not updated until the
	// effects run. Without this every assertion reads the markup as it was before the drag.
	flushSync();
}

/** The other half of the crossing. Deliberately carries no type list: a real `dragleave` is not
 *  asked what it is carrying, and counting it only when it looks takeable is the whole bug. */
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
		/* A tile is dragged to put it on a tag, and the drag begins on the <img>
		 * inside the tile, so the browser adds `text/uri-list` and `text/plain` of its own accord,
		 * pointing at the thumbnail. Those are exactly the types above, so dragging a clip onto a tag
		 * could raise a full-window offer to download the picture already on the screen. */
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
	/* Dispatch a `dragstart` on the window, optionally one whose default was already prevented by
	 * the element that was grabbed, which is what a native drag out of the window does. */
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

	/*
	 * The case that would leave the window permanently deaf.
	 *
	 * `ours` is cleared by `dragend`, which fires on the source wherever a drag finishes. A
	 * `dragstart` whose default was prevented never becomes a drag, so no `dragend` comes, and
	 * taking a picture out to another application does exactly that, because the gesture is handed
	 * to the operating system.
	 *
	 * So `ours` must not stay stuck after one attempt to drag a video into a chat window, or every
	 * later drop into the application would be refused with no overlay, no import and no error.
	 */
	it('is not deafened by a drag that was cancelled before it began', () => {
		render();
		dragStarted(true);
		dragIn(['Files']);
		expect(showing()).toBe(true);
	});
});

describe('a box on the page that takes its own drops', () => {
	/* A WireGuard configuration dropped on the tunnel importer must not bubble up to the window
	 * and be imported as media, which would start a download of a config file. The window still
	 * counts the drag; it simply does not take it.
	 */
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
		// Armed first from somewhere else, which is the case that matters: the overlay is up, the
		// pointer moves into the box, and the file is let go there.
		dragIn(['Files']);
		expect(showing()).toBe(true);

		const dropped = new Event('drop', { bubbles: true });
		Object.defineProperty(dropped, 'dataTransfer', { value: { types: ['Files'], files: [] } });
		box.dispatchEvent(dropped);
		flushSync();

		expect(handleDrop).not.toHaveBeenCalled();

		// And the counter was still cleared. That is the one thing that has to happen either way:
		// returning early before it would leave a full-screen overlay over the application with no
		// drag in progress. Proved by the NEXT drop on the window working, which it cannot do while
		// a stale count says a drag from before is still in flight.
		box.remove();
		dragIn(['Files']);
		const onWindow = new Event('drop', { bubbles: true });
		Object.defineProperty(onWindow, 'dataTransfer', { value: { types: ['Files'], files: [] } });
		window.dispatchEvent(onWindow);
		flushSync();
		expect(handleDrop).toHaveBeenCalledTimes(1);
	});

	/*
	 * A zone that names what it takes.
	 *
	 * A card on an entity wall takes a LINK (fetch it and file it under that person) and
	 * deliberately refuses a FILE, which falls through and is imported the ordinary way. Asking only
	 * whether a zone was under the pointer could not tell those apart, so the choice was between a
	 * full-window overlay over the card somebody was aiming at, and a card that swallowed a dropped
	 * file and did nothing with it.
	 */
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
		// A FILE over a card that takes only links. The window is what imports this, so the offer
		// has to stay up: without it the drop lands on a card that refuses it and nothing happens.
		dragIn(['Files'], box);
		expect(showing()).toBe(true);
		box.remove();
	});

	/*
	 * The flash: dragging onto a card and then off must leave the overlay up.
	 *
	 * The browser fires `dragenter` on the element being entered before `dragleave` on the one
	 * left, so the enters are counted unconditionally; counting only the ones this window would
	 * take would net +1 then -1 to zero when crossing from a card back onto the page. It is
	 * arithmetic, not timing.
	 *
	 * Asserted with the drop rather than the markup: the offer fades out, so the element stays in
	 * the page for the transition after the state says it is gone, and the markup cannot tell armed
	 * from leaving. A drop is synchronous and reads the counter directly, taken only while the
	 * window is genuinely armed. The same oracle, and reason, as the stale-counter test above.
	 */
	it('comes back and STAYS when the pointer leaves a card', () => {
		render();
		const card = zone('link');

		dragIn(['text/uri-list']);
		expect(showing()).toBe(true);

		// onto the card: the new target is entered, THEN the old one is left
		dragIn(['text/uri-list'], card);
		leave(window);

		// and back off it, in the same order. Counting only accepted enters would put the counter
		// at zero here.
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

	/*
	 * A drag carrying both, which is what a browser actually hands over: dragging an image or a
	 * link out of a browser fills the payload with `text/uri-list` and
	 * `Files` at the same time. The card asks `carriesALink` and lights up; this overlay must stand
	 * down for it, or one gesture would draw two offers and a drop would start two downloads, one
	 * filed under the card and one filed nowhere.
	 *
	 * Both halves are asserted: the offer must not arm, and the drop must not be taken.
	 */
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
		// The tunnel importer is such a zone. An unvalued attribute
		// must keep meaning "all of it" or that box starts letting files through to the window.
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
