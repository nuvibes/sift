/*
 * The facts a panel of facts is made of.
 *
 * Kept apart from the player so that the player and a Theater cell answer the same questions with
 * the same words: two copies would drift into one panel saying things the other never mentions, a
 * raw `transcode` beside "Converted", in different corners wearing different surfaces. A panel somebody has learned in one place is worth
 * nothing if it is a different panel in the other.
 *
 * Plain functions rather than a component's private ones, because these are the half of the panel
 * that can be checked without a browser: every one of them takes numbers and strings and returns
 * the sentence that goes on screen.
 *
 * ## What is here and what is in `$lib/library/facts`
 *
 * A size, a length, a rate, a shape, an encoder and a bit depth are the FILE's facts, and the
 * record on the asset page draws them too, so they are written down in one place, `$lib/library/facts`,
 * and this file asks it. What stays here is what only a panel of diagnostics has: an average
 * bitrate, an aspect ratio, what is being done to the file to play it, how far ahead the browser
 * has buffered, and the panel's own word for a fact it does not have.
 *
 * That word is why these functions exist at all rather than the shared ones being called
 * directly: the shared ones hand back nothing when there is nothing, and this panel says
 * `Unknown` where a record says an em dash. Both are right on their own surface.
 */

import * as facts from '$lib/library/facts';
import type { components } from '$lib/api/schema';

/** What a panel of diagnostics says about a fact it does not have. */
const UNKNOWN = 'Unknown';

/**
 * What a file is, as the library recorded it.
 *
 * Every field is sent and every one may be null. A file Sift has not finished reading yet, and one
 * whose stream it could not read at all, are both ordinary, so null is what "not known" looks
 * like here, and each function below has an answer for knowing nothing.
 *
 * Not OPTIONAL: the only thing that produces one of these is a detail response, which carries all
 * eight, and a screen that built one by hand from five of them would silently report the other
 * three as unknown while holding them.
 */
export type FileFacts = Pick<
	components['schemas']['AssetDetail'],
	'width' | 'height' | 'container' | 'size_bytes' | 'vcodec' | 'acodec' | 'fps' | 'bit_depth'
>;

/** The picture's encoding and the sound's, or whichever of the two is known. */
export function codecs(file: FileFacts | null | undefined): string {
	const picture = facts.codec(file?.vcodec);
	const sound = facts.codec(file?.acodec);
	if (picture && sound) return `${picture} / ${sound}`;
	return picture ?? sound ?? UNKNOWN;
}

/** Frames a second. */
export function rate(file: FileFacts | null | undefined): string {
	return facts.rate(file?.fps) ?? UNKNOWN;
}

/*
 * The shape of the picture, named where it has a name.
 *
 * Reduced by the greatest common divisor, which turns 1920x1080 into 16:9 and 1440x1080 into
 * 4:3 without a table of known shapes. Anything that reduces to something nobody says out loud
 * (683:384, off an odd crop) is reported as a decimal instead, because "683:384" is a
 * correct answer that tells you less than "1.78".
 */
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

/*
 * How much of the file every second of it takes, on average.
 *
 * Worked out from what is on disk and how long it runs rather than read off the stream, which
 * is what "overall bitrate" means everywhere else too: it counts the container and the sound as
 * well as the picture, and it is the number that decides whether a file will stream over a
 * given connection. A still has no time to divide by and gets nothing.
 */
export function bitrate(file: FileFacts | null | undefined, seconds: number): string {
	const bytes = file?.size_bytes;
	if (!bytes || bytes <= 0 || !Number.isFinite(seconds) || seconds <= 0) return UNKNOWN;
	const bits = (bytes * 8) / seconds;
	if (bits >= 1_000_000) return `${(bits / 1_000_000).toFixed(1)} Mbps`;
	return `${Math.round(bits / 1000)} kbps`;
}

/** How many bits each colour sample carries. */
export function depth(file: FileFacts | null | undefined): string {
	return facts.depth(file?.bit_depth) ?? UNKNOWN;
}

/** A size on disk. */
export function size(bytes: number | null | undefined): string {
	return facts.size(bytes) ?? UNKNOWN;
}

/* What is being done to the file to play it, said the way it is said everywhere else on screen.
 * `direct`, `remux` and `transcode` are the words this code uses among itself, and a Theater cell
 * must not print them at somebody, which is the drift this file exists to stop.
 *
 * `remux` covers two unrelated problems and needs two words. One copy exists because the browser
 * cannot read the container; the other because the file stores its audio too far from its picture
 * for seeking to work. Calling both "Repackaged" describes the second one's problem as the first
 * one's, which on a wall of cells is the only thing said about it at all, since a cell draws no
 * reason under the picture the way the single player does.
 *
 * ## THE THREE ANSWERS ARE NAMED FOR WHAT IS HAPPENING NOW
 *
 * Not "As it is", "Repackaged" and "Converted": nouns describing the FILE, on a row headed "How",
 * in a panel of machine facts. The row must not print `remux` at somebody, but it answers "how is
 * this being played", and a participle is what answers that: direct play, remuxing, transcoding.
 * They are also the words the rest of this kind of software uses, so somebody who has met them
 * anywhere else needs nothing explained.
 *
 * The third state is genuine and is NOT invented for symmetry: the server decides between three
 * routes, and `remux` is a container copy with no re-encode. See `PlaybackPlan.route`. What
 * `copy_kind` distinguishes is which of the two problems that copy was written for, which is a
 * narrower question and rides in the brackets rather than becoming a fourth answer.
 */
export function playingAs(route: string | null | undefined, copy?: string | null): string {
	if (route === 'direct') return 'Direct play';
	if (route === 'remux') return copy === 'repaired' ? 'Remuxing (repaired)' : 'Remuxing';
	if (route === 'transcode') return 'Transcoding';
	if (route === 'unread') return 'Not read yet';
	return 'Not yet';
}

/** Both halves of a shape, when both are known. */
export function dimensions(
	across: number | null | undefined,
	down: number | null | undefined
): string {
	return facts.dimensions(across, down) ?? UNKNOWN;
}

/** How much of the clip the browser is holding ahead of the playhead, in seconds. */
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

/* Where a playhead is, the way every clock in Sift is written: `$lib/shell/duration`'s, handed on from
   here because the stats panel and the remote read their facts from this module. */
export { clock } from '$lib/shell/duration';

/**
 * What to say about a file whose sound is stored too far from its picture.
 *
 * Here rather than in one screen, because two screens say this: the item detail, and a cell on a
 * Theater wall. Written twice they would be two explanations of one fact, and the day one is
 * improved the other quietly becomes the wrong one.
 *
 * Each sentence opens on what somebody NOTICES and never on the cause. The cause (the sound
 * stored far from the picture) is how a container orders its packets, the one part of this a
 * person cannot check and cannot act on. The repaired sentence is the player's own (`_COPY_REASON`
 * in the player's policy), word for word, and the other two keep its words for the copy and the
 * original, so one file is explained one way on one screen.
 *
 * The sentences also say WHAT the copy is. It is a remux (`remux_args` in the media jobs' ffmpeg
 * module, a `-c copy` of every stream into a new MP4, nothing decoded or re-compressed), so they
 * say that in plain words: a repackaged copy, the same picture and sound, rewrapped. "Repackaged"
 * is the word the other copy (a container the browser cannot read) uses for the same act, so one
 * act has one word.
 *
 * Empty for a file with nothing to say, which is nearly all of them: a handful in a thousand.
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
	/* The only one of the three where something is actually wrong from where the reader is sitting.
	   On the item detail a link to the setting follows it; a wall has nowhere to send somebody, so
	   the sentence has to stand on its own and does. */
	if (state === 'off') {
		return (
			'Skipping through this file may be jerky. Sift can keep a repackaged copy \u2014 the ' +
			"same picture and sound, rewrapped so it skips smoothly \u2014 and that's switched off."
		);
	}
	return '';
}

/** The player's own sentence for a repaired copy (`_COPY_REASON` on the server), held once here so
 *  a test can hold the two together. */
export const REPAIRED_WORDS =
	'Playing a repackaged copy \u2014 the same picture and sound, rewrapped so skipping through ' +
	"it doesn't stall. The original file is untouched.";

/** What the mark opening those words is called. A question, because it opens an answer. */
/**
 * What the mark in the corner of a picture says: why the file is taking the path it is taking.
 *
 * One rule for the file page, the corner panel and a Theater cell, so the three cannot come to
 * explain one file differently. Any path that is not direct play says so in the server's own
 * words (converted, reduced, repackaged, repaired), because those are the words the plan was
 * answered with, and the facts panel two lines below reads the same plan. A file that plays as
 * it is has only the repair to mention, where there is one to mention.
 *
 * An unread file is left out: the stage says so in the middle of the picture, and a mark beside
 * a sentence would be the sentence twice.
 */
export function noticeWords(
	plan: { route: string; reason: string; streamable?: boolean } | null | undefined,
	repair: string | null | undefined
): string {
	// A file the machine cannot convert fast enough is said by the panel over the picture, which
	// carries the reason and the offer to try anyway. Said again in the corner it would be the same
	// sentence twice on one screen.
	if (plan && plan.route === 'transcode' && plan.streamable === false) return '';
	if (plan && plan.route !== 'direct' && plan.route !== 'unread') return plan.reason;
	return repairWords(repair);
}

/** What the mark is called for a screen reader. A question, because it opens an answer. */
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
