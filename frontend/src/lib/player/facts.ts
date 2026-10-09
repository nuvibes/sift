/*
 * The facts a panel of diagnostics is made of, shared by the player and a Theater cell. The file's
 * own facts are `$lib/library/facts`; these add the panel's `Unknown` where a record draws a dash.
 */

import * as facts from '$lib/library/facts';
import type { components } from '$lib/api/schema';

const UNKNOWN = 'Unknown';

/** Every field may be null, and all eight are carried, so none is reported unknown while held. */
export type FileFacts = Pick<
	components['schemas']['AssetDetail'],
	'width' | 'height' | 'container' | 'size_bytes' | 'vcodec' | 'acodec' | 'fps' | 'bit_depth'
>;

export function codecs(file: FileFacts | null | undefined): string {
	const picture = facts.codec(file?.vcodec);
	const sound = facts.codec(file?.acodec);
	if (picture && sound) return `${picture} / ${sound}`;
	return picture ?? sound ?? UNKNOWN;
}

export function rate(file: FileFacts | null | undefined): string {
	return facts.rate(file?.fps) ?? UNKNOWN;
}

/* Reduced by the GCD; a shape nobody says out loud is reported as a decimal. */
export function ratio(file: FileFacts | null | undefined): string {
	const across = file?.width;
	const down = file?.height;
	if (!across || !down || across <= 0 || down <= 0) return UNKNOWN;
	let a = Math.round(across);
	let b = Math.round(down);
	while (b) [a, b] = [b, a % b];
	const w = Math.round(across / a);
	const h = Math.round(down / a);
	if (w <= 32 && h <= 32) return `${w}:${h}`;
	return `${(across / down).toFixed(2)}:1`;
}

/* Overall: size over length, the number that decides whether a file streams. */
export function bitrate(file: FileFacts | null | undefined, seconds: number): string {
	const bytes = file?.size_bytes;
	if (!bytes || bytes <= 0 || !Number.isFinite(seconds) || seconds <= 0) return UNKNOWN;
	const bits = (bytes * 8) / seconds;
	if (bits >= 1_000_000) return `${(bits / 1_000_000).toFixed(1)} Mbps`;
	return `${Math.round(bits / 1000)} kbps`;
}

export function depth(file: FileFacts | null | undefined): string {
	return facts.depth(file?.bit_depth) ?? UNKNOWN;
}

export function size(bytes: number | null | undefined): string {
	return facts.size(bytes) ?? UNKNOWN;
}

/*
 * How the file is being played, as a participle (direct play, remuxing, transcoding), never the
 * code's own words. `copy_kind` says which of two problems a remux was for, in brackets.
 */
export function playingAs(route: string | null | undefined, copy?: string | null): string {
	if (route === 'direct') return 'Direct play';
	if (route === 'remux') return copy === 'repaired' ? 'Remuxing (repaired)' : 'Remuxing';
	if (route === 'transcode') return 'Transcoding';
	if (route === 'unread') return 'Not read yet';
	return 'Not yet';
}

export function dimensions(
	across: number | null | undefined,
	down: number | null | undefined
): string {
	return facts.dimensions(across, down) ?? UNKNOWN;
}

export function bufferedAhead(
	media: HTMLVideoElement | null | undefined,
	position: number
): number {
	if (!media || media.buffered === undefined || media.buffered.length === 0) return 0;
	for (let range = 0; range < media.buffered.length; range += 1) {
		if (media.buffered.start(range) <= position && position <= media.buffered.end(range)) {
			return Math.max(0, media.buffered.end(range) - position);
		}
	}
	return 0;
}

export { clock } from '$lib/shell/duration';

/**
 * What to say about a file whose sound is stored far from its picture, for the item detail and a
 * Theater cell. Opens on what somebody NOTICES; the repaired sentence is the player's own.
 */
function repairWords(state: string | null | undefined): string {
	if (state === 'repaired') return REPAIRED_WORDS;
	if (state === 'pending') {
		return (
			'Skipping through this file may be jerky until Sift finishes a repackaged copy ' +
			'\u2014 the same picture and sound, rewrapped so it skips smoothly. ' +
			'The original file is untouched.'
		);
	}
	/* The one where something is wrong for the reader; a wall has no link, so it stands alone. */
	if (state === 'off') {
		return (
			'Skipping through this file may be jerky. Sift can keep a repackaged copy \u2014 the ' +
			"same picture and sound, rewrapped so it skips smoothly \u2014 and that's switched off."
		);
	}
	return '';
}

/** The server's `_COPY_REASON`, held once so a test can hold the two together. */
export const REPAIRED_WORDS =
	'Playing a repackaged copy \u2014 the same picture and sound, rewrapped so skipping through ' +
	"it doesn't stall. The original file is untouched.";

/**
 * Why the file takes the path it takes, in the server's words, for the file page, the corner panel
 * and a Theater cell alike. An unread file is left out.
 */
export function noticeWords(
	plan: { route: string; reason: string; streamable?: boolean } | null | undefined,
	repair: string | null | undefined
): string {
	// Said by the panel over the picture already.
	if (plan && plan.route === 'transcode' && plan.streamable === false) return '';
	if (plan && plan.route !== 'direct' && plan.route !== 'unread') return plan.reason;
	return repairWords(repair);
}

export function noticeLabel(
	plan: { route: string } | null | undefined,
	repair: string | null | undefined
): string {
	if (plan && plan.route === 'transcode') return 'Why this file is being converted';
	if (plan && plan.route === 'remux') return 'Why this file is playing from a copy';
	return repairLabel(repair);
}

function repairLabel(state: string | null | undefined): string {
	return state === 'off'
		? 'Why skipping through this file is jerky'
		: 'Why this file is playing from a copy';
}
