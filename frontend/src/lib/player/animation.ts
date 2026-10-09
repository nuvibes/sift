/*
 * PLAYING A GIF OURSELVES, SO THAT IT CAN BE STOPPED: an `<img>` cannot pause, and drawing one onto
 * a canvas draws its FIRST frame, not the one on screen. `ImageDecoder` needs a secure context;
 * elsewhere the caller falls back to the plain `<img>`.
 */

/** The size is set only when it changes: setting it reallocates the backing store. */
function drawFrame(canvas: HTMLCanvasElement, image: VideoFrame): void {
	if (canvas.width !== image.displayWidth) canvas.width = image.displayWidth;
	if (canvas.height !== image.displayHeight) canvas.height = image.displayHeight;
	const context = canvas.getContext('2d');
	context?.clearRect(0, 0, canvas.width, canvas.height);
	context?.drawImage(image, 0, 0);
}

export function canDriveAnimations(): boolean {
	return typeof globalThis.ImageDecoder !== 'undefined';
}

/** Said where a GIF cannot be held still; `here`, because it is true only on this address. */

/** GIF's own default. */
const UNSTATED_MS = 100;

export interface Animation {
	play(): void;
	pause(): void;
	close(): void;
}

/** Null where this browser cannot decode one; failures go to `onfail`. `held` starts it stopped. */
export async function driveAnimation(
	url: string,
	canvas: HTMLCanvasElement,
	options: {
		held?: boolean;
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

	async function step(): Promise<void> {
		if (closed || stopped || decoder === null) return;
		const track = decoder.tracks.selectedTrack;
		const count = track?.frameCount ?? 1;
		try {
			const { image } = await decoder.decode({ frameIndex: at % Math.max(1, count) });
			// The cell may have moved on during the await; a VideoFrame holds pixels until closed.
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
			// A frame that will not decode ends the GIF.
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
		// Drawn even while held, or a cell opening paused shows nothing.
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
