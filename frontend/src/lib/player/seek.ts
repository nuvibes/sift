/*
 * One seek in flight per video, and the latest place asked for kept until it lands.
 *
 * ## Why a drag on the scrub line would freeze the picture
 *
 * A range input fires `input` at the screen's refresh rate while a finger or a mouse drags it,
 * and setting `currentTime` on every one of those abandons the seek before it:
 * the browser drops the bytes it was fetching and the frames it was decoding, and starts again
 * from the keyframe before the new place. A seek costs the fetch plus the decode from that
 * keyframe forward, and most files keep a keyframe only every two to eleven seconds, so a seek
 * is tens of milliseconds on a desktop and more on a phone. A new one every 16 ms means almost
 * none of them finish: a drag paints next to nothing, and the picture only catches up once the
 * finger stops.
 *
 * Holding the newest place while a seek is running, and sending it when that seek lands, lets
 * every seek finish and paint, and the playhead still ends exactly where the drag let go.
 *
 * ## What it leaves alone
 *
 * A seek asked for while nothing is seeking goes straight through, so a click, a key and a
 * resume are exactly what they were. A new file drops whatever was waiting (`emptied`), so a
 * place asked of the last file is never applied to the next one.
 */

const waiting = new WeakMap<HTMLMediaElement, number>();
const watched = new WeakSet<HTMLMediaElement>();

function land(video: HTMLMediaElement): void {
	const next = waiting.get(video);
	waiting.delete(video);
	if (next !== undefined) video.currentTime = next;
}

function watch(video: HTMLMediaElement): void {
	if (watched.has(video)) return;
	watched.add(video);
	video.addEventListener('seeked', () => land(video));
	video.addEventListener('emptied', () => waiting.delete(video));
}

/** Go to `seconds`, now if nothing is seeking, or as soon as the running seek lands. */
export function seekTo(video: HTMLMediaElement, seconds: number): void {
	watch(video);
	if (video.seeking) {
		waiting.set(video, seconds);
		return;
	}
	waiting.delete(video);
	video.currentTime = seconds;
}

/** Forget a place still waiting, for a seek made some other way that must win over it. */
export function dropWaitingSeek(video: HTMLMediaElement): void {
	waiting.delete(video);
}
