/**
 * Hue, saturation and value: the three numbers a colour picker is actually drawn in.
 *
 * ## Why this space and not the one the accents are solved in
 *
 * `theme/accent.ts` works in OKLCH, because the question there is "is this legible on that", and a
 * lightness number only answers that in a perceptual space. The question HERE is different: it is
 * "where on the picture do I put the marker, and what colour is under my finger". HSV is the space
 * that makes a square out of a hue (saturation across, value down) with every corner of that
 * square inside sRGB. An OKLCH square is not a square: its gamut boundary is a curve that moves
 * with the hue, so the corners would be colours no screen can paint and the marker could be
 * dragged somewhere that does not exist.
 *
 * So the two spaces answer two questions and neither replaces the other. A colour is CHOSEN here
 * and then DERIVED there, and the hex in between is the whole of what passes.
 *
 * ## Why a parse rather than a check and a parse
 *
 * `toHsv` answers `null` for anything that is not a colour, so "is this a colour" and "which colour
 * is it" are one question asked once. The shape check itself is `isHex`, imported from where the
 * app already declares it rather than written again here: it is one rule, and the version of it
 * this file would have grown is the version that comes to disagree with the server's.
 */

import { isHex } from '$lib/theme/accent';

/** Hue in degrees (0..360), saturation and value each 0..1. */
interface Hsv {
	hue: number;
	saturation: number;
	value: number;
}

const clamp01 = (n: number): number => Math.min(1, Math.max(0, n));

/** A hue folded back into the circle, so 370 is 10 and -10 is 350. */
export const turn = (degrees: number): number => ((degrees % 360) + 360) % 360;

/**
 * A colour, as the three numbers the picker is drawn in. `null` when it is not a colour.
 *
 * A GREY HAS NO HUE, and this says so by answering 0 rather than by guessing. Which hue a grey
 * "was" is not a fact about the grey: it is a fact about what the person was looking at before
 * they made it grey, and only the control watching them knows that. `ColorPicker` keeps the hue it
 * had for exactly that case; a function handed six f's cannot.
 */
export function toHsv(colour: string): Hsv | null {
	if (!isHex(colour)) return null;
	const digits = colour.trim().slice(1);
	const channel = (at: number) => parseInt(digits.slice(at, at + 2), 16) / 255;
	const [red, green, blue] = [channel(0), channel(2), channel(4)];

	const high = Math.max(red, green, blue);
	const low = Math.min(red, green, blue);
	const spread = high - low;

	let hue = 0;
	if (spread > 0) {
		if (high === red) hue = ((green - blue) / spread) * 60;
		else if (high === green) hue = ((blue - red) / spread + 2) * 60;
		else hue = ((red - green) / spread + 4) * 60;
	}

	return { hue: turn(hue), saturation: high === 0 ? 0 : spread / high, value: high };
}

/** The other way: `#rrggbb`, lower case, which is the one spelling anything outside here sees. */
export function toHex({ hue, saturation, value }: Hsv): string {
	const chroma = clamp01(value) * clamp01(saturation);
	const sixth = turn(hue) / 60;
	const middle = chroma * (1 - Math.abs((sixth % 2) - 1));
	const floor = clamp01(value) - chroma;

	/* The six faces of the cube, as offsets from the darkest channel. Written as a table rather than
	   as a chain of comparisons because the pattern is the point: each face holds one channel at the
	   top, one at the bottom, and slides the third between them. */
	const faces: [number, number, number][] = [
		[chroma, middle, 0],
		[middle, chroma, 0],
		[0, chroma, middle],
		[0, middle, chroma],
		[middle, 0, chroma],
		[chroma, 0, middle]
	];
	const face = faces[Math.min(5, Math.floor(sixth))];

	return (
		'#' +
		face
			.map((part) =>
				Math.round(clamp01(part + floor) * 255)
					.toString(16)
					.padStart(2, '0')
			)
			.join('')
	);
}

/** The pure hue at full saturation and full value, for the ground the picker's square is washed
 *  over. A colour built from a number, which is what makes it data rather than a decision. */
export const hueColour = (hue: number): string => toHex({ hue, saturation: 1, value: 1 });
