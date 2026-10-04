/*
 * Full screen through what the browser offers: the stage where an element may fill the screen,
 * the video's own player where only a video may (an iPhone).
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { enterFullscreen, keepPlayingAcrossExit } from './fullscreen';

function refuseElements(refused: boolean) {
	Object.defineProperty(document, 'fullscreenEnabled', { configurable: true, value: !refused });
}

afterEach(() => {
	Object.defineProperty(document, 'fullscreenEnabled', { configurable: true, value: undefined });
	delete (HTMLVideoElement.prototype as { webkitEnterFullscreen?: unknown }).webkitEnterFullscreen;
});

function stage() {
	const box = document.createElement('div');
	box.append(document.createElement('video'));
	return box;
}

describe('filling the screen', () => {
	it('asks for the stage where elements may fill it', () => {
		refuseElements(false);
		const box = stage();
		box.requestFullscreen = vi.fn(async () => {});

		expect(enterFullscreen(box)).toBe(true);
		expect(box.requestFullscreen).toHaveBeenCalledOnce();
	});

	it('hands the video to the browser where only a video may', () => {
		refuseElements(true);
		const box = stage();
		box.requestFullscreen = vi.fn(async () => {});
		const video = box.querySelector('video') as HTMLVideoElement & {
			webkitEnterFullscreen?: () => void;
		};
		video.webkitEnterFullscreen = vi.fn();

		expect(enterFullscreen(box)).toBe(true);
		expect(box.requestFullscreen).not.toHaveBeenCalled();
		expect(video.webkitEnterFullscreen).toHaveBeenCalledOnce();
	});

	it('says it asked nothing for a picture where neither may, so the stage fills the window', () => {
		refuseElements(true);
		const box = document.createElement('div');
		box.append(document.createElement('img'));
		box.requestFullscreen = vi.fn(async () => {});

		expect(enterFullscreen(box)).toBe(false);
		expect(box.requestFullscreen).not.toHaveBeenCalled();
	});
});

describe('leaving an iPhone full screen player', () => {
	/* A video whose paused state the test says, since jsdom plays nothing. */
	function clip(paused: boolean) {
		const video = document.createElement('video');
		let state = paused;
		Object.defineProperty(video, 'paused', { configurable: true, get: () => state });
		const fire = (name: string, now?: boolean) => {
			if (now !== undefined) state = now;
			video.dispatchEvent(new Event(name));
		};
		return { video, fire };
	}

	it('carries on playing when the swipe down hands it back paused, the pause before the end or after', () => {
		const play = vi.fn();
		for (const pauseFirst of [true, false]) {
			play.mockClear();
			const { video, fire } = clip(false);
			const off = keepPlayingAcrossExit(video, play);
			fire('webkitbeginfullscreen');
			if (pauseFirst) fire('pause', true);
			fire('webkitendfullscreen');
			if (!pauseFirst) fire('pause', true);
			expect(play, pauseFirst ? 'pause, then the end' : 'the end, then pause').toHaveBeenCalled();
			off();
		}
	});

	it('comes out paused when it was paused inside, and was not playing when it went in', () => {
		vi.useFakeTimers();
		const play = vi.fn();
		const { video, fire } = clip(false);
		const off = keepPlayingAcrossExit(video, play);
		fire('webkitbeginfullscreen');
		fire('pause', true);
		vi.advanceTimersByTime(5000);
		fire('webkitendfullscreen');
		expect(play).not.toHaveBeenCalled();
		off();

		const still = clip(true);
		const offToo = keepPlayingAcrossExit(still.video, play);
		still.fire('webkitbeginfullscreen');
		still.fire('webkitendfullscreen');
		expect(play).not.toHaveBeenCalled();
		offToo();
		vi.useRealTimers();
	});
});
