// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * A finger's stroke: the one reader of what a quick, straight stroke across a touch screen means.
 *
 * Two surfaces hear it. A finger drawn sideways across the viewer steps through the run it was
 * opened from (`swipeBetween`), and a finger drawn down a sheet's head puts the sheet away
 * (`Drawer` and the menu sheet, through `strokes`). Both read the same stroke by the same rules,
 * here, so "how far, how straight, how quick" cannot come to mean two things on two screens.
 *
 * Touch only, and only a stroke that is clearly along one axis: a slanted stroke is somebody
 * scrolling or reaching for the tab bar, and a slow drag is somebody deciding, so neither acts. A
 * press that starts on a control is that control's (the scrubber is a sideways drag of its own),
 * and a mouse keeps the buttons, the keys and the press outside it already has.
 *
 * The picture has nothing to pan on a touch screen (magnifying is the wheel's, in full screen), so
 * a sideways stroke on it can only mean the next file. A sheet's head scrolls nothing, so a stroke
 * down it can only mean "put this away", the way every phone's sheet goes.
 */
import type { Attachment } from 'svelte/attachments';

import { move } from '$lib/shell/motion.svelte';

/** How far a stroke must travel along its axis, in CSS pixels, before it is a stroke, not a wobble. */
export const SWIPE_DISTANCE = 56;
/** How much further along its axis than across it the stroke must travel. */
const SWIPE_SLANT = 1.5;
/** How long it may take, in milliseconds. A slower stroke is a drag somebody may still take back. */
export const SWIPE_TIME = 800;

/** Where a press may begin and still be a swipe: anywhere but a control. */
const CONTROLS =
	'button, a, input, select, textarea, [role="slider"], [role="menu"], .player-bar, .still-control';

/** The four ways a finished stroke can go, in screen terms. */
type StrokeWay = 'left' | 'right' | 'up' | 'down';

/**
 * Which way a finished stroke went, or null for a stroke that is not one: too short, too slow, or
 * too slanted to say which axis it was along. The one reading every stroke in the app is judged by.
 */
export function readStroke(dx: number, dy: number, ms: number): StrokeWay | null {
	if (ms > SWIPE_TIME) return null;
	const across = Math.abs(dx);
	const along = Math.abs(dy);
	const sideways = across >= along;
	const travel = sideways ? across : along;
	const drift = sideways ? along : across;
	if (travel < SWIPE_DISTANCE) return null;
	if (travel < SWIPE_SLANT * drift) return null;
	if (sideways) return dx < 0 ? 'left' : 'right';
	return dy > 0 ? 'down' : 'up';
}

/**
 * Whether a press began on a control INSIDE the element, which is that control's to answer.
 *
 * Inside it only: a sheet's head is itself within a menu (`role="menu"`), and the menu around the
 * element is not a control the press began on.
 */
function onAControl(node: HTMLElement, target: EventTarget | null): boolean {
	if (!(target instanceof Element)) return false;
	const control = target.closest(CONTROLS);
	return control !== null && node.contains(control);
}

export type SwipeWay = 'next' | 'previous';

/** Which way a finished stroke steps through a run, or null for one that does not. Left is next. */
export function readSwipe(dx: number, dy: number, ms: number): SwipeWay | null {
	const way = readStroke(dx, dy, ms);
	return way === 'left' ? 'next' : way === 'right' ? 'previous' : null;
}

interface StrokeTargets {
	/** Whether a stroke counts at all right now. */
	live: boolean;
	/** What each way does. A way with nothing here is not a stroke on this element. */
	on: Partial<Record<StrokeWay, () => void>>;
	/**
	 * A stroke DOWN still counts when the browser took it for a scroll, as long as nothing under
	 * the finger could scroll up: the pull that puts a phone's viewer away. See `pulledDown`.
	 */
	pull?: boolean;
}

/** Whether every box the press began in is at its top, so a stroke down there scrolls nothing. */
function atTheTop(target: EventTarget | null): boolean {
	for (let at = target instanceof Element ? target : null; at; at = at.parentElement) {
		if (at.scrollTop > 0) return false;
	}
	return (document.scrollingElement?.scrollTop ?? 0) <= 0;
}

/*
 * The stroke, heard on the element it is drawn across.
 *
 * The click a finger lifting could still produce is swallowed once a stroke has acted, so a stroke
 * that ends on the picture never also pauses what it just moved to, and one that ends on whatever
 * a sheet uncovered never also presses it.
 *
 * The element must leave the stroke to script (`touch-action`): a stroke the browser takes for a
 * scroll or a pan is cancelled half way (`pointercancel`) and never finishes here.
 */
export function strokes(targets: () => StrokeTargets): Attachment<HTMLElement> {
	return (node) => {
		type Begun = { x: number; y: number; at: number; pointer: number; top: boolean };
		let start: Begun | null = null;
		/* A stroke the browser took over (`pointercancel`), kept for its touch's end. */
		let taken: Begun | null = null;

		const down = (event: PointerEvent) => {
			start = null;
			if (event.pointerType !== 'touch' || !event.isPrimary) return;
			if (!targets().live) return;
			if (onAControl(node, event.target)) return;
			start = {
				x: event.clientX,
				y: event.clientY,
				at: event.timeStamp,
				pointer: event.pointerId,
				top: atTheTop(event.target)
			};
			taken = null;
		};

		const up = (event: PointerEvent) => {
			const from = start;
			start = null;
			if (from === null || event.pointerId !== from.pointer) return;
			const way = readStroke(
				event.clientX - from.x,
				event.clientY - from.y,
				event.timeStamp - from.at
			);
			if (way === null) return;
			// A pull counts only from the top, whichever way the stroke reached here.
			if (way === 'down' && targets().pull && !from.top) return;
			const go = targets().on[way];
			if (!go) return;
			swallowNextClick(node);
			go();
		};

		const cancel = () => {
			taken = start;
			start = null;
		};

		/*
		 * THE PULL DOWN. On a box the browser may scroll up and down (`touch-action: pan-y`), a
		 * stroke down is the browser's the moment it moves, and the pointer is cancelled half way.
		 * The touch itself still ends here, so a pulled stroke is read from its touch's end, and it
		 * counts only when it began where nothing could scroll up: at the top of a box, a stroke
		 * down is not a scroll, it is the pull every phone's viewer answers by going away.
		 */
		const pulledDown = (event: TouchEvent) => {
			const from = taken;
			taken = null;
			if (from === null || !from.top || !targets().pull) return;
			const touch = event.changedTouches[0];
			if (!touch) return;
			const way = readStroke(
				touch.clientX - from.x,
				touch.clientY - from.y,
				event.timeStamp - from.at
			);
			if (way !== 'down') return;
			const go = targets().on.down;
			if (!go) return;
			swallowNextClick(node);
			go();
		};

		node.addEventListener('pointerdown', down);
		node.addEventListener('pointerup', up);
		node.addEventListener('pointercancel', cancel);
		node.addEventListener('touchend', pulledDown);
		return () => {
			node.removeEventListener('pointerdown', down);
			node.removeEventListener('pointerup', up);
			node.removeEventListener('pointercancel', cancel);
			node.removeEventListener('touchend', pulledDown);
		};
	};
}

interface SwipeTargets {
	/** Whether a stroke counts at all right now: the viewer is filling a phone's screen. */
	live: boolean;
	next?: () => void;
	previous?: () => void;
	/** Put the viewer away: a stroke down the picture, from the top of what it scrolls in. */
	close?: () => void;
}

/** The mark on the box a run is stepped through on, for `stepArrival` to find it by. */
const STEPS_ATTRIBUTE = 'data-steps';

/** A sideways stroke across the picture steps through the run: left is next, right is previous.
 *  A stroke down it puts the viewer away. */
export function swipeBetween(targets: () => SwipeTargets): Attachment<HTMLElement> {
	const heard = strokes(() => {
		const { live, next, previous, close } = targets();
		return { live, on: { left: next, right: previous, down: close }, pull: true };
	});
	return (node) => {
		node.setAttribute(STEPS_ATTRIBUTE, '');
		const stop = heard(node);
		return () => {
			node.removeAttribute(STEPS_ATTRIBUTE);
			stop?.();
		};
	};
}

/** How far the next file travels as it arrives, in px: enough to say which way, not a lurch. */
export const STEP_TRAVEL = 24;

/**
 * The file a step landed on, arriving from the side the finger sent it to.
 *
 * A stroke to the left moves the run on, so the next file comes in from the right, and the one before
 * comes in from the left: the travel says which way along the run the step went, the way a phone's
 * photographs slide. Called once the stepped-to file is on screen, since the viewer follows the id
 * without being rebuilt and the picture changes when the file has loaded, not when the stroke ends.
 * At `fast`, a small surface's pace: a step is a small thing, and it is repeated. Reduced motion is
 * the fade alone (`move`).
 */
export function stepArrival(within: Element | null, way: SwipeWay): Promise<void> {
	const box = within?.querySelector(`[${STEPS_ATTRIBUTE}]`) ?? null;
	if (box === null) return Promise.resolve();
	const from = way === 'next' ? STEP_TRAVEL : -STEP_TRAVEL;
	/* The inline style the movement ends on is taken off again: a transform left on the box, even a
	   zero one, makes it the box every fixed element inside it is placed against. */
	const settle = () => {
		(box as HTMLElement).style.removeProperty('transform');
		(box as HTMLElement).style.removeProperty('opacity');
	};
	return move(box, { x: [from, 0], opacity: [0, 1] }, { pace: 'fast' }).then(settle, settle);
}

/** How long after a stroke the click it could produce may still arrive. */
const CLICK_AFTER_STROKE_MS = 400;

/** The one click that may follow a stroke, taken before the picture hears it. */
function swallowNextClick(node: HTMLElement): void {
	const stop = (event: Event) => {
		event.stopPropagation();
		event.preventDefault();
	};
	node.addEventListener('click', stop, { capture: true, once: true });
	setTimeout(
		() => node.removeEventListener('click', stop, { capture: true }),
		CLICK_AFTER_STROKE_MS
	);
}
