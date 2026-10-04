/*
 * MARKING THE CELL A CONTROL IS ABOUT, while the pointer is on it.
 *
 * ## What it is for
 *
 * A wall is up to nine rectangles and most of the controls that change one of them are nowhere near
 * it: a row of numbers at the foot of a filled screen, a cell chooser at the head of the filter
 * panel, a transport on a bar. A number says nothing about which rectangle it means until you press
 * it. So pointing at one washes that cell in the accent, which is what "this is the one you are
 * about to act on" already means everywhere else in Sift.
 *
 * ## Why it is a module rather than four lines per control
 *
 * Four lines per control, written out per control, is a rule with no single home. And the copy
 * that matters most is the one that goes unwritten: the cell chooser in the filter panel, where
 * somebody chooses a cell and then edits it at length, lighting nothing. So there is one object and
 * every control that names a cell spreads it. Adding a control that names a cell is one word; a
 * missing spread is far easier to see than four missing handlers.
 *
 * ## Read when the pointer ARRIVES, never captured
 *
 * `at` may be a function, and the two kinds of control need the difference. A NUMBER aims at
 * itself: hovering "Cell 2" marks cell two whatever is selected, because pressing it would select
 * cell two. A control that acts on WHATEVER IS SELECTED (the filter button on the bar) has to
 * ask at the moment the pointer lands, or moving between the All chip and a number under a held
 * pointer marks the cell that was chosen when the component was built.
 */
import { createAttachmentKey } from 'svelte/attachments';
import type { Attachment } from 'svelte/attachments';
import type { Wall } from '$lib/theater/wall.svelte';

/** Which cell a control is about: one of them, all of them, or a question asked when it is needed. */
type Aim = number | 'every' | (() => number | 'every');

/** What a control spreads: the four handlers, and the attachment that lets go on the way out. */
export type Aiming = {
	onmouseenter: (event?: Event) => void;
	onmouseleave: () => void;
	onfocus: (event?: Event) => void;
	onblur: () => void;
	[key: symbol]: Attachment;
};

/**
 * The four handlers a control spreads to mark the cell it is about.
 *
 * Focus as well as the pointer, and that is not decoration: the whole row is reachable by tab, and
 * somebody moving through it with the keyboard is asking the same question about the same cells.
 *
 * ## And an ATTACHMENT, so a control that leaves while pointed at lets go
 *
 * `mouseleave` and `blur` are not enough: an element taken out of the page fires neither. A panel
 * shut with Escape while the pointer rests on "Cell 2", a kept group deleted from under the
 * pointer: the wash would stay on the wall with nothing left on screen that could ever take it
 * off. This is the general answer, in the one place every control already spreads.
 *
 * Only the mark THIS ELEMENT put up is let go. One set of handlers is spread over a whole row of
 * controls (a cell's bar spreads one object over ten), so "this object marked something" is not
 * enough: a control in that row disappearing while the pointer rests on its neighbour must leave
 * the neighbour's mark alone. The element the mark came from is kept, and only its leaving counts.
 * And if the pointer has since arrived on some other control, that control's mark is the current
 * one and is left alone too.
 */
export function aims(wall: Wall, at: Aim): Aiming {
	/* Who put the wall's current mark up: this set of handlers, and from which of its elements.
	   Kept OUTSIDE the wall's reactive state on purpose. The question is asked in a teardown, and a
	   teardown reading `$state` is handed the value from before the batch it is torn down in,
	   so "is the wall's mark still mine" read from `wall.aiming` there can answer about a moment
	   that has already passed. A plain record written beside every mark cannot. */
	const me = {};
	let from: EventTarget | null = null;
	const mark = (on: boolean, event?: Event) => {
		from = on ? (event?.currentTarget ?? null) : null;
		if (on) HELD_BY.set(wall, me);
		else if (HELD_BY.get(wall) === me) HELD_BY.delete(wall);
		wall.aiming = on ? (typeof at === 'function' ? at() : at) : null;
	};
	const letGo: Attachment = (node) => () => {
		if (from !== node || HELD_BY.get(wall) !== me) return;
		HELD_BY.delete(wall);
		from = null;
		wall.aiming = null;
	};
	return {
		onmouseenter: (event) => mark(true, event),
		onmouseleave: () => mark(false),
		onfocus: (event) => mark(true, event),
		onblur: () => mark(false),
		[createAttachmentKey()]: letGo
	};
}

/** Which set of handlers put each wall's current mark up. See `aims`. */
const HELD_BY = new WeakMap<Wall, object>();

/**
 * What a control that acts on the CURRENT selection aims at: every cell, or the one chosen.
 *
 * Written once because both bars ask it, and two copies agree only until one of them is edited.
 *
 * `chosen` is a QUESTION rather than a number: the cell bar builds this once, at setup, and its own
 * index moves under it when the wall is rearranged. A number captured there would mark whichever
 * cell it was built beside for the rest of the component's life.
 */
export function aimsAtChosen(wall: Wall, chosen: () => number): Aim {
	return () => (wall.everyCell ? 'every' : chosen());
}
