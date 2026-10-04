/*
 * THE FALLBACK, which is the half of this that most browsers take.
 *
 * `ImageDecoder` is a secure-context feature: the desktop app loads `http://127.0.0.1:5171` and has
 * it, and a browser reaching Sift over plain http on a LAN address does not. So the contract that
 * matters here is the refusal: it has to be a clean "no" that the caller can draw an ordinary
 * `<img>` against, not a throw and not a half-started GIF.
 *
 * The playing half is stood in for below, because jsdom has no decoder and no `VideoFrame`. What
 * that cannot check is whether a real GIF actually stops where it is; that needs a real browser.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { canDriveAnimations, driveAnimation } from './animation';

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('a browser that cannot decode a GIF', () => {
	it('says so', () => {
		expect(canDriveAnimations()).toBe(false);
	});

	it('refuses rather than throwing, and fetches nothing', async () => {
		const asked = vi.fn();
		vi.stubGlobal('fetch', asked);
		const canvas = document.createElement('canvas');

		const made = await driveAnimation('/api/assets/a/stream', canvas);

		expect(made, 'a caller cannot tell "no decoder" from "no GIF yet"').toBeNull();
		expect(
			asked,
			'a browser that cannot draw it should not fetch it either'
		).not.toHaveBeenCalled();
	});
});

describe('a browser that can', () => {
	/* Stood in for. What is under test is this file's own sequence: read the header, draw the first
	   frame even while held, say how big it is, and let go of everything afterwards. */
	function decoderThatHolds(frames: number) {
		const closed: string[] = [];
		class FakeDecoder {
			tracks = {
				ready: Promise.resolve(),
				selectedTrack: { frameCount: frames, animated: true, repetitionCount: Infinity }
			};
			async decode({ frameIndex }: { frameIndex: number }) {
				return {
					image: {
						displayWidth: 320,
						displayHeight: 240,
						duration: 40_000,
						close: () => closed.push(`frame ${frameIndex}`)
					}
				};
			}
			close() {
				closed.push('decoder');
			}
		}
		vi.stubGlobal('ImageDecoder', FakeDecoder);
		vi.stubGlobal('fetch', async () => ({
			ok: true,
			headers: { get: () => 'image/gif' },
			arrayBuffer: async () => new ArrayBuffer(8)
		}));
		return closed;
	}

	it('draws the first frame and says how big it is, even while held', async () => {
		const closed = decoderThatHolds(6);
		const canvas = document.createElement('canvas');
		const size = vi.fn();

		const made = await driveAnimation('/api/assets/a/stream', canvas, { held: true, onsize: size });

		expect(made).not.toBeNull();
		expect(size, 'a cell opening paused would have no shape and no picture').toHaveBeenCalledWith({
			width: 320,
			height: 240
		});
		expect(canvas.width).toBe(320);
		expect(closed, 'a decoded frame holds pixels until it is closed').toContain('frame 0');
		made?.close();
	});

	/* Every frame is closed, and so is the decoder. A `VideoFrame` holds decoded pixels until it is
	   closed, and a wall of nine GIFs leaks nine at a time. */
	it('lets go of the decoder when the cell moves on', async () => {
		const closed = decoderThatHolds(6);
		const made = await driveAnimation('/api/assets/a/stream', document.createElement('canvas'), {
			held: true
		});

		made?.close();

		expect(closed).toContain('decoder');
	});

	/* Setting a canvas's size throws its backing store away and allocates another, which a wall of
	   GIFs would do for every frame of every cell; the size is set when it changes and not otherwise. */
	it('keeps the canvas it has while the frames stay one size', async () => {
		const closed = decoderThatHolds(6);
		const canvas = document.createElement('canvas');
		let width = canvas.width;
		let sized = 0;
		Object.defineProperty(canvas, 'width', {
			get: () => width,
			set: (next: number) => {
				sized += 1;
				width = next;
			}
		});

		const made = await driveAnimation('/api/assets/a/stream', canvas);
		await new Promise((done) => setTimeout(done, 150));
		made?.close();

		expect(closed, 'the GIF never moved past its first frame').toContain('frame 2');
		expect(sized, 'the canvas was sized again for a frame of the same size').toBe(1);
	});

	it('reports a file it cannot fetch, rather than sitting on a blank canvas', async () => {
		decoderThatHolds(6);
		vi.stubGlobal('fetch', async () => ({ ok: false, status: 404 }));
		const failed = vi.fn();

		await driveAnimation('/api/assets/a/stream', document.createElement('canvas'), {
			onfail: failed
		});

		expect(failed).toHaveBeenCalled();
	});
});
