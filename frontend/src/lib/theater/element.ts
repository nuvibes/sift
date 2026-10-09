/* The values Sift decides and the video element enforces: an unset default is behaviour nobody
 * chose, and it moves when the browser updates. A gate holds `OWNED` and `settle` to each other. */

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
	/** On the list because an element built for the next file opens at ordinary speed. */
	rate: number;
}

/**
 * Write Sift's answer for every value it owns onto the element. `loop` is off because the end of
 * a file is the cell's decision; `autoplay` is off so a whole wall starts inside one gesture.
 */
export function settle(video: HTMLVideoElement, owned: Owned): void {
	video.muted = owned.muted;
	video.volume = Math.min(1, Math.max(0, owned.volume));
	video.loop = false;
	video.preload = 'none';
	video.autoplay = false;
	video.playsInline = true;
	// Refused rather than clamped: the element throws on a rate of zero or less.
	video.playbackRate = owned.rate > 0 ? owned.rate : 1;
}
