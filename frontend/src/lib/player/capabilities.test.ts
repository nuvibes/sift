/*
 * What the browser is asked, and how its answers are read.
 *
 * The stakes are asymmetric and the tests are shaped around that. Believing a browser can play
 * something it cannot is a black screen. Believing it cannot play something it can is one
 * unnecessary conversion: slower and lower quality, but it works. So every ambiguous answer has
 * to fall the second way, and that is what most of this file checks.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { capabilities, capabilitiesWithout, forget, playsHlsNatively } from './capabilities';

/** Stand in for the browser's media stack with one that answers however a test wants. */
function pretendBrowser(options: {
	canPlayType?: (type: string) => string;
	isTypeSupported?: ((type: string) => boolean) | null;
}) {
	const element = {
		canPlayType: options.canPlayType ?? (() => '')
	} as unknown as HTMLVideoElement;

	vi.spyOn(document, 'createElement').mockReturnValue(element as unknown as HTMLElement);

	if (options.isTypeSupported === null) {
		// A browser with no Media Source Extensions at all: an iPhone, in practice.
		vi.stubGlobal('MediaSource', undefined);
	} else {
		vi.stubGlobal('MediaSource', { isTypeSupported: options.isTypeSupported ?? (() => false) });
	}
}

afterEach(() => {
	vi.restoreAllMocks();
	vi.unstubAllGlobals();
	forget();
});

describe('what this browser can decode', () => {
	it('reports the codecs the media stack says it supports', () => {
		pretendBrowser({
			isTypeSupported: (type) => type.includes('avc1') || type.includes('av01')
		});

		const found = capabilities();

		expect(found.video_codecs).toContain('h264');
		expect(found.video_codecs).toContain('av1');
		expect(found.video_codecs).not.toContain('hevc');
	});

	it('finds AV1, which is the whole point of asking', () => {
		/*
		 * AV1 is simultaneously the most expensive codec for the server to convert and the one
		 * browsers support most widely. Missing it here does not break playback: it silently
		 * routes every AV1 file down the most expensive path there is, and nothing reports that.
		 */
		pretendBrowser({ isTypeSupported: (type) => type.includes('av01') });

		expect(capabilities().video_codecs).toEqual(['av1']);
	});

	it('accepts a codec under any of the spellings browsers disagree about', () => {
		// `hvc1` and `hev1` are the same codec packaged differently, and browsers admit to
		// different ones. A browser that says yes to either can play the file.
		pretendBrowser({ isTypeSupported: (type) => type.includes('hev1') });

		expect(capabilities().video_codecs).toContain('hevc');
	});

	it('asks the ten-bit question separately, because the browser answers it separately', () => {
		// A decoder that takes HEVC Main and not Main 10: on `video_codecs` and off the ten-bit
		// list, so a 10-bit file is converted rather than handed over to a black stage.
		pretendBrowser({ isTypeSupported: (type) => type.includes('hvc1.1.6') });
		expect(capabilities().video_codecs).toContain('hevc');
		expect(capabilities().video_codecs_10bit).toEqual([]);
		forget();
		pretendBrowser({
			isTypeSupported: (type) => type.includes('hvc1.1.6') || type.includes('hvc1.2.4')
		});
		expect(capabilities().video_codecs_10bit).toEqual(['hevc']);
	});

	it('can be reported without a codec the browser then failed on, at both depths', () => {
		pretendBrowser({
			// Eight-bit H.264 and both depths of HEVC; not High 10 H.264, which no browser plays.
			isTypeSupported: (type) =>
				type.includes('avc1.64') || type.includes('hvc1.1.6') || type.includes('hvc1.2.4')
		});
		const without = capabilitiesWithout('HEVC');
		expect(without.video_codecs).toEqual(['h264']);
		expect(without.video_codecs_10bit).toEqual([]);
		expect(capabilitiesWithout(null).video_codecs).toContain('hevc');
	});

	it('never lists the same codec twice', () => {
		pretendBrowser({ isTypeSupported: () => true });

		const found = capabilities().video_codecs;
		expect(new Set(found).size).toBe(found.length);
	});
});

describe('reading a hedged answer', () => {
	it('treats "probably" as yes', () => {
		pretendBrowser({
			isTypeSupported: null,
			canPlayType: (type) => (type.includes('avc1') ? 'probably' : '')
		});

		expect(capabilities().video_codecs).toContain('h264');
	});

	it('treats "maybe" as no', () => {
		/*
		 * `canPlayType` returns "maybe" when the browser has not really checked: it recognizes
		 * the container and has not looked at the codec. Believing it is a black screen; not
		 * believing it is one unnecessary conversion. The second is the one to be wrong in.
		 */
		pretendBrowser({ isTypeSupported: null, canPlayType: () => 'maybe' });

		expect(capabilities().video_codecs).toEqual([]);
	});

	it('survives a media stack that throws instead of answering', () => {
		// Some browsers throw on a malformed type string rather than returning false. One bad
		// probe must not take the whole detection down and leave the player with nothing.
		pretendBrowser({
			isTypeSupported: (type) => {
				if (type.includes('hvc1')) throw new Error('nope');
				return type.includes('avc1');
			}
		});

		expect(capabilities().video_codecs).toContain('h264');
	});

	it('survives canPlayType throwing too', () => {
		pretendBrowser({
			isTypeSupported: null,
			canPlayType: () => {
				throw new Error('nope');
			}
		});

		expect(capabilities().video_codecs).toEqual([]);
	});
});

describe('containers', () => {
	it('reports mp4 and mov together, because they are the same box', () => {
		pretendBrowser({ isTypeSupported: (type) => type.startsWith('video/mp4') });

		const found = capabilities().containers;
		expect(found).toContain('mp4');
		expect(found).toContain('mov');
	});

	it('detects mp4 on a browser with no Media Source Extensions', () => {
		/*
		 * What this test exists for. An iPhone has no MediaSource, so the box question falls to
		 * canPlayType, which answers "maybe" for a bare `video/mp4` and only "probably" for a
		 * real codec string. Probing with the codec is what keeps a plain H.264 mp4, the commonest
		 * file there is, on the direct-play path instead of the transcoder. Asking the bare MIME
		 * leaves containers empty here and sends every mp4 to transcode.
		 */
		pretendBrowser({
			isTypeSupported: null,
			canPlayType: (type) => (type.includes('avc1') ? 'probably' : 'maybe')
		});

		const found = capabilities().containers;
		expect(found).toContain('mp4');
		expect(found).toContain('mov');
	});

	it('does not report a box the browser can play nothing in', () => {
		// Plays H.264-in-mp4 and nothing else: mp4 and mov are in, webm is out.
		pretendBrowser({ isTypeSupported: (type) => type.includes('avc1') });

		expect(capabilities().containers).not.toContain('webm');
	});

	it('never reports Matroska, which no browser demuxes', () => {
		/*
		 * The remux tier exists precisely because of this. A browser that appears to claim mkv
		 * support is answering about a codec, not a container, and believing it would send an
		 * unplayable file straight to the video element.
		 */
		pretendBrowser({ isTypeSupported: () => true });

		expect(capabilities().containers).not.toContain('mkv');
	});
});

describe('caching the answer', () => {
	it('asks the browser once', () => {
		const probe = vi.fn(() => true);
		pretendBrowser({ isTypeSupported: probe });

		capabilities();
		const after = probe.mock.calls.length;
		capabilities();

		expect(probe.mock.calls.length).toBe(after);
	});
});

describe('native HLS', () => {
	it('is detected where the browser plays a playlist itself', () => {
		// Safari. This matters most on iPhone, where Media Source Extensions do not exist at all,
		// so hls.js cannot work and this is the only route to a segmented stream.
		pretendBrowser({
			isTypeSupported: null,
			canPlayType: (type) => (type.includes('mpegurl') ? 'probably' : '')
		});

		expect(playsHlsNatively()).toBe(true);
	});

	it('is not claimed where the browser says nothing', () => {
		pretendBrowser({ isTypeSupported: null, canPlayType: () => '' });

		expect(playsHlsNatively()).toBe(false);
	});

	it('is not claimed when asking throws', () => {
		pretendBrowser({
			isTypeSupported: null,
			canPlayType: () => {
				throw new Error('nope');
			}
		});

		expect(playsHlsNatively()).toBe(false);
	});
});
