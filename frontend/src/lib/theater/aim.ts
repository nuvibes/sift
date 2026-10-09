/* Washing the cell a control is about while pointed at or focused: most controls that change a
 * cell are nowhere near it. A function `at` is read when the pointer arrives. */
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
 * The handlers a control spreads to mark the cell it is about, and an attachment that lets go of
 * this element's own mark when it leaves the page, which fires neither `mouseleave` nor `blur`.
 */
export function aims(wall: Wall, at: Aim): Aiming {
	/* Outside the wall's reactive state: a teardown reading `$state` sees the value from before
	   its batch. */
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

/** What a control on the CURRENT selection aims at; `chosen` is asked, since the index moves. */
export function aimsAtChosen(wall: Wall, chosen: () => number): Aim {
	return () => (wall.everyCell ? 'every' : chosen());
}
