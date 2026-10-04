/*
 * Press and hold to select, proved rather than assumed.
 *
 * Every one of these is a way the gesture fails without anybody reporting it as a bug: they
 * report it as "it sometimes doesn't select" or "it opened the file as well".
 *
 * The one worth reading twice is the last group. A hold ends in a pointerup, a pointerup on a
 * button is a click, and a click on a tile opens it, so without the suppression, holding a tile
 * selects it and then immediately opens the thing it just selected.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { Selection } from './selection.svelte';
import { PRESS_HOLD_MS, TileGesture } from './tile-gesture.svelte';

const ORDER = ['a', 'b', 'c', 'd'];

/**
 * A pointer event jsdom will actually construct.
 *
 * `PointerEvent` is not implemented there, so a plain `MouseEvent` carries the fields the gesture
 * reads. `currentTarget` is only set by real dispatch, so the tests that care about the cue
 * dispatch through an element rather than calling the method with a hand-made event.
 */
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
	/*
	 * And let go of the pointer, which is what actually ends a SWEEP.
	 *
	 * `pressEnd` ends the press. The sweep is a second set of window listeners armed after the
	 * hold fires, and only a pointerup takes them off, so a test that swept and never released
	 * would leave them on the window for the next test, where a leaked one refuses `dragstart` in
	 * a test that held nothing.
	 *
	 * Not a leak in the application, and it is worth saying which: `#stopSweeping` is on the
	 * WINDOW, so a real pointerup reaches it even after the wall that started the sweep has
	 * unmounted. This is the one place a pointer can go missing.
	 */
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
		// A tile has drag handlers of its own. A drag that also selected what it was dragging would
		// pick things up by accident, every time.
		tile.dispatchEvent(pointer('pointerdown', { clientX: 100, clientY: 100 }));
		window.dispatchEvent(pointer('pointermove', { clientX: 140, clientY: 100 }));
		vi.advanceTimersByTime(PRESS_HOLD_MS);

		expect(selection.count).toBe(0);
	});

	it('forgives a hand that is not perfectly still', () => {
		// The reason there is a slop radius at all. With none the gesture works for people with
		// steady hands and fails for everybody else, which is the worst way for it to fail.
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

		// Something is selected now, so this one toggles rather than opening, but it is handled as
		// a click on a selection rather than as the tail of the hold.
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
		// Escape means something else to every dialog, menu and search box on the page, and
		// swallowing it while nothing is picked would take it from them.
		expect(gesture.escaped()).toBe(false);
	});

	it('leaves the selection alone when something nearer already answered the key', () => {
		/*
		 * Escape closes the innermost thing: menu before dialog before overlay.
		 *
		 * A menu is portalled to the end of the document, so the key travels from it up to the
		 * window handler this sits behind; the menu closes itself and the event carries on. So one
		 * press would do two things, and the selection the menu was opened ON would go with it:
		 * open a menu, press Escape, and the bar you were about to use is gone.
		 *
		 * Asserted on `defaultPrevented` rather than on an open menu being in the document, because
		 * in the running application the menu has already flipped its own state to closed by the
		 * time this runs, so a check for one would never fire.
		 */
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
		// They call back into the gesture, so a leaked pair fires on every pointer move and every
		// scroll on the page, for the rest of the session, holding the tile that started it.
		const remove = vi.spyOn(window, 'removeEventListener');
		tile.dispatchEvent(pointer('pointerdown'));
		gesture.pressEnd();

		expect(remove).toHaveBeenCalledWith('pointermove', expect.any(Function));
		expect(remove).toHaveBeenCalledWith('scroll', expect.any(Function), { capture: true });
	});

	it('removes the same function it added, or nothing ever comes off', () => {
		// A method passed straight to addEventListener is a new bound function every call and can
		// never be removed. On a grid that is one leaked listener per press.
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

/*
 * The sweep: hold, then keep the button down and move.
 *
 * A synthetic drag does not fire an event per step, and jsdom has no layout at all, so
 * `elementFromPoint` answers nothing here whatever the coordinates say. Both are stubbed, which
 * means what these prove is the RULE the sweep follows and never that a real mouse reaches it.
 * That half is a hand check.
 */
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

	/*
	 * The native drag, refused, which is the difference between this gesture working and not.
	 *
	 * A drag session pre-empts `pointermove` entirely, so on any draggable tile the sweep gets its
	 * hold and never sees another move. Almost every tile is draggable without anybody asking: an
	 * `<a href>` is, and so is an `<img>`.
	 *
	 * **jsdom cannot START a native drag, so this can never prove the gesture survives one.**
	 * What it proves is that the refusal is installed for exactly the window it is needed and taken
	 * off again. The other half is a hand check.
	 */
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

		// And it is given back the moment the button comes up: a wall that legitimately drags
		// must not be left unable to.
		window.dispatchEvent(pointer('pointerup', { clientX: 300, clientY: 100 }));
		expect(dragged()).toBe(false);
	});

	it("does not reach the tile's own handler while a sweep is running", () => {
		/* Capture phase and `stopPropagation`, so a wall that builds a drag payload never starts
		 * building one. Prevented but still delivered, the media grid would assemble a transfer for
		 * a drag that is not happening. */
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
		// The whole reason this extends rather than toggling each: going too far and coming back
		// has to UNPICK the overshoot, and a toggle-each would have left 'd' picked.
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

/*
 * The one a unit test cannot reach, guarded here anyway.
 *
 * A tile is `draggable`, so pressing it and moving starts a NATIVE drag session, which pre-empts
 * `pointermove` entirely, so the sweep would get its hold and never see another move. jsdom has no
 * drag-and-drop and a synthetic pointer sequence never starts one, so none of the twenty checks
 * above it can see this.
 *
 * What is provable here is the FLAG the grid refuses the drag on. That the grid reads it is a
 * contract test's job, and that a real mouse then sweeps is a hand check.
 */
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

/*
 * Sweeping again once something is already picked.
 *
 * With a selection already made, a press-and-move must start a run rather than a native drag of the
 * tile under the hand, or the gesture reads as used up after the first sweep.
 *
 * The same stubs as the group above, and the same limit: jsdom cannot start a real drag, so what is
 * proved is the rule and the window the refusal is armed for. A real second sweep is a hand check.
 */
describe('sweeping again while something is already picked', () => {
	/** Stand in for the layout: whatever id the next move should land on. */
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

		// 'b' is where the press was and 'd' is where the pointer is; 'c' was crossed. 'a' stays,
		// because a second run ADDS to a selection rather than starting over.
		expect([...selection.ordered(ORDER)]).toEqual(['a', 'b', 'c', 'd']);
	});

	it('waits for the pointer to leave the tile, so a plain click is still a plain click', () => {
		tile.dispatchEvent(pointer('pointerdown'));
		// The press has armed nothing visible. Everything that was picked is still picked and the
		// tile pressed is not.
		expect([...selection.ordered(ORDER)]).toEqual(['a']);

		tile.dispatchEvent(pointer('pointerup'));
		gesture.clicked('b', new MouseEvent('click', { bubbles: true, cancelable: true }));
		expect([...selection.ordered(ORDER)]).toEqual(['a', 'b']);
	});

	it('does not toggle the tile it started on back out on the way up', () => {
		// The click a press produces is the gesture's, exactly as it is after a hold. Without that
		// the run's own first tile would come straight back off.
		tile.dispatchEvent(pointer('pointerdown'));
		under('d');
		move(300);
		window.dispatchEvent(pointer('pointerup'));
		gesture.clicked('b', new MouseEvent('click', { bubbles: true, cancelable: true }));

		expect([...selection.ordered(ORDER)]).toEqual(['a', 'b', 'c', 'd']);
	});

	it('UNPICKS what it crosses when the run began on a tile that was picked', () => {
		/*
		 * The press is on 'b', which is picked, so this run takes 'b', 'c' and 'd' off and leaves
		 * 'a', which the hand never went near. A run that could only add would never let a sweep
		 * back rub anything out.
		 */
		selection.toggle('b');
		expect([...selection.ordered(ORDER)]).toEqual(['a', 'b']);

		tile.dispatchEvent(pointer('pointerdown'));
		under('d');
		move(300);

		expect([...selection.ordered(ORDER)]).toEqual(['a']);
	});

	it('takes an unpick run back onto the selection that was on screen', () => {
		/*
		 * Where `beginRun` and `toggle` actually differ, which is not where it looks.
		 *
		 * `toggle` acts on the tile it is handed and the run is then drawn over it anyway, so for a
		 * run that adds the two end with the same tiles picked. They differ in the direction (a
		 * `toggle` would leave the run adding, and this group is about a run that removes) and in
		 * what the gesture leaves to take back: `beginRun` changes nothing and takes no step, so
		 * one Ctrl+Z lands on what was picked before the hand moved rather than on a half-drawn
		 * selection nobody saw.
		 */
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
		// Out over 'c' and 'd' with the run removing, then back to 'c': 'd' is no longer in the run,
		// so it returns to the state it was in before the hand moved, which is picked.
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
		// A run that removed things must not leave the next range removing. Letting go closes the
		// run; a shift-click after it is a gesture of its own and adds, as it always has.
		selection.toggle('b');
		tile.dispatchEvent(pointer('pointerdown'));
		under('d');
		move(300);
		window.dispatchEvent(pointer('pointerup'));

		selection.pick('d', { shiftKey: true }, ORDER);

		expect([...selection.ordered(ORDER)]).toEqual(['a', 'b', 'c', 'd']);
	});

	it('refuses the native drag from the PRESS, not from the first move', () => {
		// A drag session pre-empts `pointermove`, so a refusal armed after the first move is armed
		// after the only event that could have told us to.
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
