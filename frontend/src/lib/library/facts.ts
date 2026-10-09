/* The one way a file's facts are written down. */

import { lengthClock } from '$lib/shell/duration';

/** The byte ladder, biggest last, each rung a thousand of the one before it. */
export const BYTE_UNITS = ['B', 'kB', 'MB', 'GB', 'TB'];

const UNITS = BYTE_UNITS;

/** A size on disk, in whichever unit keeps it to three or four figures. */
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

/** A size on its way to a total, as a transfer draws it: `11.4 GB of 11.5 GB`. */
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

/** How long a file runs, from milliseconds. Hours appear only when there are some: `4:07` for a
 * clip, `1:30:00` for a film. */
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

/* Written the way the encoders are named rather than the way ffmpeg spells them: `h264` is H.264
 * to everybody who has ever chosen it, and `av1` is AV1 rather than Av1. */
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

/** How many bits each colour sample carries. Zero is not a depth. */
export function depth(bits: number | null | undefined): string | null {
	if (typeof bits !== 'number' || !Number.isFinite(bits) || bits <= 0) return null;
	return `${Math.round(bits)}-bit`;
}
