/* The maths of framing a cover: the window stays the box's shape, stays inside the picture, and
 * names itself on the address the same way the server does. */
import { describe, expect, it } from 'vitest';
import { coverUrl, wholeCoverUrl } from '$lib/entity/art';
import {
	COVER_RATIO,
	MAX_ZOOM,
	centred,
	coverBody,
	frameToken,
	framed,
	isCentred,
	largest,
	opening,
	sameFrame,
	zoomOf,
	atLeast,
	boxOf,
	coverSpace,
	windowOf,
	type Frame
} from '$lib/entity/cover-frame';
import { dragged, fitted, moved, shaped } from '$lib/edit/geometry';

/** A landscape still, 16:9, the commonest picture a cover is chosen from. */
const WIDE = 16 / 9;

/** The window's shape in PIXELS (what the box sees), which is the ratio it must keep. */
function shapeOf(frame: Frame, aspect: number): number {
	return (frame.w * aspect) / frame.h;
}

function inside(frame: Frame): boolean {
	return (
		frame.x >= 0 && frame.y >= 0 && frame.x + frame.w <= 1 + 1e-9 && frame.y + frame.h <= 1 + 1e-9
	);
}

describe('the window', () => {
	it('is at its largest the full height of a wide picture and the full width of a tall one', () => {
		expect(largest(WIDE, COVER_RATIO).h).toBe(1);
		expect(largest(9 / 16, COVER_RATIO).w).toBe(1);
	});

	it('opens in the middle at its largest, in the box shape', () => {
		const middle = centred(WIDE, COVER_RATIO);
		expect(middle.y).toBe(0);
		expect(middle.h).toBe(1);
		expect(middle.x + middle.w / 2).toBeCloseTo(0.5, 3);
		expect(shapeOf(middle, WIDE)).toBeCloseTo(COVER_RATIO, 3);
	});

	it('keeps the box shape at every zoom', () => {
		for (const zoom of [1, 1.5, 2, 3, MAX_ZOOM]) {
			const frame = framed(WIDE, COVER_RATIO, zoom, 0.3, 0.4);
			expect(shapeOf(frame, WIDE)).toBeCloseTo(COVER_RATIO, 2);
			expect(zoomOf(frame, WIDE, COVER_RATIO)).toBeCloseTo(zoom, 2);
		}
	});

	it('never zooms past the limit or out past the whole height', () => {
		expect(zoomOf(framed(WIDE, COVER_RATIO, 40, 0.5, 0.5), WIDE, COVER_RATIO)).toBeCloseTo(
			MAX_ZOOM
		);
		expect(framed(WIDE, COVER_RATIO, 0.2, 0.5, 0.5)).toEqual(centred(WIDE, COVER_RATIO));
	});
});

describe("the handshake with the picture editor's crop control", () => {
	/* The window is dragged by `edit/CropStage` and the editor's own arithmetic, on a box in the
	   units `coverSpace` lends it. */
	const space = coverSpace(WIDE);

	it("lends square units, ten thousand tall, so the editor's ratio lock is the box shape", () => {
		expect(space.height).toBe(10000);
		expect(space.width).toBe(Math.round(10000 * WIDE));
		// The editor's own "largest of this shape" is the cover's centred window, near enough.
		const box = fitted(space, COVER_RATIO);
		expect(sameFrame(windowOf(box, space), centred(WIDE, COVER_RATIO))).toBe(true);
	});

	it('reads a window as a box and back without moving it', () => {
		const stored = framed(WIDE, COVER_RATIO, 2, 0.3, 0.6);
		expect(windowOf(boxOf(stored, space), space)).toEqual(stored);
	});

	it("keeps the box shape through the editor's corner and edge drags", () => {
		const start = boxOf(framed(WIDE, COVER_RATIO, 1.5, 0.5, 0.5), space);
		for (const grip of ['se', 'nw', 'e', 'n'] as const) {
			const next = shaped(
				dragged(start, grip, { x: start.left + 900, y: start.top + 300 }, space),
				grip,
				space,
				COVER_RATIO
			);
			expect(shapeOf(windowOf(next, space), WIDE)).toBeCloseTo(COVER_RATIO, 3);
			expect(inside(windowOf(next, space))).toBe(true);
		}
	});

	it('moves by what the editor moved and stops at the edges', () => {
		const start = framed(WIDE, COVER_RATIO, 2, 0.5, 0.5);
		const far = windowOf(moved(boxOf(start, space), { x: 1e6, y: 1e6 }, space), space);
		expect(inside(far)).toBe(true);
		expect(far.x + far.w).toBeCloseTo(1, 3);
		expect(far.y + far.h).toBeCloseTo(1, 3);
	});

	it('holds a window no closer in than MAX_ZOOM, at the edges that did not move', () => {
		const was = centred(WIDE, COVER_RATIO);
		const tiny: Frame = { x: was.x, y: 0, w: 0.01, h: 0.01 * (WIDE / COVER_RATIO) };
		const held = atLeast(tiny, was, WIDE, COVER_RATIO);
		expect(zoomOf(held, WIDE, COVER_RATIO)).toBeCloseTo(MAX_ZOOM, 2);
		expect([held.x, held.y]).toEqual([was.x, 0]);
		// Dragged from the top-left corner, the bottom-right stays put.
		const fromCorner: Frame = { x: 0.5, y: 0.9, w: 0.01, h: 0.01 * (WIDE / COVER_RATIO) };
		const pinned = atLeast(
			{ ...fromCorner, x: was.x + was.w - fromCorner.w, y: 1 - fromCorner.h },
			was,
			WIDE,
			COVER_RATIO
		);
		expect(pinned.x + pinned.w).toBeCloseTo(was.x + was.w, 3);
		expect(pinned.y + pinned.h).toBeCloseTo(1, 3);
		// A window already large enough is untouched.
		expect(atLeast(was, was, WIDE, COVER_RATIO)).toBe(was);
	});
});

describe('what is saved', () => {
	it('is no frame when the window was left where it opened', () => {
		expect(isCentred(opening(null, WIDE, COVER_RATIO), WIDE, COVER_RATIO)).toBe(true);
		const middle = centred(WIDE, COVER_RATIO);
		expect(isCentred({ ...middle, x: middle.x + 0.1 }, WIDE, COVER_RATIO)).toBe(false);
	});

	it('opens a stored window exactly where it was', () => {
		// Exactly: re-made from its centre and zoom it came back a ten-thousandth off, so an editor
		// opened and saved untouched recorded a reframe nobody made.
		const stored = framed(WIDE, COVER_RATIO, 2, 0.3, 0.6);
		expect(opening(stored, WIDE, COVER_RATIO)).toEqual(stored);
	});

	it('re-makes a stored window of another shape into the box shape, where it was', () => {
		const square: Frame = { x: 0.2, y: 0.2, w: 0.3, h: 0.5 };
		const opened = opening(square, WIDE, COVER_RATIO);
		expect(shapeOf(opened, WIDE)).toBeCloseTo(COVER_RATIO, 2);
		expect(opened.x + opened.w / 2).toBeCloseTo(0.35, 2);
	});

	it('tells the same window from a moved one', () => {
		const stored = framed(WIDE, COVER_RATIO, 2, 0.3, 0.6);
		expect(sameFrame(stored, { ...stored, x: stored.x + 0.0001 })).toBe(true);
		expect(sameFrame(stored, { ...stored, x: stored.x + 0.01 })).toBe(false);
		expect(sameFrame(null, null)).toBe(true);
		expect(sameFrame(stored, null)).toBe(false);
	});

	it('names an upload only to reframe it, and then sends no file', () => {
		const frame = framed(WIDE, COVER_RATIO, 2, 0.3, 0.6);
		expect(coverBody('a1', 1500, { frame })).toEqual({
			asset_id: 'a1',
			at_ms: 1500,
			upload_id: null,
			frame
		});
		expect(coverBody('a1', 1500, { uploadId: 'u1', frame })).toEqual({
			asset_id: null,
			at_ms: null,
			upload_id: 'u1',
			frame
		});
	});
});

describe('the address', () => {
	const frame: Frame = { x: 0.125, y: 0, w: 0.5, h: 0.6667 };

	it('is the server token, digit for digit', () => {
		// `kernel/cover_frame.py CoverFrame.token` for the same four numbers.
		expect(frameToken(frame)).toBe('f1250-0-5000-6667');
	});

	it('moves when the window moves', () => {
		const before = coverUrl('/people/p1', 's1', { assetId: 'a1', atMs: 1500 });
		const after = coverUrl('/people/p1', 's1', { assetId: 'a1', atMs: 1500, frame });
		expect(after).not.toBe(before);
		expect(after).toContain(encodeURIComponent(`s1.a1.1500.${frameToken(frame)}`));
		expect(coverUrl('/people/p1', 's1', { uploadId: 'u1', frame })).toContain(
			encodeURIComponent(`s1.u1.${frameToken(frame)}`)
		);
	});

	it('asks for the whole picture without the window in its token', () => {
		const whole = wholeCoverUrl('/people/p1', 's1', { assetId: 'a1', atMs: 1500, frame });
		expect(whole).toBe(`${coverUrl('/people/p1', 's1', { assetId: 'a1', atMs: 1500 })}&whole=1`);
	});
});
