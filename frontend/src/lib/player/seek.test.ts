/*
 * The scrub line's seeks: one in flight, the newest place kept, nothing carried to the next file.
 */

import { describe, expect, it } from 'vitest';

import { dropWaitingSeek, seekTo } from './seek';

/** A video as far as seeking goes: setting `currentTime` starts a seek that `land` finishes. */
function fakeVideo() {
	const target = new EventTarget();
	const sets: number[] = [];
	let seeking = false;
	let at = 0;
	// Accessors, not a copied object: `Object.assign` would copy the getters' values once.
	Object.defineProperties(target, {
		seeking: { get: () => seeking },
		currentTime: {
			get: () => at,
			set: (value: number) => {
				at = value;
				seeking = true;
				sets.push(value);
			}
		}
	});
	const video = target as unknown as HTMLMediaElement;
	const land = () => {
		seeking = false;
		target.dispatchEvent(new Event('seeked'));
	};
	return { video, sets, land, empty: () => target.dispatchEvent(new Event('emptied')) };
}

describe('seeking from the scrub line', () => {
	it('goes straight there when nothing is seeking', () => {
		const { video, sets } = fakeVideo();
		seekTo(video, 12);
		expect(sets).toEqual([12]);
	});

	it('holds only the newest place while a seek runs, and sends it when that seek lands', () => {
		const { video, sets, land } = fakeVideo();
		seekTo(video, 1);
		seekTo(video, 2);
		seekTo(video, 3);
		seekTo(video, 4);
		expect(sets).toEqual([1]);
		land();
		expect(sets).toEqual([1, 4]);
		land();
		expect(sets).toEqual([1, 4]);
		expect(video.currentTime).toBe(4);
	});

	it('never carries a waiting place to the next file', () => {
		const { video, sets, land, empty } = fakeVideo();
		seekTo(video, 1);
		seekTo(video, 9);
		empty();
		land();
		expect(sets).toEqual([1]);
	});

	it('lets a seek made another way win over a waiting one', () => {
		const { video, sets, land } = fakeVideo();
		seekTo(video, 1);
		seekTo(video, 9);
		dropWaitingSeek(video);
		land();
		expect(sets).toEqual([1]);
	});
});
