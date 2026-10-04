/*
 * The one way a file's facts are written down.
 *
 * A size, a length, a frame rate, a shape, an encoder and a bit depth are drawn on four surfaces:
 * the record on the asset page, the player's stats panel, the same panel over a still, and the
 * badge on a tile. Four copies of a rule disagree: a six-gigabyte file reads `5.6 GB` in one place
 * and `6.0 GB` two inches below it (1024 against 1000), `H.264` against `h264`, a ninety-minute
 * film `90:00` against `1:30:00`. The fix is not to correct four copies but to have one.
 *
 * ## Absent is the CALLER's word, not this file's
 *
 * Every function here returns `null` for a value there is nothing to say about, and never a
 * sentence. The record writes a blank as an em dash because that is what a record's empty row looks
 * like; the stats panel writes `Unknown` because it is a panel of diagnostics and a dash there
 * reads as "zero". Both are right, and a shared formatter that picked one of them would be wrong on
 * the other surface. So this file formats what IS known and hands back nothing when there is
 * nothing.
 *
 * ## Why a size divides by 1000
 *
 * `GB` means a thousand million bytes; a thousand and twenty-four of them is a `GiB`. Dividing by
 * 1024 and writing `GB` is the one of the two that cannot be defended. If binary units are ever
 * wanted, this is the one place to change, and the labels have to change with it.
 */

import { lengthClock } from '$lib/shell/duration';

/**
 * The byte ladder, biggest last, each rung a thousand of the one before it.
 *
 * Exported because `$lib/shell/units` builds the unit MENUS on numeric settings from it (offering GB
 * beside a setting stored in MB), and a second list of these names is exactly what this file
 * exists to prevent. `check_one_facts_definition.js` refuses one.
 */
export const BYTE_UNITS = ['B', 'kB', 'MB', 'GB', 'TB'];

const UNITS = BYTE_UNITS;

/**
 * A size on disk, in whichever unit keeps it to three or four figures.
 *
 * Zero is a real size and reads as `0 B`: a file that is there and empty is a fact worth drawing,
 * and it is not the same as not knowing.
 */
export function size(bytes: number | null | undefined): string | null {
	if (typeof bytes !== 'number' || !Number.isFinite(bytes) || bytes < 0) return null;
	let amount = bytes;
	let step = 0;
	while (amount >= 1000 && step < UNITS.length - 1) {
		amount /= 1000;
		step += 1;
	}
	if (step === 0) return `${Math.round(amount)} ${UNITS[step]}`;
	return `${amount < 10 ? amount.toFixed(1) : Math.round(amount)} ${UNITS[step]}`;
}

/**
 * A size on its way to a total, as a transfer draws it: `11.4 GB of 11.5 GB`.
 *
 * Both in the TOTAL's unit and to one decimal, however large. `size` drops the decimal from ten
 * upward, which is right for a size standing still and wrong for one that moves: beside a total of
 * 11.53 GB the moving figure would read `11 GB` for minutes while the total read `12 GB`, so the
 * last gigabyte of a swap would look stopped and its end look short. One unit for the pair keeps the two
 * figures comparable (`0.4 GB of 11.5 GB`, never `400 MB of 11.5 GB`). A total under a kilobyte
 * is counted in bytes, which have no tenths.
 */
export function sizeOf(
	done: number | null | undefined,
	total: number | null | undefined
): string | null {
	const whole = size(total);
	if (whole === null || typeof done !== 'number' || !Number.isFinite(done) || done < 0) {
		return null;
	}
	let step = 0;
	let divisor = 1;
	while ((total as number) / divisor >= 1000 && step < UNITS.length - 1) {
		divisor *= 1000;
		step += 1;
	}
	const unit = UNITS[step];
	if (step === 0) return `${Math.round(done)} ${unit} of ${Math.round(total as number)} ${unit}`;
	const tenth = (bytes: number) => (Math.floor((bytes / divisor) * 10) / 10).toFixed(1);
	return `${tenth(done)} ${unit} of ${tenth(total as number)} ${unit}`;
}

/**
 * How long a file runs, from milliseconds.
 *
 * Hours appear only when there are some: `4:07` for a clip, `1:30:00` for a film. Minutes are
 * padded once there is an hour in front of them and not before, because `0:04:07` is a clock and
 * `4:07` is a length.
 */
export function length(ms: number | null | undefined): string | null {
	if (typeof ms !== 'number' || !Number.isFinite(ms) || ms <= 0) return null;
	return lengthClock(ms / 1000);
}

/** Frames a second, to two places and without a trailing zero on a whole number. */
export function rate(fps: number | null | undefined): string | null {
	if (typeof fps !== 'number' || !Number.isFinite(fps) || fps <= 0) return null;
	return `${Number(fps.toFixed(2))} fps`;
}

/** The shape of the picture, in pixels. */
export function dimensions(
	across: number | null | undefined,
	down: number | null | undefined
): string | null {
	if (typeof across !== 'number' || typeof down !== 'number') return null;
	if (!Number.isFinite(across) || !Number.isFinite(down) || across <= 0 || down <= 0) return null;
	return `${Math.round(across)} x ${Math.round(down)}`;
}

/*
 * Written the way the encoders are named rather than the way ffmpeg spells them: `h264` is H.264
 * to everybody who has ever chosen it, and `av1` is AV1 rather than Av1. Anything not in the list
 * is passed through as it came, because a name nobody here anticipated is still more use than
 * "Unknown".
 */
const CODEC_NAMES: Record<string, string> = {
	av1: 'AV1',
	vp9: 'VP9',
	vp8: 'VP8',
	h264: 'H.264',
	avc1: 'H.264',
	hevc: 'H.265',
	h265: 'H.265',
	hev1: 'H.265',
	mpeg4: 'MPEG-4',
	aac: 'AAC',
	mp3: 'MP3',
	opus: 'Opus',
	vorbis: 'Vorbis',
	flac: 'FLAC',
	ac3: 'AC-3',
	eac3: 'E-AC-3',
	pcm_s16le: 'PCM'
};

/** One encoder, by the name the person who chose it knows it by. */
export function codec(word: string | null | undefined): string | null {
	if (typeof word !== 'string' || word.trim() === '') return null;
	const cleaned = word.trim();
	return CODEC_NAMES[cleaned.toLowerCase()] ?? cleaned;
}

/**
 * How many bits each colour sample carries.
 *
 * Zero is not a depth. It is what a file that is there and could not be read is written down as, so
 * the pass that fills this in does not come back to it for ever. And it says nothing about the
 * picture, so it is reported the same way an absent one is.
 */
export function depth(bits: number | null | undefined): string | null {
	if (typeof bits !== 'number' || !Number.isFinite(bits) || bits <= 0) return null;
	return `${Math.round(bits)}-bit`;
}
