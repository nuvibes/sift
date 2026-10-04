/*
 * How a cover sits in its frame: the window of its picture an entity is drawn as.
 *
 * A cover is drawn in a portrait box, and WHICH PART of the picture fills that box is chosen, the
 * way a phone frames a profile picture: a zoom and a position. The server stores the window as four
 * fractions of the picture and cuts it when the cover is sent (`kernel/cover_frame.py`,
 * `kernel/covers.py`); this is the client's half, and it is only MATHS. The editor
 * (`CoverFramer.svelte`) draws what this says.
 *
 * ## The gestures are the picture editor's, not these
 *
 * The window is dragged, resized and nudged by the editor's own crop control
 * (`components/edit/CropStage.svelte`) and the editor's own arithmetic (`lib/edit/geometry.ts`),
 * which works on a `Box` in the units of a `Frame`. What lives HERE is the cover's side of that
 * handshake: the units the cover lends the control (`coverSpace`), the two translations between a
 * window and a box (`boxOf`, `windowOf`), and the one rule the editor has no reason to know: a
 * window no smaller than a quarter of the largest (`atLeast`). The sharing runs one way: the cover
 * uses the editor's geometry; the editor knows nothing about covers.
 *
 * ## Fractions of the picture, never pixels
 *
 * `x` and `y` are the window's top-left corner and `w` and `h` its size, each 0..1 of the picture's
 * own width and height. The same cover is served from stills of different sizes, and a fraction is
 * right for all of them. So a window in the SHAPE of the box is not `w === h * ratio`. It is that
 * in pixels, which is `w * aspect / h === ratio`, where `aspect` is the picture's width over its
 * height. Every function here that makes a window takes both for that reason.
 *
 * ## The window with no frame is the middle, at its largest
 *
 * No stored frame means the whole picture, and the box cuts the whole picture to fit from the
 * middle, so what is on screen is the largest window of the box's shape, centred. The editor
 * opens there, and a window left there is saved as NO frame (`isCentred`), so opening the editor
 * and pressing Save changes nothing, not even the address.
 */
import type { components } from '$lib/api/schema';
import type { Box, Frame as Space } from '$lib/edit/geometry';

/** The window, as the server sends and stores it. */
export type Frame = components['schemas']['CoverFrame'];

/**
 * The cover box's shape, width over height. Every cover Sift draws is a 3:4 portrait (the header,
 * `--entity-cover-width` by four thirds of it, and every card), so the window is held to it.
 */
export const COVER_RATIO = 3 / 4;

/**
 * What a cover write carries beside the file and the moment: the window, and (to reframe an
 * UPLOADED cover, the one case a write names an upload) the upload that is already the cover.
 * The server refuses any other (`kernel/covers.py upload_kept_by_put`).
 */
export interface CoverMore {
	frame?: Frame | null;
	uploadId?: string | null;
}

/**
 * The body of a cover write: the file, the moment, and whatever `more` carries. One place, so the
 * five stores that write a cover send one shape.
 */
export function coverBody(assetId: string | null, atMs: number | null, more?: CoverMore) {
	return {
		asset_id: more?.uploadId ? null : assetId,
		at_ms: more?.uploadId ? null : atMs,
		upload_id: more?.uploadId ?? null,
		frame: more?.frame ?? null
	};
}

/** How far in the editor lets a window go: a quarter of the largest window across. */
export const MAX_ZOOM = 4;

/** Places every fraction is rounded to: the server's `_FRAME_PLACES`, so the tokens agree. */
const PLACES = 4;
const SCALE = 10 ** PLACES;

/** Near enough to count as the same window: well under a pixel of any picture Sift serves. */
const SAME = 5e-4;

function round(value: number): number {
	return Math.round(value * SCALE) / SCALE;
}

function clamp(value: number, low: number, high: number): number {
	return Math.min(Math.max(value, low), high);
}

/**
 * The largest window of the box's shape the picture holds: its full height for a picture wider than
 * the box, its full width for one narrower.
 */
export function largest(aspect: number, ratio: number): { w: number; h: number } {
	if (!(aspect > 0) || !(ratio > 0)) return { w: 1, h: 1 };
	return aspect >= ratio ? { w: ratio / aspect, h: 1 } : { w: 1, h: aspect / ratio };
}

/**
 * The window at `zoom` (1 is the largest, `MAX_ZOOM` the closest) centred as near `cx`, `cy` as the
 * picture's edges allow. The one place a window is MADE, so every other function here agrees with it
 * about the shape, the bounds and the rounding.
 */
export function framed(aspect: number, ratio: number, zoom: number, cx: number, cy: number): Frame {
	const most = largest(aspect, ratio);
	const z = clamp(Number.isFinite(zoom) ? zoom : 1, 1, MAX_ZOOM);
	const w = most.w / z;
	const h = most.h / z;
	const x = clamp(cx - w / 2, 0, 1 - w);
	const y = clamp(cy - h / 2, 0, 1 - h);
	return { x: round(x), y: round(y), w: round(w), h: round(h) };
}

/** The window the box shows when there is no frame: the largest, in the middle. */
export function centred(aspect: number, ratio: number): Frame {
	return framed(aspect, ratio, 1, 0.5, 0.5);
}

/** How far in a window is: 1 at its largest. */
export function zoomOf(frame: Frame, aspect: number, ratio: number): number {
	const most = largest(aspect, ratio);
	return frame.w > 0 ? clamp(most.w / frame.w, 1, MAX_ZOOM) : 1;
}

/**
 * A stored frame as the editor opens on it: kept where it was, held to the box's shape.
 *
 * Null opens on the middle. A stored window already of the box's shape is kept EXACTLY: re-made
 * from its own centre and zoom it would come back a ten-thousandth off, and an editor opened and
 * saved untouched would then record a reframe nobody made. Only a window of another shape (one
 * saved against a still since rebuilt at another size) is re-made, as the nearest window of the
 * right shape rather than a stretched one.
 */
export function opening(frame: Frame | null | undefined, aspect: number, ratio: number): Frame {
	if (!frame) return centred(aspect, ratio);
	const shape = frame.h > 0 ? (frame.w * aspect) / frame.h : 0;
	const inside =
		frame.x >= 0 && frame.y >= 0 && frame.x + frame.w <= 1 + SAME && frame.y + frame.h <= 1 + SAME;
	if (inside && Math.abs(shape - ratio) <= ratio * 0.01) return { ...frame };
	return framed(
		aspect,
		ratio,
		zoomOf(frame, aspect, ratio),
		frame.x + frame.w / 2,
		frame.y + frame.h / 2
	);
}

/** Whether two windows are the same window, to well under a pixel. Null is no window. */
export function sameFrame(a: Frame | null | undefined, b: Frame | null | undefined): boolean {
	if (!a || !b) return !a && !b;
	return (
		Math.abs(a.x - b.x) < SAME &&
		Math.abs(a.y - b.y) < SAME &&
		Math.abs(a.w - b.w) < SAME &&
		Math.abs(a.h - b.h) < SAME
	);
}

/**
 * Whether this window is what the box shows with no frame at all, in which case it is saved as
 * none, so an unmoved editor writes nothing new and the cover keeps its address.
 */
export function isCentred(frame: Frame, aspect: number, ratio: number): boolean {
	return sameFrame(frame, centred(aspect, ratio));
}

/**
 * What a frame adds to its cover's address: `f` and the four numbers in ten-thousandths.
 *
 * The server's `CoverFrame.token`, written the same way from the same rounded numbers, so a moved
 * window moves the address and the browser cannot go on drawing the old one from its cache.
 */
export function frameToken(frame: Frame): string {
	return `f${[frame.x, frame.y, frame.w, frame.h].map((one) => Math.round(one * SCALE)).join('-')}`;
}

/**
 * The units a cover lends the crop control: the picture ten thousand tall and as wide as its shape
 * makes it.
 *
 * Ten thousand because a stored fraction has four places (`PLACES`), so a box rounded to whole
 * units (which the editor's arithmetic does on every drag) is rounded exactly as finely as the
 * window it becomes, and no finer. Square units, so a window of the box's shape in these units is
 * one in pixels too, and the editor's ratio lock holds the right shape.
 */
export function coverSpace(aspect: number): Space {
	const safe = aspect > 0 && Number.isFinite(aspect) ? aspect : 1;
	return { width: Math.max(1, Math.round(SCALE * safe)), height: SCALE };
}

/** A window as the crop control draws it: a box in `space`'s units. */
export function boxOf(frame: Frame, space: Space): Box {
	return {
		left: frame.x * space.width,
		top: frame.y * space.height,
		width: frame.w * space.width,
		height: frame.h * space.height
	};
}

/** A box the crop control left, as the window it stands for: fractions, rounded, inside. */
export function windowOf(box: Box, space: Space): Frame {
	if (!(space.width > 0) || !(space.height > 0)) return { x: 0, y: 0, w: 1, h: 1 };
	const w = clamp(box.width / space.width, 0, 1);
	const h = clamp(box.height / space.height, 0, 1);
	return {
		x: round(clamp(box.left / space.width, 0, 1 - w)),
		y: round(clamp(box.top / space.height, 0, 1 - h)),
		w: round(w),
		h: round(h)
	};
}

/**
 * A window held to its smallest: no closer in than `MAX_ZOOM`, keeping whichever edges `was` had.
 *
 * The crop control lets a rectangle shrink to a couple of units, and a cover that small is a blur
 * the server refuses anyway (`FRAME_SMALLEST`). The editor's arithmetic has no reason to know
 * that, so the floor is applied here, to what the control hands back. Grown back about the edges
 * that did NOT move (the corner opposite the one being dragged), so the window stops where it is
 * rather than jumping toward the pointer. A window already large enough is returned untouched.
 */
export function atLeast(next: Frame, was: Frame, aspect: number, ratio: number): Frame {
	const most = largest(aspect, ratio);
	const w = most.w / MAX_ZOOM;
	const h = most.h / MAX_ZOOM;
	if (next.w >= w - SAME && next.h >= h - SAME) return next;
	const rightHeld = Math.abs(next.x + next.w - (was.x + was.w)) < SAME;
	const bottomHeld = Math.abs(next.y + next.h - (was.y + was.h)) < SAME;
	const x = rightHeld ? next.x + next.w - w : next.x;
	const y = bottomHeld ? next.y + next.h - h : next.y;
	return {
		x: round(clamp(x, 0, 1 - w)),
		y: round(clamp(y, 0, 1 - h)),
		w: round(w),
		h: round(h)
	};
}
