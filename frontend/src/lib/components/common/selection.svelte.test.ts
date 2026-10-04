import { describe, expect, it } from 'vitest';
import { Selection } from './selection.svelte';

/* Picking several things out of a grid.
 *
 * The gestures here are ones people arrive already knowing, which is exactly why they have to be
 * right: a shift-click that ranges from the wrong end is not a bug somebody reports, it is a screen
 * that feels broken.
 *
 * One of them is deliberately NOT the file manager's. A plain click adds and removes rather than
 * starting over, because in this app a plain click on nothing-picked opens the asset, so every
 * click that gets here is one where a set is already being built. See `pick`.
 */

const items = ['a', 'b', 'c', 'd', 'e'];

const plain = { shiftKey: false, ctrlKey: false, metaKey: false };
const shift = { shiftKey: true, ctrlKey: false, metaKey: false };
const ctrl = { shiftKey: false, ctrlKey: true, metaKey: false };
const meta = { shiftKey: false, ctrlKey: false, metaKey: true };

describe('picking', () => {
	it('starts with nothing picked', () => {
		const selection = new Selection();

		expect(selection.count).toBe(0);
		expect(selection.isEmpty).toBe(true);
	});

	it('a plain click ADDS, rather than starting a new selection', () => {
		/* Sending a plain click to the file-manager answer would pick "just this one", so choosing
		 * a second tile would silently drop the first and the count in the bar would stay at one
		 * however many things were clicked. */
		const selection = new Selection();
		selection.pick('a', plain, items);
		selection.pick('c', plain, items);

		expect(selection.ordered(items)).toEqual(['a', 'c']);
	});

	it('and a plain click on something already picked takes it away', () => {
		// The other half of "adds and removes". Without it there is no way to correct a mis-click
		// except to clear the whole selection and start again.
		const selection = new Selection();
		selection.pick('a', plain, items);
		selection.pick('c', plain, items);
		selection.pick('a', plain, items);

		expect(selection.ordered(items)).toEqual(['c']);
	});

	it('a pick the screen no longer shows is still picked, after the visible ones', () => {
		/* Two people found by two searches: the second search takes the first pick off the screen.
		 * A plain filter over the rows on screen would drop it, and a merge of two would reach the
		 * sheet as a merge of one, the wrong way round. */
		const selection = new Selection();
		selection.pick('a', plain, items);
		selection.pick('c', plain, items);

		expect(selection.ordered(['c', 'd'])).toEqual(['c', 'a']);
		expect(selection.ordered([])).toEqual(['a', 'c']);
	});

	it('starting from nothing is the long press, not a click', () => {
		// `only` is still the right answer for beginning a selection, and it is what the long press
		// calls. It is not what a click means once one exists.
		const selection = new Selection();
		selection.pick('a', plain, items);
		selection.pick('c', plain, items);

		selection.only('e');

		expect(selection.ordered(items)).toEqual(['e']);
	});

	it('ctrl-click adds without dropping what is already picked', () => {
		const selection = new Selection();
		selection.pick('a', plain, items);
		selection.pick('c', ctrl, items);

		expect(selection.ordered(items)).toEqual(['a', 'c']);
	});

	it('the same on a mac, where the key is cmd', () => {
		const selection = new Selection();
		selection.pick('a', plain, items);
		selection.pick('c', meta, items);

		expect(selection.ordered(items)).toEqual(['a', 'c']);
	});

	it('ctrl-click on something already picked takes it away again', () => {
		const selection = new Selection();
		selection.pick('a', plain, items);
		selection.pick('b', ctrl, items);
		selection.pick('a', ctrl, items);

		expect(selection.ordered(items)).toEqual(['b']);
	});
});

describe('ranges', () => {
	it('shift-click takes everything between', () => {
		const selection = new Selection();
		selection.pick('b', plain, items);
		selection.pick('d', shift, items);

		expect(selection.ordered(items)).toEqual(['b', 'c', 'd']);
	});

	it('and it works backwards', () => {
		const selection = new Selection();
		selection.pick('d', plain, items);
		selection.pick('b', shift, items);

		expect(selection.ordered(items)).toEqual(['b', 'c', 'd']);
	});

	it('a range dragged too far can be dragged back', () => {
		/* The point of the anchor, and of recomputing rather than adding.
		 *
		 * Out to `e`, then back to `c`, with nothing cleared in between, because a clear would hide
		 * the fault completely: after a clear both a correct implementation and one whose anchor
		 * follows the last click behave identically.
		 *
		 * Two ways to fail. An anchor that follows the last click ranges c-e and leaves b-e. Adding
		 * to the current set instead of recomputing can only grow, so it also leaves b-e. Both are a
		 * selection that will not let go of a mistake. */
		const selection = new Selection();
		selection.pick('b', plain, items);
		selection.pick('e', shift, items);

		expect(selection.ordered(items)).toEqual(['b', 'c', 'd', 'e']);

		selection.pick('c', shift, items);

		expect(selection.ordered(items)).toEqual(['b', 'c']);
	});

	it('and a range does not swallow what was picked before it', () => {
		/* The other half of recomputing: what was ctrl-picked before the range gesture survives it,
		 * because the range is added to that rather than replacing it. */
		const selection = new Selection();
		selection.pick('a', plain, items);
		selection.pick('c', ctrl, items);
		selection.pick('e', shift, items);

		expect(selection.ordered(items)).toEqual(['a', 'c', 'd', 'e']);
	});

	it('shift-click with nothing picked yet is just a click', () => {
		const selection = new Selection();
		selection.pick('c', shift, items);

		expect(selection.ordered(items)).toEqual(['c']);
	});

	it('shift-click onto something not in the list changes nothing', () => {
		const selection = new Selection();
		selection.pick('b', plain, items);
		selection.pick('gone', shift, items);

		expect(selection.ordered(items)).toEqual(['b']);
	});

	it('ranges from an anchor that has left the list start over', () => {
		const selection = new Selection();
		selection.pick('b', plain, items);
		selection.retain(['c', 'd', 'e']);
		selection.pick('d', shift, ['c', 'd', 'e']);

		expect(selection.ordered(['c', 'd', 'e'])).toEqual(['d']);
	});
});

describe('what comes out', () => {
	it('is in the order on screen, not the order clicked', () => {
		/* A bar saying "3 selected" and an action running over them should agree with what somebody
		 * is looking at. Insertion order would hand back a shift-dragged range in whichever direction
		 * it was dragged, which nobody chose and nobody can see. */
		const selection = new Selection();
		selection.pick('d', plain, items);
		selection.pick('a', ctrl, items);
		selection.pick('c', ctrl, items);

		expect(selection.ordered(items)).toEqual(['a', 'c', 'd']);
	});
});

describe('select all', () => {
	it('takes everything on screen', () => {
		const selection = new Selection();
		selection.toggleAll(items);

		expect(selection.count).toBe(5);
	});

	it('and drops it all when everything is already picked', () => {
		const selection = new Selection();
		selection.toggleAll(items);
		selection.toggleAll(items);

		expect(selection.isEmpty).toBe(true);
	});

	it('a partial selection becomes everything rather than nothing', () => {
		const selection = new Selection();
		selection.pick('a', plain, items);
		selection.toggleAll(items);

		expect(selection.count).toBe(5);
	});

	it('on an empty list it picks nothing rather than claiming to have picked everything', () => {
		const selection = new Selection();
		selection.toggleAll([]);

		expect(selection.isEmpty).toBe(true);
	});
});

describe('when the list changes underneath', () => {
	it('anything no longer on screen is forgotten', () => {
		/* The count in the bar has to match the screen. On a destructive action the gap between them
		 * is the difference between deleting what you meant and deleting more. */
		const selection = new Selection();
		selection.toggleAll(items);

		selection.retain(['a', 'b']);

		expect(selection.ordered(['a', 'b'])).toEqual(['a', 'b']);
		expect(selection.count).toBe(2);
	});

	it('and a list that lost nothing leaves the selection alone', () => {
		const selection = new Selection();
		selection.pick('a', plain, items);
		selection.retain(items);

		expect(selection.ordered(items)).toEqual(['a']);
	});
});

describe('taking back a click', () => {
	const ITEMS = ['a', 'b', 'c', 'd', 'e'];

	it('undoes a whole shift-run in one press, not one tile at a time', () => {
		// The decision this encodes: a range was taken in one gesture, so it comes back in one.
		// Undoing it twenty times would be a worse convenience than having none.
		const selection = new Selection();
		selection.only('a');
		selection.extendTo('e', ITEMS);
		expect(selection.count).toBe(5);

		expect(selection.undo()).toBe(true);

		expect([...selection.ids]).toEqual(['a']);
		// And the run really was ONE step, not five that happen to restore the same thing: two
		// gestures were made, so two presses go all the way back and a third has nothing to do.
		expect(selection.undo()).toBe(true);
		expect(selection.isEmpty).toBe(true);
		expect(selection.canUndo).toBe(false);
	});

	it('brings a whole selection back after a mis-hit Clear', () => {
		// Where this earns its keep. Clearing is a step, so forty picked files come back in one press.
		const selection = new Selection();
		selection.only('a');
		selection.toggle('c');
		selection.clear();
		expect(selection.isEmpty).toBe(true);

		expect(selection.undo()).toBe(true);

		expect([...selection.ids].sort()).toEqual(['a', 'c']);
	});

	it('puts back whatever undo just took away', () => {
		const selection = new Selection();
		selection.only('a');
		selection.toggle('b');
		selection.undo();
		expect([...selection.ids]).toEqual(['a']);

		expect(selection.redo()).toBe(true);

		expect([...selection.ids].sort()).toEqual(['a', 'b']);
	});

	it('drops what was taken back once a new click carries on from there', () => {
		// Every undo stack does this: once you carry on from a point you went back to, the branch
		// you left is not somewhere that can be returned to.
		const selection = new Selection();
		selection.only('a');
		selection.toggle('b');
		selection.undo();
		selection.toggle('d');

		expect(selection.canRedo).toBe(false);
		expect(selection.redo()).toBe(false);
	});

	it('does nothing, and says so, with nothing to take back', () => {
		const selection = new Selection();
		expect(selection.canUndo).toBe(false);
		expect(selection.undo()).toBe(false);
		expect(selection.redo()).toBe(false);
	});

	it('empties the steps when the screen underneath does', () => {
		/* A selection undo that outlives the wall it belonged to is a surprise rather than a
		   convenience: the ids in it name rows that are not there any more. */
		const selection = new Selection();
		selection.only('a');
		selection.toggle('b');
		expect(selection.canUndo).toBe(true);

		selection.retain(['c', 'd']);

		expect(selection.canUndo).toBe(false);
		expect(selection.undo()).toBe(false);
	});

	it('keeps the steps while the screen holds still', () => {
		// The other half of the one above: without it, "empties the steps" would pass on a
		// `retain` that cleared them every time it was called, which is most renders.
		const selection = new Selection();
		selection.only('a');
		selection.toggle('b');

		selection.retain(ITEMS);

		expect(selection.canUndo).toBe(true);
		expect(selection.undo()).toBe(true);
		expect([...selection.ids]).toEqual(['a']);
	});
});

/*
 * Picking a whole question, which reaches past the page the screen has loaded.
 *
 * "Select 1,000 of 1,250" reads the whole query, and what a verb acts on must be all of it:
 * `ordered`, as a filter over the rows on screen, would hand the verb only the loaded page, and
 * `retain` would drop everything not on screen at the next re-read. `page` below is the screenful;
 * `everything` is what the question matches.
 */
describe('picking a whole question', () => {
	const page = ['a', 'b'];
	const everything = ['a', 'b', 'c', 'd', 'e'];

	it('hands the verb every id, not the ones that happen to be drawn', () => {
		const selection = new Selection();

		selection.pickWholeQuery(everything);

		expect(selection.count).toBe(5);
		// The count in the bar and the list a verb runs over are the same set. That is the whole of it.
		expect(selection.ordered(page)).toEqual(everything);
		expect(selection.ordered(page)).toHaveLength(selection.count);
	});

	it('keeps them when the page underneath is re-read', () => {
		const selection = new Selection();
		selection.pickWholeQuery(everything);

		// The wall re-reads itself on a bell and on a timer, handing the rows it now holds.
		selection.retain(page);

		expect(selection.count).toBe(5);
		expect(selection.ordered(page)).toEqual(everything);
	});

	it('does not clear itself when it is pressed a second time', () => {
		/* The offer stays on the bar while the question is bigger than the ceiling, so the second
		   press is handed the same capped list with every id in it already picked, which is the
		   one case `toggleAll` reads as "everything is on, so turn it off". */
		const selection = new Selection();

		selection.pickWholeQuery(everything);
		selection.pickWholeQuery(everything);

		expect(selection.count).toBe(5);
	});

	it('is a pick of the SCREEN again the moment a tile is clicked', () => {
		const selection = new Selection();
		selection.pickWholeQuery(everything);

		selection.pick('c', plain, everything);

		// Back to the ordinary rules: what is picked is what the screen can show.
		expect(selection.beyondScreen).toBe(false);
		selection.retain(page);
		expect(selection.ordered(page)).toEqual(page);
	});

	it('and a select-all over the page is a pick of the screen too', () => {
		/* `toggleAll` is the page's own control and keeps its own rule: everything it was handed is
		   already picked, so it drops the lot. What matters here is that it ends the other mode:
		   otherwise the flag would outlive the pick that set it and the NEXT ordinary pick would
		   quietly claim to reach past the screen. */
		const selection = new Selection();
		selection.pickWholeQuery(everything);

		selection.toggleAll(page);

		expect(selection.beyondScreen).toBe(false);
		expect(selection.count).toBe(0);

		// And what it picks after that is the page, read under the ordinary rules.
		selection.toggleAll(page);
		expect(selection.beyondScreen).toBe(false);
		expect(selection.ordered(page)).toEqual(page);
	});

	it('takes the whole thing back in one step, under its own rules', () => {
		// The flag travels with the ids. Restored without it, the thousand would come back as a
		// pick of the page and the first read of them would cut it to what is drawn.
		const selection = new Selection();
		selection.only('a');

		selection.pickWholeQuery(everything);
		expect(selection.undo()).toBe(true);
		expect(selection.beyondScreen).toBe(false);
		expect(selection.ordered(page)).toEqual(['a']);

		expect(selection.redo()).toBe(true);
		expect(selection.beyondScreen).toBe(true);
		expect(selection.ordered(page)).toEqual(everything);
	});

	it('lets go of everything on a clear, like any other pick', () => {
		const selection = new Selection();
		selection.pickWholeQuery(everything);

		selection.clear();

		expect(selection.count).toBe(0);
		expect(selection.beyondScreen).toBe(false);
	});
});

/*
 * A sweep paints, and the tile it started on says which way.
 *
 * A run that asked each tile whether it was picked could only add, so sweeping back over tiles
 * would leave them picked. The direction is read once, where the run begins, and the run is
 * recomputed from what was picked before it on every move, so a tile the run has left returns to
 * its state before the run, in both directions.
 */
describe('a run, and which way it paints', () => {
	it('picks everything it crosses when it began on a tile nobody had picked', () => {
		const selection = new Selection();
		selection.toggle('a');

		selection.beginRun('b');
		selection.extendTo('d', items);

		expect(selection.ordered(items)).toEqual(['a', 'b', 'c', 'd']);
	});

	it('UNPICKS everything it crosses when it began on a tile that was picked', () => {
		// The fault itself. This run started on 'b', which was picked, so it takes 'b', 'c' and 'd'
		// back off and leaves 'a', which nothing pointed at, exactly where it was.
		const selection = new Selection();
		selection.toggleAll(['a', 'b', 'c', 'd']);

		selection.beginRun('b');
		selection.extendTo('d', items);

		expect(selection.ordered(items)).toEqual(['a']);
	});

	it('gives back what a PICK run overshot when the hand comes back', () => {
		const selection = new Selection();

		selection.beginRun('b');
		selection.extendTo('d', items);
		selection.extendTo('c', items);

		// 'd' returns to what it was before the run, which is unpicked.
		expect(selection.ordered(items)).toEqual(['b', 'c']);
	});

	it('gives back what an UNPICK run overshot when the hand comes back', () => {
		// Sweeping back over what a run took off puts it back, because the run is redrawn rather
		// than toggled tile by tile.
		const selection = new Selection();
		selection.toggleAll(['a', 'b', 'c', 'd']);

		selection.beginRun('b');
		selection.extendTo('d', items);
		selection.extendTo('c', items);

		expect(selection.ordered(items)).toEqual(['a', 'd']);
	});

	it('leaves a tile the run crossed and left exactly as it found it', () => {
		// A PICK run over a tile that was already picked must not take it off on the way back. The
		// run adds; what it stops covering returns to the selection it started from.
		const selection = new Selection();
		selection.toggle('c');

		selection.beginRun('a');
		selection.extendTo('d', items);
		selection.extendTo('a', items);

		expect(selection.ordered(items)).toEqual(['a', 'c']);
	});

	it('takes the whole movement back in one press, onto a selection that was on screen', () => {
		/* A run that has begun and not moved has done nothing, so it takes no step of its own. One
		 * press therefore lands on what was picked before the hand moved, not on the half-drawn
		 * selection between the press and the first tile crossed, which nobody ever saw. */
		const selection = new Selection();
		selection.toggleAll(['a', 'b', 'c', 'd']);

		selection.beginRun('b');
		selection.extendTo('d', items);

		expect(selection.undo()).toBe(true);
		expect(selection.ordered(items)).toEqual(['a', 'b', 'c', 'd']);
	});

	it('forgets the direction when the hand is lifted', () => {
		// Otherwise the next shift-click would go on removing, because the sweep before it did.
		const selection = new Selection();
		selection.toggleAll(['a', 'b', 'c', 'd']);
		selection.beginRun('b');
		selection.extendTo('d', items);
		selection.endRun();

		selection.extendTo('d', items);

		expect(selection.ordered(items)).toEqual(['a', 'b', 'c', 'd']);
	});
});
