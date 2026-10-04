import type { components } from '$lib/api/schema';
/*
 * Asking the browser what it can actually play.
 *
 * This is the highest-value thing in the player, and it is worth being clear about why. The naive
 * design transcodes anything that is not H.264, on the theory that browsers only reliably play
 * H.264. AV1 decode is effectively universal in Chrome, Edge and Firefox, and HEVC plays in Chrome
 * 107+ wherever the machine has a hardware decoder, so the naive design spends enormous amounts
 * of CPU converting files the browser would have played untouched, and loses quality doing it.
 *
 * The catch is that there is no single answer to encode toward. HEVC is universal on Safari and
 * absent from most Firefox builds; AV1 is universal on Chrome and limited to recent Apple silicon.
 * An iPhone 15 Pro and an iPhone 14 disagree. So the browser is asked, per device, and the server
 * decides from what it reported.
 *
 * The most important single answer is AV1: it is simultaneously the most expensive codec for the
 * server to convert and the one browsers support most widely. Every AV1 file this moves off the
 * transcode path costs nothing, loses nothing and starts instantly.
 */

/** What this browser told us it can handle, in the server's vocabulary. */
/* `Required`, and that is the difference between the two directions. The server accepts a report
   with a list left out (a browser that answered nothing about video still has an answer), while
   this function always produces all three, empty where there is nothing to say. */
export type Capabilities = Required<components['schemas']['Capabilities']>;

/*
 * The strings the browser is asked about, and what each one means in the vocabulary the server
 * stores.
 *
 * The translation happens here rather than on the server deliberately. These strings are long,
 * full of exceptions, and are the browser's own language; keeping them next to the browser means
 * the server compares plain codec names that match the columns it reads. A translation layer on
 * the server would be a second vocabulary to keep in step with this one.
 *
 * Several candidates per codec because the profile string matters: `hvc1` and `hev1` are the same
 * codec in different packagings and browsers disagree about which they admit to, and a browser
 * that says no to one and yes to the other can play the file either way.
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

/*
 * The same codecs at TEN bits a sample. A browser answers these as separate questions (HEVC
 * Main 10, AV1 high bit depth, VP9 profile 2), and a decoder that takes only the eight-bit
 * profile would be handed a 10-bit file as if it could play it: a black stage and no sentence. H.264
 * High 10 is asked about for completeness; no browser Sift targets answers yes to it.
 */
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
 * Containers.
 *
 * A box is probed with real codec strings, not a bare `video/mp4`. The bare form is the wrong
 * question: `isTypeSupported` wants codecs and answers no without them, and `canPlayType` answers
 * "maybe" for a naked MIME, so a plain H.264 mp4, the commonest file there is, would read as
 * unplayable and be sent to transcode, worst of all on an iPhone where there is no MediaSource to
 * fall back on. A box is playable when the browser can play something in it, so each one is asked
 * with the codecs that actually ship in it and counts as supported if any single probe is a yes.
 *
 * `mov` rides with `mp4` because a QuickTime file is an ISO base-media file with a different
 * extension, and every browser that reads one reads the other.
 *
 * Matroska is deliberately absent and cannot be added by probing: no browser demuxes `.mkv`
 * natively, which is exactly why the remux tier exists. A browser that claims otherwise is
 * answering a question about a codec, not a container.
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
 * Ask this browser one question.
 *
 * `MediaSource.isTypeSupported` is the stricter and more honest of the two available answers: it
 * says whether the type can be played through Media Source Extensions, which is how a segmented
 * stream is fed to the player. `canPlayType` is the fallback, and its answers are famously
 * hedged: it returns `"probably"`, `"maybe"` or `""`, and `"maybe"` means the browser has not
 * really checked. Only `"probably"` is treated as a yes here: a `"maybe"` that turns out to be a
 * no is a black screen, where a `"maybe"` treated as a no is one unnecessary conversion.
 */
function supports(type: string, probe: HTMLVideoElement): boolean {
	const mediaSource = globalThis.MediaSource;
	if (mediaSource?.isTypeSupported) {
		try {
			if (mediaSource.isTypeSupported(type)) return true;
		} catch {
			// A malformed type string throws in some browsers rather than returning false. Fall
			// through to the other question rather than taking the whole detection down.
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

/**
 * Everything this browser can play.
 *
 * Computed once and remembered: the answer cannot change while the page is open, and each probe
 * is a real call into the media stack.
 */
let cached: Capabilities | null = null;

export function capabilities(): Capabilities {
	if (cached) return cached;

	// A detached element, never added to the document. `canPlayType` needs an element to be called
	// on and needs nothing else: no source, no layout, no attachment.
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
 * What this browser reported, less one video codec: what to send when the browser said it
 * could play a codec and then could not. `canPlayType` is a guess the browser makes from a
 * string, and a decoder that answers "probably" to HEVC can still refuse a particular file.
 * The ask is made again without that codec, so the server chooses the path the browser can
 * take rather than the one it claimed it could.
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

/** Forget the cached answer. For tests, which need to probe a different pretend browser. */
export function forget(): void {
	cached = null;
}

/**
 * Whether this browser plays HLS by itself.
 *
 * Safari does, natively, which matters: on iOS there is no other option, because Media Source
 * Extensions are unavailable on iPhone. Where this is true the playlist goes straight into the
 * video element's `src` and no JavaScript player is involved at all.
 */
export function playsHlsNatively(): boolean {
	const probe = document.createElement('video');
	try {
		return probe.canPlayType('application/vnd.apple.mpegurl') !== '';
	} catch {
		return false;
	}
}
