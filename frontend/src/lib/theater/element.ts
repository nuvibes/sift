/* The values Sift decides and the video element enforces.
 *
 * Two sides with an opinion about the same behaviour, and only one of them should be having it. A
 * video element will repeat if it is told to, fetch as much as it likes before anybody presses
 * play, start at whatever volume it was built with and take over the screen on a phone. And none
 * of that is a decision anybody made. An unset default is behaviour nobody chose, and it moves when
 * the browser updates.
 *
 * So the split is: **Sift owns the value, the element enforces it.** The element is the only thing
 * that can seek, decode or open a socket, so those stay its. Everything below is Sift's, and every
 * one of them is written on every render rather than left out when it happens to match: a value
 * left unset is one nobody chose, and it is exactly the one that changes underneath the app.
 *
 * `OWNED` is the list, and a gate reads it: a name here that `settle` does not write, or a name
 * `settle` writes that is not here, fails the build. That is what stops a value being quietly
 * dropped back to the browser's answer.
 */

/** Everything about a cell's video element that Sift decides rather than the browser. */
export const OWNED = [
	'muted',
	'volume',
	'loop',
	'preload',
	'autoplay',
	'playsInline',
	'playbackRate'
] as const;

export interface Owned {
	/** Whether this cell's sound reaches anybody. Every cell starts silent. */
	muted: boolean;
	/** How loud, as the element's own 0-1 rather than the whole percent everything else speaks. */
	volume: number;
	/**
	 * How fast it plays, as a multiple of ordinary speed.
	 *
	 * On the list because an element built for the next file opens at ordinary speed whatever the
	 * cell asked for, so a rate written once, straight onto the element, lasts exactly as long as
	 * the file it was written on and comes back to normal with nothing saying it did.
	 */
	rate: number;
}

/**
 * Write Sift's answer for every value it owns onto the element.
 *
 * `loop` is off, always, and that is the load-bearing one. What happens at the end of a file is the
 * cell's decision (it is the only thing that knows whether "again" means this file or the next in
 * the run), and no browser loops seamlessly anyway: the end of the media seeks back to the start
 * and hitches every time round.
 *
 * `autoplay` is off for the same shape of reason. A cell is started by being told to, so that every
 * cell on a wall can begin inside one gesture; an element that starts itself would begin whenever
 * its source happened to attach, which on a wall of four is four different moments.
 */
export function settle(video: HTMLVideoElement, owned: Owned): void {
	video.muted = owned.muted;
	video.volume = Math.min(1, Math.max(0, owned.volume));
	video.loop = false;
	video.preload = 'none';
	video.autoplay = false;
	video.playsInline = true;
	/* Refused rather than clamped: a rate of zero or less is not a slower film, it is a request the
	   element answers by throwing, and a browser's own ceiling on how fast it will play is the
	   browser's business. */
	video.playbackRate = owned.rate > 0 ? owned.rate : 1;
}
