/*
 * Which of the three paths gets attached, and whether it is cleaned up.
 *
 * The teardown test is the one that matters most and it is easy to under-rate. On this design a
 * player that keeps running does not merely waste the browser's time: it keeps requesting
 * segments, and every segment request makes the server transcode. A leaked player is a leaked
 * ffmpeg workload, on someone's home server, for a video nobody is watching.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const destroy = vi.fn();
const loadSource = vi.fn();
const attachMedia = vi.fn();
const construct = vi.fn();
const startLoad = vi.fn();
const recoverMediaError = vi.fn();

/** Every error hls.js was subscribed to, so a test can deliver one. */
let handlers: Record<string, (event: string, data: unknown) => void> = {};

vi.mock('hls.js', () => {
	class FakeHls {
		constructor(config: unknown) {
			construct(config);
		}
		loadSource = loadSource;
		attachMedia = attachMedia;
		destroy = destroy;
		startLoad = startLoad;
		recoverMediaError = recoverMediaError;
		on(event: string, handler: (event: string, data: unknown) => void) {
			handlers[event] = handler;
		}
		static isSupported = () => true;
		static Events = { ERROR: 'hlsError' };
		static ErrorTypes = {
			NETWORK_ERROR: 'networkError',
			MEDIA_ERROR: 'mediaError',
			OTHER_ERROR: 'otherError'
		};
	}
	return { default: FakeHls };
});

const nativeHls = vi.fn(() => false);
vi.mock('./capabilities', () => {
	const reported = () => ({
		video_codecs: ['h264', 'hevc'],
		video_codecs_10bit: ['hevc'],
		audio_codecs: ['aac'],
		containers: ['mp4']
	});
	return {
		capabilities: reported,
		capabilitiesWithout: (codec: string | null | undefined) => {
			const all = reported();
			return {
				...all,
				video_codecs: all.video_codecs.filter((one) => one !== codec),
				video_codecs_10bit: all.video_codecs_10bit.filter((one) => one !== codec)
			};
		},
		playsHlsNatively: () => nativeHls()
	};
});

const post = vi.fn();
vi.mock('$lib/api/client', () => ({
	api: { post: (path: string, options: unknown) => post(path, options) }
}));

import {
	attach,
	changeQuality,
	directFailure,
	planForWithout,
	hlsIsSupported,
	planFor,
	startAt,
	type PlaybackPlan,
	type Quality
} from './playback';

function plan(overrides: Partial<PlaybackPlan> = {}): PlaybackPlan {
	return {
		route: 'transcode',
		reason: '',
		copy_kind: null,
		url: '/api/assets/abc/hls/index.m3u8',
		scale_height: null,
		projected_realtime: 2,
		streamable: true,
		duration_ms: 10_000,
		resume_ms: null,
		view_at_ms: 2000,
		qualities: [],
		...overrides
	};
}

function video(): HTMLVideoElement {
	return document.createElement('video');
}

beforeEach(() => {
	nativeHls.mockReturnValue(false);
});

afterEach(() => {
	vi.clearAllMocks();
	handlers = {};
});

function fail(type: string) {
	handlers['hlsError']?.('hlsError', { fatal: true, type });
}

describe('direct play', () => {
	it('points the element straight at the file and loads no player', () => {
		const element = video();

		attach(element, plan({ route: 'direct', url: '/api/assets/abc/stream' }));

		expect(element.src).toContain('/api/assets/abc/stream');
		expect(loadSource).not.toHaveBeenCalled();
	});

	it('lets go of the file when the player closes', () => {
		const element = video();

		attach(element, plan({ route: 'direct', url: '/api/assets/abc/stream' })).detach();

		expect(element.getAttribute('src')).toBeNull();
	});
});

describe('native HLS', () => {
	it('is preferred over the JavaScript player where the browser has it', () => {
		/*
		 * Checked before reaching for hls.js, and the order is not a preference. On iPhone there
		 * are no Media Source Extensions, so hls.js cannot work at all. Taking the other branch
		 * there is a player that never starts.
		 */
		nativeHls.mockReturnValue(true);
		const element = video();

		attach(element, plan());

		expect(element.src).toContain('index.m3u8');
		expect(loadSource).not.toHaveBeenCalled();
	});
});

describe('hls.js', () => {
	it('is used for a segmented stream where the browser has no native HLS', () => {
		const element = video();

		attach(element, plan());

		expect(loadSource).toHaveBeenCalledWith('/api/assets/abc/hls/index.m3u8');
		expect(attachMedia).toHaveBeenCalledWith(element);
	});

	it('is torn down when the player closes', () => {
		/*
		 * Without this the player keeps fetching segments for a video nobody is watching, and on
		 * this design every one of those requests starts a transcode.
		 */
		attach(video(), plan()).detach();

		expect(destroy).toHaveBeenCalledOnce();
	});

	it('reads ahead of the playhead, but a bounded distance', () => {
		/*
		 * Two failures, one on each side, and this guards the shape rather than the number.
		 *
		 * NO CEILING at all is the expensive one. The server makes each piece of video on demand,
		 * so an unbounded buffer converts a whole film for somebody who watched ten seconds of it:
		 * "only convert what is being watched" would stop being true.
		 *
		 * TOO SMALL a ceiling costs the thing people do most. At ten seconds the player is never
		 * more than a piece or two ahead, so every drag of the scrub bar lands on video that does
		 * not exist yet and has to wait for it to be made. Thirty is enough slack for a seek to
		 * feel immediate and still a couple of seconds of a graphics card's time if the clip is
		 * abandoned.
		 *
		 * The bounds are wide because the number between them is a tuning choice; what must not
		 * change without somebody meaning it is that there IS a ceiling and that it leaves room to
		 * seek into.
		 */
		attach(video(), plan());

		const config = construct.mock.calls[0][0] as {
			maxBufferLength: number;
			maxMaxBufferLength: number;
		};
		expect(config.maxBufferLength).toBeGreaterThanOrEqual(20);
		expect(config.maxBufferLength).toBeLessThanOrEqual(60);
		expect(config.maxMaxBufferLength).toBeGreaterThanOrEqual(config.maxBufferLength);
		expect(config.maxMaxBufferLength).toBeLessThanOrEqual(120);
	});

	it('sends the session with every segment request', () => {
		// The playlist and each segment are permission-scoped. Without credentials each one is a
		// 401 and the player shows nothing.
		attach(video(), plan());

		const config = construct.mock.calls[0][0] as {
			xhrSetup: (xhr: { withCredentials: boolean }) => void;
		};
		const xhr = { withCredentials: false };
		config.xhrSetup(xhr);

		expect(xhr.withCredentials).toBe(true);
	});

	it('reports whether it can run here at all', () => {
		expect(hlsIsSupported()).toBe(true);
	});

	it('retries a network failure rather than giving up on it', () => {
		/*
		 * The likely failure on the hardware this targets: the server is still transcoding the
		 * segment and the request timed out. hls.js does not retry a fatal error by itself, so
		 * without this the player simply stops with no picture and no message.
		 */
		attach(video(), plan());

		fail('networkError');

		expect(startLoad).toHaveBeenCalledOnce();
		expect(destroy).not.toHaveBeenCalled();
	});

	it('tries to recover a media failure before giving up', () => {
		attach(video(), plan());

		fail('mediaError');

		expect(recoverMediaError).toHaveBeenCalledOnce();
		expect(destroy).not.toHaveBeenCalled();
	});

	it('says so when it cannot recover, rather than stalling silently', () => {
		const said: string[] = [];
		attach(video(), plan(), (message) => said.push(message));

		fail('otherError');

		expect(destroy).toHaveBeenCalledOnce();
		expect(said).toHaveLength(1);
	});

	it('ignores a non-fatal error', () => {
		attach(video(), plan());

		handlers['hlsError']?.('hlsError', { fatal: false, type: 'networkError' });

		expect(startLoad).not.toHaveBeenCalled();
		expect(destroy).not.toHaveBeenCalled();
	});
});

describe('asking for a plan', () => {
	it('tells the server what this browser can decode, and gives the ask a deadline', () => {
		post.mockResolvedValue(plan());

		void planFor('abc');

		expect(post).toHaveBeenCalledWith('/assets/abc/playback', {
			body: {
				video_codecs: ['h264', 'hevc'],
				video_codecs_10bit: ['hevc'],
				audio_codecs: ['aac'],
				containers: ['mp4']
			},
			signal: expect.any(AbortSignal)
		});
	});

	it('can ask again without a codec the browser said it played and then did not', () => {
		post.mockResolvedValue(plan());

		void planForWithout('abc', 'hevc');

		expect(post).toHaveBeenCalledWith('/assets/abc/playback', {
			body: {
				video_codecs: ['h264'],
				video_codecs_10bit: [],
				audio_codecs: ['aac'],
				containers: ['mp4']
			},
			signal: expect.any(AbortSignal)
		});
	});
});

describe('a direct play that fails', () => {
	function brokenOn(element: HTMLVideoElement, code: number) {
		Object.defineProperty(element, 'error', { value: { code }, configurable: true });
	}

	it('is reported through the same handler a streaming failure is', () => {
		const element = video();
		const heard: string[] = [];

		attach(element, plan({ route: 'direct', url: '/api/assets/abc/stream' }), (message) =>
			heard.push(message)
		);
		brokenOn(element, 4);
		element.dispatchEvent(new Event('error'));

		expect(heard).toEqual(["Your browser said it could play this file and then couldn't."]);
	});

	it('is not heard after the player has let go of the file', () => {
		const element = video();
		const heard: string[] = [];

		attach(element, plan({ route: 'direct', url: '/api/assets/abc/stream' }), (message) =>
			heard.push(message)
		).detach();
		element.dispatchEvent(new Event('error'));

		expect(heard).toEqual([]);
	});

	it('says what the element said, in a sentence', () => {
		const element = video();
		// The element's own numbering: 1 aborted, 2 network, 3 decode, 4 source not supported.
		brokenOn(element, 2);
		expect(directFailure(element)).toContain('connection');
		brokenOn(element, 3);
		expect(directFailure(element)).toContain("couldn't");
		brokenOn(element, 1);
		expect(directFailure(element)).toContain('stopped');
	});
});

describe('where to start from', () => {
	it('goes to the place the server named', () => {
		expect(startAt(900_000, 3600)).toBe(900);
	});

	it('stays at the beginning when there is nowhere to go back to', () => {
		// Most of a library: never played, too short to bother with, or watched to the end. The
		// server decides which; null is how it says so.
		expect(startAt(null, 3600)).toBeNull();
		expect(startAt(undefined, 3600)).toBeNull();
		expect(startAt(0, 3600)).toBeNull();
	});

	it('will not seek a video whose length this browser does not know yet', () => {
		// A live stream reads Infinity and an element with no metadata reads NaN. Seeking against
		// either is undefined behaviour rather than a position.
		expect(startAt(5_000, 0)).toBeNull();
		expect(startAt(5_000, Number.NaN)).toBeNull();
		expect(startAt(5_000, Number.POSITIVE_INFINITY)).toBeNull();
	});

	it('will not land on the end of the video', () => {
		/*
		 * The two lengths come from different places (one measured at import, one read out of the
		 * container by this browser), and a disagreement of a frame is ordinary. Seeking to the end
		 * opens on a black frame with nothing left to play, which looks like a broken file.
		 */
		expect(startAt(3_600_000, 3600)).toBe(3599.75);
		expect(startAt(9_999_999, 3600)).toBe(3599.75);
	});

	it('leaves a video too short to hold a position alone', () => {
		// A quarter-second clip has no room between its start and the margin off its end. The
		// clamp must not turn that into a seek to zero, which is a pointless element rebuild.
		expect(startAt(100, 0.2)).toBeNull();
	});
});

describe('changing quality', () => {
	function rung(overrides: Partial<Quality> = {}): Quality {
		return {
			label: '720p',
			height: 720,
			url: '/api/assets/a/hls/index.m3u8?route=transcode&height=720',
			auto: false,
			smooth: true,
			detail: null,
			...overrides
		};
	}

	it('tears the old player down before starting the new one', () => {
		/*
		 * On this design a player left running keeps asking for segments, and every segment request
		 * makes the server encode one. Changing quality four times without this would leave four
		 * streams being produced for one person watching one video.
		 */
		const element = video();
		const first = attach(element, plan({ route: 'transcode' }));

		changeQuality(element, first, plan({ route: 'transcode' }), rung());

		expect(destroy).toHaveBeenCalledTimes(1);
		expect(loadSource).toHaveBeenLastCalledWith(rung().url);
	});

	it('keeps the place rather than starting again from the beginning', () => {
		/*
		 * The failure that makes a quality menu a control people learn not to touch. The new stream
		 * is a different address, so as far as the element is concerned it starts at zero, and
		 * somebody four minutes into something expects to still be four minutes into it.
		 */
		const element = video();
		Object.defineProperty(element, 'duration', { value: 600, configurable: true });
		element.currentTime = 240;
		const first = attach(element, plan({ route: 'transcode' }));

		changeQuality(element, first, plan({ route: 'transcode' }), rung());
		element.dispatchEvent(new Event('loadedmetadata'));

		expect(element.currentTime).toBe(240);
	});

	/* jsdom's <video> has no `play` or `pause` worth the name (`play` throws "Not implemented"),
	 * so both are stood in for, along with the one property the decision is actually made from. */
	function videoThatIs(playing: boolean) {
		const element = video();
		element.play = vi.fn(async () => undefined);
		element.pause = vi.fn();
		Object.defineProperty(element, 'paused', { value: !playing, configurable: true });
		return element;
	}

	/* THE AUTOPLAY ATTRIBUTE.
	 *
	 * Picking a smaller size on a video that is PAUSED must not start it playing. An `autoplay`
	 * attribute is not "play this when it opens": it is "play this every time a source is
	 * loaded", and a quality change loads a new source into the same element, after the code that
	 * pauses on the first load has already run.
	 *
	 * Both halves are asserted deliberately. `play` not being called is not enough: with
	 * `autoplay` back the element would start on its own without this function calling anything,
	 * and a test that only watched for a call would stay green through the whole fault.
	 */
	it('leaves a paused video paused', () => {
		const element = videoThatIs(false);
		const first = attach(element, plan({ route: 'transcode' }));

		changeQuality(element, first, plan({ route: 'transcode' }), rung());
		element.dispatchEvent(new Event('loadedmetadata'));

		expect(element.play).not.toHaveBeenCalled();
		expect(element.pause).toHaveBeenCalled();
	});

	it('leaves a playing video playing', () => {
		// Changing quality is not a request to stop, either. The other half of the same promise.
		const element = videoThatIs(true);
		const first = attach(element, plan({ route: 'transcode' }));

		changeQuality(element, first, plan({ route: 'transcode' }), rung());
		element.dispatchEvent(new Event('loadedmetadata'));

		expect(element.play).toHaveBeenCalled();
		expect(element.pause).not.toHaveBeenCalled();
	});

	it('does not seek past the end of a stream that is shorter than the position', () => {
		// The two lengths come from different places and disagree by a frame often enough. Seeking
		// to or past the end opens on a black frame with no obvious way back.
		const element = video();
		Object.defineProperty(element, 'duration', { value: 10, configurable: true });
		element.currentTime = 240;
		const first = attach(element, plan({ route: 'transcode' }));

		changeQuality(element, first, plan({ route: 'transcode' }), rung());
		// What a real element does when a new source is attached, and what jsdom does not.
		element.currentTime = 0;
		element.dispatchEvent(new Event('loadedmetadata'));

		expect(element.currentTime).toBe(0);
	});
});
