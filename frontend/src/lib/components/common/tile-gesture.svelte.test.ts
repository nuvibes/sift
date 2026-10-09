/* Press and hold to select, proved: each case is a way the gesture fails unreported. A hold ends
 * in a click, which must not also open the tile it picked. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { Selection } from './selection.svelte';
import { PRESS_HOLD_MS, TileGesture } from './tile-gesture.svelte';

const ORDER = ['a', 'b', 'c', 'd'];

/** A pointer event jsdom constructs (a MouseEvent); the cue tests dispatch through an element. */
function pointer(type: string, init: Partial<PointerEvent> = {}): MouseEvent {
	return new MouseEvent(type, {
		bubbles: true,
		cancelable: true,
		button: init.button ?? 0,
		clientX: init.clientX ?? 100,
		clientY: init.clientY ?? 100
	});
}

let selection: Selection;
let gesture: TileGesture;
let tile: HTMLElement;

beforeEach(() => {
	vi.useFakeTimers();
	selection = new Selection();
	gesture = new TileGesture(selection, () => ORDER);
	tile = document.createElement('button');
	document.body.append(tile);
	tile.addEventListener('pointerdown', (event) => gesture.pressStart('b', event as PointerEvent));
	tile.addEventListener('pointerup', () => gesture.pressEnd());
});

afterEach(() => {
	gesture.pressEnd();
	/* And let go: only a pointerup removes the sweep's window listeners, or they leak onward. */
	window.dispatchEvent(pointer('pointerup'));
	tile.remove();
	vi.useRealTimers();
	vi.restoreAllMocks();
});

describe('holding', () => {
	it('picks the thing held, once the threshold has passed', () => {
		tile.dispatchEvent(pointer('pointerdown'));
		vi.advanceTimersByTime(PRESS_HOLD_MS);

		expect(selection.count).toBe(1);
		expect(selection.has('b')).toBe(true);
	});

	it('says it is counting, so a press that does nothing yet is not a stopped screen', () => {
		tile.dispatchEvent(pointer('pointerdown'));
		expect(tile.hasAttribute('data-holding')).toBe(true);

		vi.advanceTimersByTime(PRESS_HOLD_MS);
		expect(tile.hasAttribute('data-holding')).toBe(false);
	});

	it('takes the cue off again when the press is let go early', () => {
		tile.dispatchEvent(pointer('pointerdown'));
		tile.dispatchEvent(pointer('pointerup'));

		expect(tile.hasAttribute('data-holding')).toBe(false);
	});
});

describe('not holding', () => {
	it('does nothing on an ordinary click', () => {
		tile.dispatchEvent(pointer('pointerdown'));
		vi.advanceTimersByTime(PRESS_HOLD_MS - 50);
		tile.dispatchEvent(pointer('pointerup'));
		vi.advanceTimersByTime(PRESS_HOLD_MS);

		expect(selection.count).toBe(0);
	});

	it('gives up when the pointer wanders off, which is a drag', () => {
		// A drag that also selected would pick things up by accident.
		tile.dispatchEvent(pointer('pointerdown', { clientX: 100, clientY: 100 }));
		window.dispatchEvent(pointer('pointermove', { clientX: 140, clientY: 100 }));
		vi.advanceTimersByTime(PRESS_HOLD_MS);

		expect(selection.count).toBe(0);
	});

	it('forgives a hand that is not perfectly still', () => {
		// The slop radius, for hands that are not steady.
		tile.dispatchEvent(pointer('pointerdown', { clientX: 100, clientY: 100 }));
		window.dispatchEvent(pointer('pointermove', { clientX: 103, clientY: 102 }));
		vi.advanceTimersByTime(PRESS_HOLD_MS);

		expect(selection.count).toBe(1);
	});

	it('gives up when the page scrolls under it', () => {
		tile.dispatchEvent(pointer('pointerdown'));
		window.dispatchEvent(new Event('scroll'));
		vi.advanceTimersByTime(PRESS_HOLD_MS);

		expect(selection.count).toBe(0);
	});

	it('ignores the right button, which belongs to the menu', () => {
		tile.dispatchEvent(pointer('pointerdown', { button: 2 }));
		vi.advanceTimersByTime(PRESS_HOLD_MS);

		expect(selection.count).toBe(0);
	});
});

describe('the click that follows a hold', () => {
	it('is swallowed, so holding a tile does not also open it', () => {
		tile.dispatchEvent(pointer('pointerdown'));
		vi.advanceTimersByTime(PRESS_HOLD_MS);

		const click = pointer('click');
		gesture.clicked('b', click);

		expect(click.defaultPrevented).toBe(true);
		expect(selection.has('b')).toBe(true);
	});

	it('lets the next click through, so the tile can be let go of again', () => {
		tile.dispatchEvent(pointer('pointerdown'));
		vi.advanceTimersByTime(PRESS_HOLD_MS);
		gesture.clicked('b', pointer('click'));

		// Now it toggles, as a click on a selection, not the hold's tail.
		gesture.clicked('b', pointer('click'));
		expect(selection.count).toBe(0);
	});
});

describe('Escape lets go of the selection', () => {
	it('lets go when nothing else is open', () => {
		selection.toggle('a');

		expect(gesture.escaped()).toBe(true);
		expect(selection.count).toBe(0);
	});

	it('says it did nothing when there was nothing to let go of', () => {
		// Escape belongs to everything else while nothing is picked.
		expect(gesture.escaped()).toBe(false);
	});

	it('leaves the selection alone when something nearer already answered the key', () => {
		/* Escape closes the innermost thing: a portalled menu has already closed, so `defaultPrevented`
		 * says the window must leave the selection alone. */
		selection.toggle('a');
		const answered = new KeyboardEvent('keydown', { key: 'Escape', cancelable: true });
		answered.preventDefault();

		expect(gesture.escaped(answered)).toBe(false);
		expect(selection.count).toBe(1);

		const untouched = new KeyboardEvent('keydown', { key: 'Escape', cancelable: true });
		expect(gesture.escaped(untouched)).toBe(true);
		expect(selection.count).toBe(0);
	});
});

describe('tearing down', () => {
	it('takes its window listeners with it', () => {
		// A leaked pair would hold the tile for the rest of the session.
		const remove = vi.spyOn(window, 'removeEventListener');
		tile.dispatchEvent(pointer('pointerdown'));
		gesture.pressEnd();

		expect(remove).toHaveBeenCalledWith('pointermove', expect.any(Function));
		expect(remove).toHaveBeenCalledWith('scroll', expect.any(Function), { capture: true });
	});

	it('removes the same function it added, or nothing ever comes off', () => {
		// A method passed straight to addEventListener can never be removed.
		const added: unknown[] = [];
		vi.spyOn(window, 'addEventListener').mockImplementation(((type: string, fn: unknown) => {
			if (type === 'pointermove') added.push(fn);
		}) as never);

		tile.dispatchEvent(pointer('pointerdown'));
		gesture.pressEnd();
		tile.dispatchEvent(pointer('pointerdown'));

		expect(added).toHaveLength(2);
		expect(added[0]).toBe(added[1]);
	});
});

/* The sweep, with the layout stubbed: this proves the rule, a real mouse is a hand check. */
describe('sweeping across tiles after a hold', () => {
	/** Stand in for the layout: whatever id the next move should land on. */
	function under(id: string | null) {
		const row = id === null ? null : Object.assign(document.createElement('div'), {});
		if (row && id) row.setAttribute('data-tile-id', id);
		document.elementFromPoint = () => row;
	}

	function hold() {
		tile.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, cancelable: true }));
		vi.advanceTimersByTime(PRESS_HOLD_MS);
	}

	function move(x: number) {
		window.dispatchEvent(pointer('pointermove', { clientX: x, clientY: 100 }));
	}

	it('picks the run from where the press began to wherever the pointer is', () => {
		hold();
		expect([...selection.ordered(ORDER)]).toEqual(['b']);

		under('d');
		move(300);
		// A RUN, not the two ends: 'c' was crossed and belongs to it.
		expect([...selection.ordered(ORDER)]).toEqual(['b', 'c', 'd']);
	});

	/* The native drag refused while sweeping; jsdom cannot start one, so the window is proved. */
	it('refuses the native drag a sweep looks like, while one is running', () => {
		const dragged = () => {
			const event = new Event('dragstart', { bubbles: true, cancelable: true });
			tile.dispatchEvent(event);
			return event.defaultPrevented;
		};

		// Before anything is held, a drag is a drag and nothing interferes with it.
		expect(dragged()).toBe(false);

		hold();
		under('d');
		move(300);
		expect(dragged()).toBe(true);

		// Given back on pointerup, so a wall can drag again.
		window.dispatchEvent(pointer('pointerup', { clientX: 300, clientY: 100 }));
		expect(dragged()).toBe(false);
	});

	it("does not reach the tile's own handler while a sweep is running", () => {
		/* Capture phase and stopPropagation, so no drag payload is built. */
		const built = vi.fn();
		tile.addEventListener('dragstart', built);

		hold();
		under('d');
		move(300);
		tile.dispatchEvent(new Event('dragstart', { bubbles: true, cancelable: true }));

		expect(built).not.toHaveBeenCalled();
	});

	it('gives back what was overshot when the pointer comes home again', () => {
		hold();
		under('d');
		move(300);
		under('c');
		move(200);
		// Coming back unpicks the overshoot.
		expect([...selection.ordered(ORDER)]).toEqual(['b', 'c']);
	});

	it('does nothing until the hold has actually fired', () => {
		tile.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, cancelable: true }));
		vi.advanceTimersByTime(PRESS_HOLD_MS - 50);
		under('d');
		move(300);
		// Before the hold a moving pointer is a drag or a scroll, and it CANCELS the press.
		expect(selection.count).toBe(0);
	});

	it('stops when the button comes up', () => {
		hold();
		under('c');
		move(200);
		expect([...selection.ordered(ORDER)]).toEqual(['b', 'c']);

		window.dispatchEvent(pointer('pointerup'));
		under('d');
		move(300);
		expect([...selection.ordered(ORDER)]).toEqual(['b', 'c']);
	});

	it('ignores a move that is over nothing pickable', () => {
		hold();
		under(null);
		move(300);
		expect([...selection.ordered(ORDER)]).toEqual(['b']);
	});
});

/* A sweep is not a drag: the flag the grid refuses the drag on. */
describe('a sweep is not a drag', () => {
	it('says it is sweeping only between the hold and letting go', () => {
		expect(gesture.sweeping).toBe(false);

		tile.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, cancelable: true }));
		expect(gesture.sweeping, 'a press that has not held yet is still a possible drag').toBe(false);

		vi.advanceTimersByTime(PRESS_HOLD_MS);
		expect(gesture.sweeping, 'the hold has fired, so a move means picking').toBe(true);

		window.dispatchEvent(pointer('pointerup'));
		expect(gesture.sweeping, 'let go and the tile is draggable again').toBe(false);
	});
});

/* A second sweep while something is picked starts a run, not a drag. */
describe('sweeping again while something is already picked', () => {
	function under(id: string | null) {
		const row = id === null ? null : document.createElement('div');
		if (row && id) row.setAttribute('data-tile-id', id);
		document.elementFromPoint = () => row;
	}

	function move(x: number) {
		window.dispatchEvent(pointer('pointermove', { clientX: x, clientY: 100 }));
	}

	/** Something picked somewhere else on the wall, which is what puts the bar up. */
	beforeEach(() => {
		selection.toggle('a');
	});

	it('draws another run from the tile pressed, with no hold to sit through', () => {
		tile.dispatchEvent(pointer('pointerdown'));
		under('d');
		move(300);

		// A second run adds to the selection.
		expect([...selection.ordered(ORDER)]).toEqual(['a', 'b', 'c', 'd']);
	});

	it('waits for the pointer to leave the tile, so a plain click is still a plain click', () => {
		tile.dispatchEvent(pointer('pointerdown'));
		// The press alone picks nothing.
		expect([...selection.ordered(ORDER)]).toEqual(['a']);

		tile.dispatchEvent(pointer('pointerup'));
		gesture.clicked('b', new MouseEvent('click', { bubbles: true, cancelable: true }));
		expect([...selection.ordered(ORDER)]).toEqual(['a', 'b']);
	});

	it('does not toggle the tile it started on back out on the way up', () => {
		// The press's click is the gesture's, as after a hold.
		tile.dispatchEvent(pointer('pointerdown'));
		under('d');
		move(300);
		window.dispatchEvent(pointer('pointerup'));
		gesture.clicked('b', new MouseEvent('click', { bubbles: true, cancelable: true }));

		expect([...selection.ordered(ORDER)]).toEqual(['a', 'b', 'c', 'd']);
	});

	it('UNPICKS what it crosses when the run began on a tile that was picked', () => {
		/* Pressed on a picked tile, the run removes and leaves 'a'. */
		selection.toggle('b');
		expect([...selection.ordered(ORDER)]).toEqual(['a', 'b']);

		tile.dispatchEvent(pointer('pointerdown'));
		under('d');
		move(300);

		expect([...selection.ordered(ORDER)]).toEqual(['a']);
	});

	it('takes an unpick run back onto the selection that was on screen', () => {
		/* `beginRun` sets the direction and takes no step, so one Ctrl+Z undoes the whole run. */
		selection.toggle('b');
		tile.dispatchEvent(pointer('pointerdown'));
		under('d');
		move(300);
		expect([...selection.ordered(ORDER)]).toEqual(['a']);

		selection.undo();
		expect([...selection.ordered(ORDER)]).toEqual(['a', 'b']);
	});

	it('gives back what was overshot, the same as the first sweep does', () => {
		tile.dispatchEvent(pointer('pointerdown'));
		under('d');
		move(300);
		under('c');
		move(200);

		expect([...selection.ordered(ORDER)]).toEqual(['a', 'b', 'c']);
	});

	it('puts back what an unpick run overshot, which is the other half of the same rule', () => {
		// Back to 'c': 'd' returns to picked.
		selection.toggle('b');
		selection.toggle('c');
		selection.toggle('d');
		tile.dispatchEvent(pointer('pointerdown'));
		under('d');
		move(300);
		expect([...selection.ordered(ORDER)]).toEqual(['a']);

		under('c');
		move(200);

		expect([...selection.ordered(ORDER)]).toEqual(['a', 'd']);
	});

	it('lets go of the direction when the hand is lifted', () => {
		// Letting go closes the run; the next shift-click adds.
		selection.toggle('b');
		tile.dispatchEvent(pointer('pointerdown'));
		under('d');
		move(300);
		window.dispatchEvent(pointer('pointerup'));

		selection.pick('d', { shiftKey: true }, ORDER);

		expect([...selection.ordered(ORDER)]).toEqual(['a', 'b', 'c', 'd']);
	});

	it('refuses the native drag from the PRESS, not from the first move', () => {
		// Armed at the press, before a drag pre-empts the moves.
		const event = new Event('dragstart', { bubbles: true, cancelable: true });
		tile.dispatchEvent(pointer('pointerdown'));
		tile.dispatchEvent(event);

		expect(event.defaultPrevented).toBe(true);
	});

	it('keeps the drag reachable with Alt, which is how a selection is dropped on something', () => {
		const event = new Event('dragstart', { bubbles: true, cancelable: true });
		tile.dispatchEvent(
			new MouseEvent('pointerdown', { bubbles: true, cancelable: true, altKey: true })
		);
		tile.dispatchEvent(event);

		expect(event.defaultPrevented).toBe(false);
		// And it picks nothing on the way: an Alt press is the drag and only the drag.
		under('d');
		move(300);
		expect([...selection.ordered(ORDER)]).toEqual(['a']);
	});

	it('does not run the hold, which would have thrown the rest of the selection away', () => {
		tile.dispatchEvent(pointer('pointerdown'));
		vi.advanceTimersByTime(PRESS_HOLD_MS * 2);

		// `only` is the hold's call, and over a selection of forty it drops thirty-nine.
		expect([...selection.ordered(ORDER)]).toEqual(['a']);
		expect(tile.hasAttribute('data-holding'), 'nothing is being counted').toBe(false);
	});

	it('gives the drag back the moment the selection is let go', () => {
		selection.clear();
		const event = new Event('dragstart', { bubbles: true, cancelable: true });
		tile.dispatchEvent(pointer('pointerdown'));
		tile.dispatchEvent(event);

		expect(event.defaultPrevented).toBe(false);
	});
});
