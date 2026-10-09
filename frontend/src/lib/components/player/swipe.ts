// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * The one reader of a quick, straight touch stroke: sideways across the viewer steps the run, down
 * a sheet's head puts it away. Touch only, clearly along one axis, never from a control.
 */
import type { Attachment } from 'svelte/attachments';

import { move } from '$lib/shell/motion.svelte';

/** In CSS pixels. */
export const SWIPE_DISTANCE = 56;
const SWIPE_SLANT = 1.5;
/** A slower stroke is a drag somebody may still take back. */
export const SWIPE_TIME = 800;

const CONTROLS =
	'button, a, input, select, textarea, [role="slider"], [role="menu"], .player-bar, .still-control';

type StrokeWay = 'left' | 'right' | 'up' | 'down';

/** Null for too short, too slow, or too slanted. */
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

/** A control INSIDE the element; the menu around it does not count. */
function onAControl(node: HTMLElement, target: EventTarget | null): boolean {
	if (!(target instanceof Element)) return false;
	const control = target.closest(CONTROLS);
	return control !== null && node.contains(control);
}

export type SwipeWay = 'next' | 'previous';

/** Left is next. */
export function readSwipe(dx: number, dy: number, ms: number): SwipeWay | null {
	const way = readStroke(dx, dy, ms);
	return way === 'left' ? 'next' : way === 'right' ? 'previous' : null;
}

interface StrokeTargets {
	live: boolean;
	on: Partial<Record<StrokeWay, () => void>>;
	/** A stroke DOWN counts as a pull from the top even when the browser took it (`pulledDown`). */
	pull?: boolean;
}

function atTheTop(target: EventTarget | null): boolean {
	for (let at = target instanceof Element ? target : null; at; at = at.parentElement) {
		if (at.scrollTop > 0) return false;
	}
	return (document.scrollingElement?.scrollTop ?? 0) <= 0;
}

/* The click a lifting finger may still produce is swallowed; the element needs `touch-action`. */
export function strokes(targets: () => StrokeTargets): Attachment<HTMLElement> {
	return (node) => {
		type Begun = { x: number; y: number; at: number; pointer: number; top: boolean };
		let start: Begun | null = null;
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

		/* THE PULL DOWN, read from the touch's end, counting only where nothing could scroll up. */
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
	live: boolean;
	next?: () => void;
	previous?: () => void;
	close?: () => void;
}

const STEPS_ATTRIBUTE = 'data-steps';

/** Left is next, right previous; down puts the viewer away. */
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

export const STEP_TRAVEL = 24;

/**
 * The stepped-to file arrives from the side the finger sent it, at `fast`; reduced motion fades.
 */
export function stepArrival(within: Element | null, way: SwipeWay): Promise<void> {
	const box = within?.querySelector(`[${STEPS_ATTRIBUTE}]`) ?? null;
	if (box === null) return Promise.resolve();
	const from = way === 'next' ? STEP_TRAVEL : -STEP_TRAVEL;
	/* The ending style is removed: a leftover transform re-anchors fixed children. */
	const settle = () => {
		(box as HTMLElement).style.removeProperty('transform');
		(box as HTMLElement).style.removeProperty('opacity');
	};
	return move(box, { x: [from, 0], opacity: [0, 1] }, { pace: 'fast' }).then(settle, settle);
}

const CLICK_AFTER_STROKE_MS = 400;

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
