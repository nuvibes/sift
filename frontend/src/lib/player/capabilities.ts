import type { components } from '$lib/api/schema';
/*
 * Asking the browser what it can actually play, so the server transcodes only what it cannot: AV1
 * and HEVC play widely, and differ per device. The most valuable answer is AV1.
 */

/** `Required`: this always reports all three lists, where the server accepts a list left out. */
export type Capabilities = Required<components['schemas']['Capabilities']>;

/*
 * The browser's codec strings, translated here into the server's names; several per codec, as
 * browsers admit to different packagings.
 */
const VIDEO_PROBES: Array<[string, string[]]> = [
	['h264', ['video/mp4; codecs="avc1.640028"', 'video/mp4; codecs="avc1.42E01E"']],
	[
		'hevc',
		[
			'video/mp4; codecs="hvc1.1.6.L93.B0"',
			'video/mp4; codecs="hev1.1.6.L93.B0"',
			'video/mp4; codecs="hvc1"'
		]
	],
	['av1', ['video/mp4; codecs="av01.0.05M.08"', 'video/mp4; codecs="av01.0.00M.08"']],
	['vp9', ['video/webm; codecs="vp9"', 'video/mp4; codecs="vp09.00.10.08"']],
	['vp8', ['video/webm; codecs="vp8"']]
];

/* TEN-bit profiles, asked separately, or an 8-bit-only decoder would get a 10-bit file. */
const TEN_BIT_PROBES: Array<[string, string[]]> = [
	['h264', ['video/mp4; codecs="avc1.6E0028"']],
	['hevc', ['video/mp4; codecs="hvc1.2.4.L120.B0"', 'video/mp4; codecs="hev1.2.4.L120.B0"']],
	['av1', ['video/mp4; codecs="av01.0.05M.10"']],
	['vp9', ['video/webm; codecs="vp09.02.10.10"', 'video/mp4; codecs="vp09.02.10.10"']]
];
const AUDIO_PROBES: Array<[string, string[]]> = [
	['aac', ['audio/mp4; codecs="mp4a.40.2"']],
	['opus', ['audio/webm; codecs="opus"', 'audio/mp4; codecs="opus"']],
	['mp3', ['audio/mp4; codecs="mp4a.6B"', 'audio/mpeg']],
	['flac', ['audio/mp4; codecs="flac"', 'audio/flac']],
	['vorbis', ['audio/webm; codecs="vorbis"']]
];

/*
 * Containers, probed with real codec strings: a bare type answers no or "maybe", and a plain H.264
 * mp4 would be transcoded. Matroska cannot be probed: no browser demuxes it, hence remux.
 */
const MP4_CODECS = [
	'video/mp4; codecs="avc1.42E01E"',
	'video/mp4; codecs="av01.0.05M.08"',
	'video/mp4; codecs="hvc1.1.6.L93.B0"'
];
const CONTAINER_PROBES: Array<[string, string[]]> = [
	['mp4', MP4_CODECS],
	['mov', MP4_CODECS],
	[
		'webm',
		[
			'video/webm; codecs="vp8"',
			'video/webm; codecs="vp09.00.10.08"',
			'video/webm; codecs="av01.0.05M.08"'
		]
	]
];

/**
 * `isTypeSupported` first; from `canPlayType` only "probably" is a yes, since a wrong "maybe" is a
 * black screen.
 */
function supports(type: string, probe: HTMLVideoElement): boolean {
	const mediaSource = globalThis.MediaSource;
	if (mediaSource?.isTypeSupported) {
		try {
			if (mediaSource.isTypeSupported(type)) return true;
		} catch {
			// A malformed type throws in some browsers.
		}
	}
	try {
		return probe.canPlayType(type) === 'probably';
	} catch {
		return false;
	}
}

function detect(probes: Array<[string, string[]]>, probe: HTMLVideoElement): string[] {
	const found: string[] = [];
	for (const [name, types] of probes) {
		if (types.some((type) => supports(type, probe)) && !found.includes(name)) {
			found.push(name);
		}
	}
	return found;
}

/** Computed once: it cannot change while the page is open. */
let cached: Capabilities | null = null;

export function capabilities(): Capabilities {
	if (cached) return cached;

	const probe = document.createElement('video');

	cached = {
		video_codecs: detect(VIDEO_PROBES, probe),
		video_codecs_10bit: detect(TEN_BIT_PROBES, probe),
		audio_codecs: detect(AUDIO_PROBES, probe),
		containers: detect(CONTAINER_PROBES, probe)
	};
	return cached;
}

/**
 * Less one codec the browser claimed and then failed on, so the server picks a path it can take.
 */
export function capabilitiesWithout(codec: string | null | undefined): Capabilities {
	const all = capabilities();
	if (!codec) return all;
	const name = codec.toLowerCase();
	return {
		...all,
		video_codecs: all.video_codecs.filter((one) => one !== name),
		video_codecs_10bit: all.video_codecs_10bit.filter((one) => one !== name)
	};
}

export function forget(): void {
	cached = null;
}

/** Safari does, and on an iPhone it is the only way. */
export function playsHlsNatively(): boolean {
	const probe = document.createElement('video');
	try {
		return probe.canPlayType('application/vnd.apple.mpegurl') !== '';
	} catch {
		return false;
	}
}
