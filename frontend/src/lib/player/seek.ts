/*
 * One seek in flight per video, the latest place kept until it lands: a drag fires `input` every
 * frame, and each new `currentTime` abandons the seek before it, so nothing would paint. A seek
 * with nothing running goes straight through; a new file drops what was waiting.
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

export function seekTo(video: HTMLMediaElement, seconds: number): void {
	watch(video);
	if (video.seeking) {
		waiting.set(video, seconds);
		return;
	}
	waiting.delete(video);
	video.currentTime = seconds;
}

/** For a seek made some other way that must win. */
export function dropWaitingSeek(video: HTMLMediaElement): void {
	waiting.delete(video);
}
