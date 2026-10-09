/* How a cover sits in its frame: the window of its picture an entity is drawn as. */
import type { components } from '$lib/api/schema';
import type { Box, Frame as Space } from '$lib/edit/geometry';

/** The window, as the server sends and stores it. */
export type Frame = components['schemas']['CoverFrame'];

/** The cover box's shape, width over height. Every cover Sift draws is a 3:4 portrait (the
 * header, `--entity-cover-width` by four thirds of it, and every card), so the window is held to
 * it. */
export const COVER_RATIO = 3 / 4;

/** What a cover write carries beside the file and the moment: the window, and (to reframe an
 * UPLOADED cover, the one case a write names an upload) the upload that is already the cover. */
export interface CoverMore {
	frame?: Frame | null;
	uploadId?: string | null;
}

/** The body of a cover write: the file, the moment, and whatever `more` carries. */
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

/** The largest window of the box's shape the picture holds: its full height for a picture wider
 * than the box, its full width for one narrower. */
export function largest(aspect: number, ratio: number): { w: number; h: number } {
	if (!(aspect > 0) || !(ratio > 0)) return { w: 1, h: 1 };
	return aspect >= ratio ? { w: ratio / aspect, h: 1 } : { w: 1, h: aspect / ratio };
}

/** The window at `zoom` (1 is the largest, `MAX_ZOOM` the closest) centred as near `cx`, `cy` as
 * the picture's edges allow. */
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

/** A stored frame as the editor opens on it: kept where it was, held to the box's shape. */
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

/** Whether this window is what the box shows with no frame at all, in which case it is saved as
 * none, so an unmoved editor writes nothing new and the cover keeps its address. */
export function isCentred(frame: Frame, aspect: number, ratio: number): boolean {
	return sameFrame(frame, centred(aspect, ratio));
}

/** What a frame adds to its cover's address: `f` and the four numbers in ten-thousandths. */
export function frameToken(frame: Frame): string {
	return `f${[frame.x, frame.y, frame.w, frame.h].map((one) => Math.round(one * SCALE)).join('-')}`;
}

/** The units a cover lends the crop control: the picture ten thousand tall and as wide as its
 * shape makes it. */
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

/** A window held to its smallest: no closer in than `MAX_ZOOM`, keeping whichever edges `was`
 * had. */
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
