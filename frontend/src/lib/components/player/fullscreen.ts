// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * Full screen through whatever the browser offers: the stage; an iPhone's video through its own
 * player; and a picture there fills the window instead (`filling` in `MediaStage`).
 */

import { aboutToChange } from './motion';

interface NativeVideo extends HTMLVideoElement {
	webkitEnterFullscreen?: () => void;
	webkitSupportsFullscreen?: boolean;
}

interface NativeDocument extends Document {
	webkitFullscreenEnabled?: boolean;
}

function elementsRefused(): boolean {
	if (typeof document === 'undefined') return false;
	const doc = document as NativeDocument;
	if (doc.fullscreenEnabled === true) return false;
	return doc.fullscreenEnabled === false || doc.webkitFullscreenEnabled === false;
}

function videoCanFill(video: NativeVideo | null): video is NativeVideo {
	return (
		typeof video?.webkitEnterFullscreen === 'function' && video.webkitSupportsFullscreen !== false
	);
}

/** False where nothing may fill the screen, and the stage fills the window. */
export function enterFullscreen(stage: HTMLElement): boolean {
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

const EXIT_PAUSE_MS = 1000;

/**
 * Keeps a video playing out of an iPhone's own player, which hands it back PAUSED; a pause either
 * side of the closing is the closing's. Returns the remover.
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
