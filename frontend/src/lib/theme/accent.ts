// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * A seventh accent worked out from one chosen colour by the five rules `app.css` records for the
 * six. Every role is solved for its contrast floor; the caller hands in the grounds read off the
 * page, since a colour is named in `app.css` and nowhere else.
 */

/** Red, green and blue, each 0..1. */
type Rgb = [number, number, number];

/** OKLCH, the space the palette was solved in: its lightness matches what the eye sees. */
type Lch = [number, number, number];

/** The three grounds a derived accent has to hold up against, as the page is wearing them. */
export interface Ground {
	/** `--p-bg`: the app canvas. The tint is mixed into this, and the text role is lifted on it. */
	canvas: string;
	/** `--p-surface-3`: an input, a hover. The highest surface the text role is allowed on. */
	surface3: string;
	/** `--p-surface-4`: a chip, a pressed button. Where the ring's own line has to stay visible. */
	surface4: string;
}

/** The five values an accent block in the stylesheet sets, in the same roles and order. */
export interface AccentFamily {
	/** `--p-accent`: the fill. Buttons and markers, carrying white. */
	accent: string;
	/** `--p-accent-hover`: the fill, one step lighter. */
	hover: string;
	/** `--p-accent-text`: accent-coloured words, on a card. */
	text: string;
	/** `--p-accent-bg`: the tinted surface under an active row or a selected chip. */
	bg: string;
	/** `--p-accent-ring-line`: the focus ring's own line. */
	ring: string;
}

/* The floors, from the design system. */
const TEXT_FLOOR = 4.5;
const ELEMENT_FLOOR = 3;

/* What the six were solved to (`app.css`), with margins above the published floors. */
const TEXT_ON_CARD = 4.7;
const RING_ON_CHIP = 3.05;
const TEXT_ON_CANVAS = 9;

/* Where the shipped text roles stop; past it a hue stops reading as the one chosen. */
const TEXT_CEILING = 0.78;

/* The shipped hover gap; not held to the white floor, as a hover is transient. */
const HOVER_STEP = 0.0523;

/* Reproduces all fifteen shipped tints to within a rounding step. */
const TINT_SHARE = 0.22;

/* A colour exactly on the sRGB edge rounds outside it, and a clipped channel moves the hue. */
const CHROMA_HEADROOM = 0.98;

/* About half what the eye can tell apart, under one 8-bit step. */
const LIGHTNESS_STEP = 0.002;

const clamp01 = (n: number): number => Math.min(1, Math.max(0, n));

const toLinear = (c: number): number => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const toGamma = (c: number): number =>
	c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055;

/** `#rgb` or `#rrggbb`. See `isHex`. */
function parseHex(value: string): Rgb {
	const digits = value.trim().replace('#', '');
	const wide =
		digits.length === 3
			? [...digits].map((one) => one + one).join('')
			: digits.length === 8
				? digits.slice(0, 6)
				: digits;
	const at = (index: number) => parseInt(wide.slice(index, index + 2), 16) / 255;
	return [at(0), at(2), at(4)];
}

/** `#rrggbb`, lower case; out-of-gamut channels are clamped here and nowhere else. */
function toHex(rgb: Rgb): string {
	return (
		'#' +
		rgb
			.map((channel) =>
				Math.round(clamp01(channel) * 255)
					.toString(16)
					.padStart(2, '0')
			)
			.join('')
	);
}

/** Whether a string is a colour a person may have typed: a shape check, as the server's is. */
export function isHex(value: string): boolean {
	return /^#[0-9a-fA-F]{6}$/.test(value.trim());
}

function toOklab(rgb: Rgb): [number, number, number] {
	const [r, g, b] = rgb.map(toLinear);
	const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
	const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
	const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
	return [
		0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
		1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
		0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s
	];
}

function fromOklab([lightness, a, b]: [number, number, number]): Rgb {
	const l = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3;
	const m = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3;
	const s = (lightness - 0.0894841775 * a - 1.291485548 * b) ** 3;
	return [
		toGamma(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
		toGamma(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
		toGamma(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s)
	];
}

function toLch(rgb: Rgb): Lch {
	const [lightness, a, b] = toOklab(rgb);
	return [lightness, Math.hypot(a, b), (Math.atan2(b, a) * (180 / Math.PI) + 360) % 360];
}

function fromLch([lightness, chroma, hue]: Lch): Rgb {
	const radians = hue * (Math.PI / 180);
	return fromOklab([lightness, chroma * Math.cos(radians), chroma * Math.sin(radians)]);
}

/** Whether a colour returns to sRGB unclipped: a clipped channel moves its hue. */
const inGamut = (rgb: Rgb): boolean =>
	rgb.every((channel) => channel >= -0.0005 && channel <= 1.0005);

/** The most chroma sRGB holds at this lightness and hue, bisected: no closed form. */
function chromaCeiling(lightness: number, hue: number): number {
	let low = 0;
	let high = 0.45;
	for (let round = 0; round < 28; round += 1) {
		const middle = (low + high) / 2;
		if (inGamut(fromLch([lightness, middle, hue]))) low = middle;
		else high = middle;
	}
	return low;
}

/* Eight bits a channel: every floor is checked on what gets painted, not on the float. */
const snap = (rgb: Rgb): Rgb => parseHex(toHex(rgb));

/** The chosen hue and chroma at a lightness, inside sRGB: a muted pick stays muted. */
function at(lightness: number, chroma: number, hue: number): Rgb {
	return snap(
		fromLch([lightness, Math.min(chroma, CHROMA_HEADROOM * chromaCeiling(lightness, hue)), hue])
	);
}

function luminance(rgb: Rgb): number {
	const [r, g, b] = rgb.map(toLinear);
	return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** The WCAG 2.x ratio, 1..21, written out so the floors mean exactly what is quoted. */
export function contrast(a: Rgb, b: Rgb): number {
	const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
	return (high + 0.05) / (low + 0.05);
}

const WHITE: Rgb = [1, 1, 1];

/**
 * The fill: the chosen colour moved in lightness only as far as white text on it and a marker on
 * the canvas both need, searched outward; where no lightness clears both, the closest.
 */
function fillFor(chosen: Lch, canvas: Rgb): Rgb {
	const [chosenLightness, chroma, hue] = chosen;
	let best: { rgb: Rgb; score: number } | null = null;
	for (let step = 0; step <= 1 / LIGHTNESS_STEP; step += 1) {
		for (const lightness of [
			chosenLightness + step * LIGHTNESS_STEP,
			chosenLightness - step * LIGHTNESS_STEP
		]) {
			if (lightness < 0 || lightness > 1) continue;
			const rgb = at(lightness, chroma, hue);
			const onWhite = contrast(WHITE, rgb) / TEXT_FLOOR;
			const onCanvas = contrast(rgb, canvas) / ELEMENT_FLOOR;
			if (onWhite >= 1 && onCanvas >= 1) return rgb;
			const score = Math.min(onWhite, onCanvas);
			if (best === null || score > best.score) best = { rgb, score };
		}
	}
	// Unreachable for every hue on today's bases; another base would be a stylesheet change.
	return best!.rgb;
}

/** The text role: the lowest lightness legible everywhere it is allowed, lifted towards 9.0:1. */
function textFor(chosen: Lch, fill: Rgb, tint: Rgb, ground: Ground): Rgb {
	const [, chroma, hue] = chosen;
	const canvas = parseHex(ground.canvas);
	const card = parseHex(ground.surface3);
	const chip = parseHex(ground.surface4);
	const from = toLch(fill)[0];

	let legible: Rgb | null = null;
	for (let lightness = from; lightness <= 1; lightness += LIGHTNESS_STEP) {
		const rgb = at(lightness, chroma, hue);
		if (
			contrast(rgb, card) < TEXT_ON_CARD ||
			contrast(rgb, tint) < TEXT_ON_CARD ||
			contrast(rgb, chip) < RING_ON_CHIP
		) {
			continue;
		}
		legible = rgb;
		if (contrast(rgb, canvas) >= TEXT_ON_CANVAS || lightness >= TEXT_CEILING) return rgb;
	}
	// Nothing at or under white cleared the card: white clears everything a dark base can hold.
	return legible ?? WHITE;
}

/** The tint: the fill mixed into the canvas in OKLab, where an average is perceptual. */
function tintFor(fill: Rgb, canvas: Rgb): Rgb {
	const front = toOklab(fill);
	const back = toOklab(canvas);
	return snap(
		fromOklab([
			TINT_SHARE * front[0] + (1 - TINT_SHARE) * back[0],
			TINT_SHARE * front[1] + (1 - TINT_SHARE) * back[1],
			TINT_SHARE * front[2] + (1 - TINT_SHARE) * back[2]
		])
	);
}

/** The whole family from one `#rrggbb` colour and the grounds of the page it goes onto. */
export function accentFamily(chosen: string, ground: Ground): AccentFamily {
	const canvas = parseHex(ground.canvas);
	const wanted = toLch(parseHex(chosen));
	const [, chroma, hue] = wanted;

	const fill = fillFor(wanted, canvas);
	const tint = tintFor(fill, canvas);
	const text = textFor(wanted, fill, tint, ground);

	// Between the fill and the word: seen against a chip without being as loud as the fill.
	const ring = at((toLch(fill)[0] + toLch(text)[0]) / 2, chroma, hue);

	return {
		accent: toHex(fill),
		hover: toHex(at(Math.min(1, toLch(fill)[0] + HOVER_STEP), chroma, hue)),
		text: toHex(text),
		bg: toHex(tint),
		ring: toHex(ring)
	};
}

/* About the smallest difference anybody can see side by side. */
const SEEN_APART = 0.02;

/** How the worn fill differs from the chosen colour, or null when it looks the same. */
export function wornDifferently(
	chosen: string,
	worn: string
): 'darker' | 'lighter' | 'softer' | null {
	if (!isHex(chosen) || !isHex(worn)) return null;
	const before = toOklab(parseHex(chosen));
	const after = toOklab(parseHex(worn));
	const apart = Math.hypot(before[0] - after[0], before[1] - after[1], before[2] - after[2]);
	if (apart < SEEN_APART) return null;
	const lighter = after[0] - before[0];
	if (Math.abs(lighter) < SEEN_APART / 2) return 'softer';
	return lighter < 0 ? 'darker' : 'lighter';
}
