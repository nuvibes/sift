// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * A seventh accent, worked out from one colour somebody chose.
 *
 * ## Why this is arithmetic rather than six more values in the stylesheet
 *
 * The six named accents are one family: the same lightness, hues spaced evenly, and each given as
 * much chroma as sRGB can hold there. They are not picked by eye: `app.css` writes the five rules
 * that produce them down, beside the values, precisely so that "a seventh accent is derived rather
 * than guessed". A chosen colour is that seventh accent. So this runs the same five rules on
 * whatever hue arrives, and the result still looks like a member of the family because the rules
 * are what made the family in the first place.
 *
 * ## The five roles, and why one colour cannot be all of them
 *
 * A fill carries white text, so it is held DOWN to where white passes on it. The text role sits on
 * a card, so it is held UP to where it is legible there. One value cannot do both jobs, which is
 * why the stylesheet has five names per accent and why this returns five.
 *
 * ## Contrast is the rule, not a check afterwards
 *
 * Every role below is SOLVED for its floor rather than nudged and measured. The floors are the
 * design system's own (body text 4.5:1, an element or a meaningful graphic 3:1) and they are
 * the same floors `design/contrast.test.ts` holds the six named accents to. `accent.test.ts` runs a
 * grid of chosen colours through this against every base and measures what comes out, so the
 * claim in this paragraph is a measurement rather than an intention.
 *
 * ## Why it takes the GROUND rather than the name of a base
 *
 * A base's canvas and surfaces are colours, and a colour is named in `app.css` and nowhere else:
 * a table of the bases' canvases kept here would be a second declaration of them, free to
 * drift the first time one is retuned, and the repository's colour gate refuses exactly that. So
 * the caller reads the three grounds off the page it is about to paint and hands them in. That also
 * makes this a pure function of what it is given, which is what lets the test measure it.
 */

/** Red, green and blue, each 0..1. */
type Rgb = [number, number, number];

/** Lightness 0..1, chroma, and hue in degrees: OKLCH, which is the space the palette was solved
 *  in. A lightness number there matches what the eye sees, which is the whole reason: in HSL a
 *  "50% lightness" yellow is far brighter than a 50% blue, and that is how a palette ends up with
 *  one accent nobody can read. */
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

/* The floors, from the design system. Named rather than repeated, because the same two numbers
   decide four of the five roles and a floor typed twice is a floor that gets changed once. */
const TEXT_FLOOR = 4.5;
const ELEMENT_FLOOR = 3;

/* What the six were solved to, and this is quoted from `app.css` rather than reinvented: the text
   role clears 4.7:1 on surface-3 and on its own tint, keeps 3.05:1 on surface-4, and is then lifted
   to 9.0:1 on the canvas. The margins above the published floors are deliberate: they are what
   stops a value that measures 4.51 today failing on the next surface that moves. */
const TEXT_ON_CARD = 4.7;
const RING_ON_CHIP = 3.05;
const TEXT_ON_CANVAS = 9;

/* The lightness the shipped text roles stop at. The gold's is 0.647 and the magenta's 0.780; past
   this a hue goes pale enough to stop reading as the colour that was chosen, so a hue that cannot
   reach 9.0:1 on the canvas by here takes the best it can get instead, which is exactly the
   "darker gold" exception the stylesheet already records. */
const TEXT_CEILING = 0.78;

/* How much lighter the hover is than the fill. The shipped gap, 0.5984 - 0.5461. The hover is
   NOT held to the white floor: the shipped blue hover measures 4.09:1, because a hover is a
   transient state under a pointer rather than a ground words are read on at rest. */
const HOVER_STEP = 0.0523;

/* How much of the fill is mixed into the canvas to make the tint. The shipped rule, and it
   reproduces all fifteen shipped tints to within a rounding step. */
const TINT_SHARE = 0.22;

/* How close to the sRGB edge a chroma is allowed. The shipped accents stop at 98% of the maximum:
   a colour sitting exactly on the boundary rounds outside it on some values, and a clipped channel
   moves the hue rather than the chroma. */
const CHROMA_HEADROOM = 0.98;

/* How finely the two searches step through lightness. 0.002 is about half of what the eye can tell
   apart in OKLCH and is well under one step of an 8-bit channel, so a finer search would return the
   same hex more slowly. */
const LIGHTNESS_STEP = 0.002;

// --- colour arithmetic ---------------------------------------------------------------------

const clamp01 = (n: number): number => Math.min(1, Math.max(0, n));

const toLinear = (c: number): number => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const toGamma = (c: number): number =>
	c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055;

/** `#rgb` or `#rrggbb`. Anything else is not a colour this can work from. See `isHex`. */
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

/** `#rrggbb`, lower case. Out-of-gamut channels are clamped here and nowhere else, so every value
 *  this module returns is one a browser can actually paint. */
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

/** Whether a string is a colour a person may have typed into the hex box. The one shape check the
 *  client and the server both make, and it is a SHAPE check: every six-digit colour is a colour. */
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

/** Whether a colour survives the trip back to sRGB without a channel being clipped. A clipped
 *  channel does not darken a colour: it moves its HUE, which is the one thing a person choosing
 *  a colour would notice. */
const inGamut = (rgb: Rgb): boolean =>
	rgb.every((channel) => channel >= -0.0005 && channel <= 1.0005);

/** The most chroma sRGB can hold at this lightness and hue. Bisected rather than solved: the gamut
 *  boundary in OKLCH has no closed form worth carrying, and twenty-eight halvings of a 0.45 range
 *  settle it to well under a channel step. */
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

/**
 * A colour as the screen will actually paint it: eight bits a channel.
 *
 * EVERY FLOOR BELOW IS CHECKED AGAINST THIS AND NOT AGAINST THE FLOAT, and that is not fussiness. A
 * search that stops at the first lightness clearing 4.5:1 in floating point hands back a value
 * which, once rounded to `#rrggbb`, measures 4.48, so the module's whole claim would be false by
 * a hundredth on about one colour in twenty, which is exactly the size of error nobody ever finds.
 * Rounding first makes the search solve for the thing that gets painted.
 */
const snap = (rgb: Rgb): Rgb => parseHex(toHex(rgb));

/** The chosen hue and chroma at a given lightness, kept inside sRGB. The chroma is the CHOSEN one
 *  where the gamut allows it: a pale colour stays pale, because somebody who picked a muted teal
 *  did not ask for the most saturated teal a screen can make. */
function at(lightness: number, chroma: number, hue: number): Rgb {
	return snap(
		fromLch([lightness, Math.min(chroma, CHROMA_HEADROOM * chromaCeiling(lightness, hue)), hue])
	);
}

function luminance(rgb: Rgb): number {
	const [r, g, b] = rgb.map(toLinear);
	return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** The WCAG 2.x ratio, 1..21. Written out rather than imported from a package for the reason
 *  `design/colour.ts` gives beside the same formula: a library that computes it slightly
 *  differently would make the floors mean something slightly different from what is quoted. */
export function contrast(a: Rgb, b: Rgb): number {
	const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
	return (high + 0.05) / (low + 0.05);
}

const WHITE: Rgb = [1, 1, 1];

// --- the five rules ----------------------------------------------------------------------------

/**
 * The fill. The chosen colour, moved in lightness only as far as it has to be.
 *
 * TWO FLOORS TOGETHER, and they pull in opposite directions: white sits on this, so it may not be
 * too light; it is also a marker on the canvas, so it may not be too dark. Measured across every
 * base and the whole hue circle, the band where both hold is about 0.47 to 0.60, which is
 * why a derived accent lands beside the shipped six at 0.546 without ever being told to.
 *
 * The search walks outward from the chosen lightness so the answer is the nearest one that works,
 * rather than a fixed lightness that would hand back the same saturated blue whatever shade of blue
 * was picked. If no lightness satisfies both (a hue and chroma where the band closes), the one
 * that comes closest on the worse of the two floors is returned, because a colour somebody chose
 * that is slightly under one floor is a better answer than refusing to have an accent.
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
	// Unreachable while the band above is open, which it is for every hue on every base.
	// Kept because "unreachable" is a measurement of today's bases, and another would be a
	// stylesheet change rather than a change here.
	return best!.rgb;
}

/**
 * The text role. The LOWEST lightness that is legible everywhere it is allowed, then lifted.
 *
 * Lowest first because a word in the accent should still read as the accent, and every step of
 * lightness takes it further towards white. Then lifted to the 9.0:1 on the canvas the six were
 * given, because stopping at the floor makes the one accent nobody solved by hand visibly duller
 * than its siblings, and stopped at `TEXT_CEILING` for the hues that cannot reach it, which is
 * the same exception the gold already carries in the stylesheet.
 */
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
	// Nothing at or under white cleared the card: take white itself, which clears everything a dark
	// base can put behind it. A base light enough for this to matter is not a dark base any more.
	return legible ?? WHITE;
}

/** The tint. The fill mixed into the canvas in OKLab, which is the rule that produced all fifteen
 *  shipped tints: an sRGB mix of the same two colours is muddier, because a mix is an average and
 *  an average is only meaningful in a space where the numbers are perceptual. */
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

/**
 * The whole family, from one chosen colour and the page it is going onto.
 *
 * `chosen` is `#rrggbb`; a value `isHex` refuses has no family and the caller should not ask for
 * one. The three grounds are the base's own, read off the page rather than tabulated here.
 */
export function accentFamily(chosen: string, ground: Ground): AccentFamily {
	const canvas = parseHex(ground.canvas);
	const wanted = toLch(parseHex(chosen));
	const [, chroma, hue] = wanted;

	const fill = fillFor(wanted, canvas);
	const tint = tintFor(fill, canvas);
	const text = textFor(wanted, fill, tint, ground);

	// Between the fill and the word, which is where the shipped rings sit: the ring has to be seen
	// against a chip (3:1) without being as loud as the fill it surrounds.
	const ring = at((toLch(fill)[0] + toLch(text)[0]) / 2, chroma, hue);

	return {
		accent: toHex(fill),
		hover: toHex(at(Math.min(1, toLch(fill)[0] + HOVER_STEP), chroma, hue)),
		text: toHex(text),
		bg: toHex(tint),
		ring: toHex(ring)
	};
}

/* How far apart two colours have to be in OKLab before the eye calls them two colours. About the
   smallest difference anybody can see side by side; anything closer is the derivation's rounding
   and the gamut's headroom, not a change worth a sentence. */
const SEEN_APART = 0.02;

/**
 * How the fill a colour is WORN as differs from the colour that was chosen, or null when it is the
 * same colour to the eye.
 *
 * For a person's kept colours, which are shown as they will be worn on the background in force:
 * the fill can be darker than the colour chosen (white words have to read on it), lighter (it has
 * to stand off the canvas), or the same lightness with less colour in it (the screen cannot hold
 * that much at that lightness). Saying which is the difference between a dot that looks wrong
 * and a dot that says why.
 */
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
