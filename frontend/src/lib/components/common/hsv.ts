/** Hue, saturation and value, the space a picker is drawn in: its square stays inside sRGB,
 * unlike OKLCH, where accents are derived. A hex passes between the two. */

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

/** A colour as the picker's numbers, or null; a grey's hue is 0, as ColorPicker keeps its own. */
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

	/* The cube's six faces as offsets from the darkest channel. */
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

/** The pure hue, for the ground under the picker's square. */
export const hueColour = (hue: number): string => toHex({ hue, saturation: 1, value: 1 });
