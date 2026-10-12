import type { components } from '$lib/api/schema';
/*
 * Asking the browser what it can actually play, so the server transcodes only what it cannot: AV1
 * and HEVC play widely, and differ per device. The most valuable answer is AV1.
 */

/** `Required`: this always reports every list, where the server accepts a list left out. */
export type Capabilities = Required<components['schemas']['Capabilities']> & {
	/** Codecs the browser only said "maybe" to: planned as no, said in the plan's log line. */
	video_codecs_maybe: string[];
};

/* A "maybe" is believed for these once this window has played one: they need the device's
   decoder, which a codec string cannot tell apart from a guess. */
const PROVEN_BY_PLAYING = new Set(['hevc', 'av1']);
const PLAYED_KEY = 'sift.played-codecs';

/** Computed once, and again only when this window proves or disproves a codec (`played`). */
let cached: Capabilities | null = null;

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

function guesses(type: string, probe: HTMLVideoElement): boolean {
	try {
		return probe.canPlayType(type) === 'maybe';
	} catch {
		return false;
	}
}

function detect(probes: Array<[string, string[]]>, probe: HTMLVideoElement): string[] {
	return answers(probes, probe).yes;
}

function answers(
	probes: Array<[string, string[]]>,
	probe: HTMLVideoElement
): { yes: string[]; maybe: string[] } {
	const yes: string[] = [];
	const maybe: string[] = [];
	for (const [name, types] of probes) {
		if (types.some((type) => supports(type, probe))) yes.push(name);
		else if (types.some((type) => guesses(type, probe))) maybe.push(name);
	}
	return { yes, maybe };
}

function playedHere(): Set<string> {
	try {
		const kept: unknown = JSON.parse(localStorage.getItem(PLAYED_KEY) ?? '[]');
		return new Set(Array.isArray(kept) ? kept.filter((one) => typeof one === 'string') : []);
	} catch {
		return new Set();
	}
}

function keepPlayed(played: Set<string>): void {
	try {
		localStorage.setItem(PLAYED_KEY, JSON.stringify([...played]));
	} catch {
		// Not remembered: the next window asks with the browser's own answer.
	}
	cached = null;
}

/** This window played a file of this codec itself, so its "maybe" is a yes from now on. */
export function played(codec: string | null | undefined, depth: number | null | undefined): void {
	const name = codec?.toLowerCase() ?? '';
	if (!PROVEN_BY_PLAYING.has(name)) return;
	const known = playedHere();
	const tenBit = depth !== null && depth !== undefined && depth > 8;
	if (known.has(name) && (!tenBit || known.has(`${name}:10`))) return;
	known.add(name);
	if (tenBit) known.add(`${name}:10`);
	keepPlayed(known);
}

/** The element refused a file of this codec, so a remembered yes for it was wrong. */
export function refused(codec: string | null | undefined): void {
	const name = codec?.toLowerCase() ?? '';
	const known = playedHere();
	if (!known.has(name) && !known.has(`${name}:10`)) return;
	known.delete(name);
	known.delete(`${name}:10`);
	keepPlayed(known);
}

/** Yes, plus each "maybe" this window has proved by playing one. */
function believed(found: { yes: string[]; maybe: string[] }, played: Set<string>, suffix: string) {
	const proved = found.maybe.filter(
		(name) => PROVEN_BY_PLAYING.has(name) && played.has(`${name}${suffix}`)
	);
	return [...found.yes, ...proved];
}

export function capabilities(): Capabilities {
	if (cached) return cached;

	const probe = document.createElement('video');
	const known = playedHere();
	const video = answers(VIDEO_PROBES, probe);
	const videoCodecs = believed(video, known, '');

	cached = {
		video_codecs: videoCodecs,
		video_codecs_10bit: believed(answers(TEN_BIT_PROBES, probe), known, ':10'),
		audio_codecs: detect(AUDIO_PROBES, probe),
		containers: detect(CONTAINER_PROBES, probe),
		video_codecs_maybe: video.maybe.filter((name) => !videoCodecs.includes(name))
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

/**
 * Only where hls.js cannot run: an iPhone, which has no Media Source Extensions. A Chromium that
 * says "maybe" to a playlist refused conversions hls.js plays.
 */
export function playsHlsNatively(): boolean {
	if (typeof globalThis.MediaSource?.isTypeSupported === 'function') return false;
	const probe = document.createElement('video');
	try {
		return probe.canPlayType('application/vnd.apple.mpegurl') !== '';
	} catch {
		return false;
	}
}
