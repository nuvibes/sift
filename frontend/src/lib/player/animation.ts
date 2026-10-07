/*
 * PLAYING A GIF OURSELVES, SO THAT IT CAN BE STOPPED.
 *
 * ## Why this exists at all
 *
 * A GIF is drawn in an `<img>`, because no browser demuxes GIF through the media stack: a
 * `<video>` pointed at a perfectly good one shows a blank frame for ever and reports nothing wrong.
 * An `<img>` animates it without being asked, and that is the whole of the interface: it cannot be
 * paused, it cannot say where it is, and it cannot be sent anywhere.
 *
 * On a wall that is a real fault rather than a curiosity: "Stop everything" would stop every cell
 * but the one holding a GIF.
 *
 * ## The thing that does NOT work, so it is not tried again
 *
 * **`canvas.drawImage(animatedImg)` draws the FIRST frame, not the frame on screen.** In Chromium,
 * the same `<img>` drawn twice a second and a half apart while it is demonstrably animating
 * produces byte-identical PNGs. So the obvious freeze (copy what is on screen onto a canvas and
 * show that) silently snaps the picture back to its beginning.
 *
 * ## So the frames are decoded here, and the GIF is ours
 *
 * `ImageDecoder` hands back one frame at a time with its own duration. Drawing them on a timer is
 * playing it; not drawing the next one is the pause, and the canvas keeps the frame it is on,
 * which is what a pause means and is the one thing an `<img>` cannot do.
 *
 * ## It is not available everywhere, and the fallback is the plain `<img>`
 *
 * WebCodecs is a **secure-context** feature. The desktop app loads `http://127.0.0.1:5171`, which
 * is a secure context, so the shipped application has it. A browser on another machine reaching
 * Sift over plain http does NOT, and there is nothing this file can do about that, so it says so,
 * the caller draws the ordinary `<img>` instead, and a GIF on that path goes on moving while
 * the wall is held. Degrading to a GIF that moves is honest; pretending to freeze is not.
 */

/**
 * Put one decoded frame on the canvas.
 *
 * The size is set only when it changes: setting it at all throws the canvas's backing store away
 * and allocates another, which a wall of GIFs would otherwise do for every frame of every cell. A
 * decoded frame is the whole picture, so clearing is all that is needed between two of one size.
 */
function drawFrame(canvas: HTMLCanvasElement, image: VideoFrame): void {
	if (canvas.width !== image.displayWidth) canvas.width = image.displayWidth;
	if (canvas.height !== image.displayHeight) canvas.height = image.displayHeight;
	const context = canvas.getContext('2d');
	context?.clearRect(0, 0, canvas.width, canvas.height);
	context?.drawImage(image, 0, 0);
}

/** Whether this browser can decode a GIF frame by frame. See the note above. */
export function canDriveAnimations(): boolean {
	return typeof globalThis.ImageDecoder !== 'undefined';
}

/**
 * What is said where a GIF cannot be held still. One sentence, in the two places that say
 * it.
 *
 * A Theater cell keeps its hold control (the hold is the wall's and is right for every clip on it)
 * and says this under the transport. The ordinary player has one file and withholds the control
 * instead, and withholding it silently would leave nowhere to learn why the Play button is absent.
 * So it says this, in the same words, under the same bar.
 *
 * Here rather than in either component because this file is what KNOWS the fact:
 * `canDriveAnimations` above is the measurement, and a sentence describing it belongs beside it.
 * Written out twice it would be two sentences within a release, which is how one surface ends up
 * explaining a thing differently from the one beside it.
 *
 * ## It says the outcome, not the mechanism
 *
 * The secure-context and frame-decoding reason is true and answers a question nobody in front of it
 * is asking: somebody who has just noticed that a picture will not stop wants the plain outcome.
 *
 * `here` is the one word that is load-bearing. This sentence is drawn ONLY where the decoder is
 * missing, which is a plain-http address reached from another machine; in the shipped application a
 * GIF is held like anything else. Without that word the sentence would be a claim about Sift rather
 * than about this screen, and would read as wrong to the same person on the same file tomorrow.
 */

/** How long a frame is held when the file does not say. A tenth of a second is GIF's own default. */
const UNSTATED_MS = 100;

/** What a running GIF offers whoever started it. */
export interface Animation {
	/** Draw frames again from where it stopped. */
	play(): void;
	/** Stop on the frame that is showing. The canvas keeps it. */
	pause(): void;
	/** Let go of the decoder and every frame. Called when the cell moves on. */
	close(): void;
}

/**
 * Decode a GIF and draw it into a canvas.
 *
 * Returns null when this browser cannot decode one, which is the caller's cue to draw an ordinary
 * `<img>` instead. Throws nothing: a file that cannot be fetched or decoded is reported through
 * `onfail`, because a cell has one answer for every way a file can fail to play.
 *
 * `held` says whether it should start stopped, which matters on a wall: it opens paused, and a
 * GIF that ran for one frame before being told would be a flicker on every cell at the same time.
 */
export async function driveAnimation(
	url: string,
	canvas: HTMLCanvasElement,
	options: {
		held?: boolean;
		/** The file's own size, once the decoder has read the header. */
		onsize?: (size: { width: number; height: number }) => void;
		onfail?: () => void;
	} = {}
): Promise<Animation | null> {
	if (!canDriveAnimations()) return null;

	let stopped = options.held === true;
	let closed = false;
	let at = 0;
	let waiting: ReturnType<typeof setTimeout> | null = null;
	let decoder: ImageDecoder | null = null;

	const stopWaiting = () => {
		if (waiting !== null) clearTimeout(waiting);
		waiting = null;
	};

	const control: Animation = {
		play() {
			if (!stopped || closed) return;
			stopped = false;
			void step();
		},
		pause() {
			stopped = true;
			stopWaiting();
		},
		close() {
			closed = true;
			stopWaiting();
			decoder?.close();
			decoder = null;
		}
	};

	/** Draw the frame we are on, then line the next one up. */
	async function step(): Promise<void> {
		if (closed || stopped || decoder === null) return;
		const track = decoder.tracks.selectedTrack;
		const count = track?.frameCount ?? 1;
		try {
			const { image } = await decoder.decode({ frameIndex: at % Math.max(1, count) });
			// The await above is a gap in which the cell may have moved on. Closing a frame that is
			// never drawn is not optional: a VideoFrame holds decoded pixels until it is closed.
			if (closed) {
				image.close();
				return;
			}
			drawFrame(canvas, image);
			const holds = image.duration === null ? UNSTATED_MS : image.duration / 1000;
			image.close();
			at += 1;
			if (stopped) return;
			waiting = setTimeout(() => void step(), Math.max(20, holds));
		} catch {
			// A frame that will not decode ends the GIF rather than looping on it. The caller
			// has already been given a size, so this is a file that started and stopped: the cell's
			// own failure handler is the right place for it.
			options.onfail?.();
			control.close();
		}
	}

	try {
		const answer = await fetch(url);
		if (!answer.ok) throw new Error(`${answer.status}`);
		const type = answer.headers.get('content-type') ?? 'image/gif';
		const data = await answer.arrayBuffer();
		if (closed) return control;
		decoder = new ImageDecoder({ data, type });
		await decoder.tracks.ready;
		if (closed) {
			decoder.close();
			decoder = null;
			return control;
		}
		// The first frame is drawn even while held, or a cell that opens paused shows nothing at all.
		const { image } = await decoder.decode({ frameIndex: 0 });
		drawFrame(canvas, image);
		options.onsize?.({ width: image.displayWidth, height: image.displayHeight });
		image.close();
		at = 1;
		if (!stopped) void step();
	} catch {
		options.onfail?.();
		control.close();
	}

	return control;
}
