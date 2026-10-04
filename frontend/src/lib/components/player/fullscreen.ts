// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * Full screen through whatever the browser offers.
 *
 * The stage asks for itself, so the bar comes with the picture and stepping to the next file keeps
 * the screen filled. A phone that cannot fill the screen with an element (Safari on an iPhone only
 * lets a video do it, through the video's own `webkitEnterFullscreen`) gets the video's native
 * player instead: the picture fills the screen and the browser draws the controls. A photograph
 * or a GIF has no such path there, so the stage fills the window itself instead (`filling` in
 * `MediaStage`): the picture edge to edge with the viewer's own chrome out of the way, the one
 * full screen such a browser allows, so the button is a press that does something there too.
 */

import { aboutToChange } from './motion';

interface NativeVideo extends HTMLVideoElement {
	webkitEnterFullscreen?: () => void;
	webkitSupportsFullscreen?: boolean;
}

interface NativeDocument extends Document {
	webkitFullscreenEnabled?: boolean;
}

/** Whether the browser has said, in so many words, that an element may not fill the screen. */
function elementsRefused(): boolean {
	if (typeof document === 'undefined') return false;
	const doc = document as NativeDocument;
	if (doc.fullscreenEnabled === true) return false;
	return doc.fullscreenEnabled === false || doc.webkitFullscreenEnabled === false;
}

/** Whether this video can fill the screen by itself, the one path an iPhone has. */
function videoCanFill(video: NativeVideo | null): video is NativeVideo {
	return (
		typeof video?.webkitEnterFullscreen === 'function' && video.webkitSupportsFullscreen !== false
	);
}

/**
 * Fill the screen with the stage, or with its video where only a video may. Says whether it asked:
 * false is a browser with no full screen for this stage at all, and the stage fills the window.
 */
export function enterFullscreen(stage: HTMLElement): boolean {
	// Where every stage stands, for the movement from there into the screen (`motion.ts`).
	aboutToChange();
	if (typeof stage.requestFullscreen === 'function' && !elementsRefused()) {
		void stage.requestFullscreen();
		return true;
	}
	const video = stage.querySelector('video') as NativeVideo | null;
	if (!videoCanFill(video)) return false;
	video.webkitEnterFullscreen?.();
	return true;
}

/** How long after the browser's own player closes a pause is taken as the closing's, not a press. */
const EXIT_PAUSE_MS = 1000;

/**
 * Keep a video playing across the way out of an iPhone's own full screen player.
 *
 * Swiping down out of it (or pressing its Done) hands the picture back to the page PAUSED, which is
 * the browser's doing rather than anybody's press: nothing on the page paused it. A video that was
 * playing as it came out carries on. The closing's pause can land either side of the end event, so
 * one a moment before it or a moment after it is the closing's; a pause pressed inside the player
 * well before closing it is a press, and the video comes out paused.
 * Returns what takes the listeners off again.
 */
export function keepPlayingAcrossExit(video: HTMLVideoElement, play: () => void): () => void {
	let filled = false;
	let playing = false;
	let pausedAt = -Infinity;
	let closedAt = -Infinity;
	const began = () => {
		filled = true;
		playing = !video.paused;
	};
	const played = () => {
		if (filled) playing = true;
	};
	const paused = () => {
		if (filled) {
			pausedAt = Date.now();
			return;
		}
		if (playing && Date.now() - closedAt < EXIT_PAUSE_MS) play();
	};
	const ended = () => {
		filled = false;
		closedAt = Date.now();
		if (video.paused && closedAt - pausedAt >= EXIT_PAUSE_MS) playing = false;
		if (playing && video.paused) play();
	};
	video.addEventListener('webkitbeginfullscreen', began);
	video.addEventListener('webkitendfullscreen', ended);
	video.addEventListener('play', played);
	video.addEventListener('pause', paused);
	return () => {
		video.removeEventListener('webkitbeginfullscreen', began);
		video.removeEventListener('webkitendfullscreen', ended);
		video.removeEventListener('play', played);
		video.removeEventListener('pause', paused);
	};
}
