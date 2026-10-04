/** Colour arithmetic, for the gates that measure the token layer.
 *
 * Nothing here is imported by the running app: it exists so `contrast.test.ts` can answer "is this
 * pair legible" from the stylesheet itself rather than from a comment somebody wrote beside it.
 *
 * WCAG 2.x, exactly as written: linearize each channel, weight them, and take the ratio of the two
 * relative luminances with the 0.05 flare term. The formula is worth stating rather than importing,
 * because a library that computes it slightly differently would make the floors mean slightly
 * different things than the ones the design system quotes.
 */

/** Red, green and blue in 0..1, plus alpha. */
export type Rgba = { r: number; g: number; b: number; a: number };

const clamp = (n: number): number => Math.min(1, Math.max(0, n));

/** `#rgb`, `#rrggbb`, `#rrggbbaa`, `rgb(...)`, `rgba(...)` and the one `color-mix` form the token
 *  layer uses. Throws on anything else: a colour the gate cannot read is a colour the gate must
 *  not silently pass. */
export function parseColour(value: string): Rgba {
	const text = value.trim();

	/*
	 * `color-mix(in srgb, <colour> N%, transparent)`, which is how a WASH is written.
	 *
	 * Mixing a colour with `transparent` is exactly "the same colour at N% alpha" (the
	 * transparent side contributes no channels, only the missing alpha), so this is read as that
	 * and nothing more. It is the only `color-mix` in the stylesheet, and it is not read for the
	 * sake of measuring it: the contrast sweep SKIPS a translucent background on purpose, because a
	 * wash has to be composited over whatever is behind it and a rule block does not say what that
	 * is. Without this the sweep would not skip it: it would THROW, on `not a colour`, and take
	 * every base-and-accent case down with it.
	 *
	 * Any other mix still throws, deliberately: two opaque colours mixed is a value this cannot
	 * infer, and guessing at one is worse than refusing.
	 */
	const wash = text.match(
		/^color-mix\(\s*in\s+srgb\s*,\s*(.+?)\s+([\d.]+)%\s*,\s*transparent\s*\)$/i
	);
	if (wash) {
		const base = parseColour(wash[1]);
		return { ...base, a: clamp(base.a * (parseFloat(wash[2]) / 100)) };
	}

	if (text.startsWith('#')) {
		const digits = text.slice(1);
		const wide = digits.length <= 4 ? [...digits].map((c) => c + c).join('') : digits;
		if (wide.length !== 6 && wide.length !== 8) throw new Error(`not a colour: ${value}`);
		const byte = (at: number) => parseInt(wide.slice(at, at + 2), 16) / 255;
		return { r: byte(0), g: byte(2), b: byte(4), a: wide.length === 8 ? byte(6) : 1 };
	}

	const fn = text.match(/^rgba?\(([^)]*)\)$/i);
	if (fn) {
		// Both the comma form and the space form, because the stylesheet uses both.
		const parts = fn[1].split(/[,/\s]+/).filter(Boolean);
		if (parts.length < 3) throw new Error(`not a colour: ${value}`);
		const channel = (raw: string) =>
			raw.endsWith('%') ? clamp(parseFloat(raw) / 100) : clamp(parseFloat(raw) / 255);
		const alpha =
			parts[3] === undefined
				? 1
				: parts[3].endsWith('%')
					? parseFloat(parts[3]) / 100
					: parseFloat(parts[3]);
		return { r: channel(parts[0]), g: channel(parts[1]), b: channel(parts[2]), a: clamp(alpha) };
	}

	throw new Error(`not a colour: ${value}`);
}

/** A translucent colour laid over an opaque one. Source-over, in sRGB, the way a browser paints it. */
export function composite(front: Rgba, back: Rgba): Rgba {
	if (front.a >= 1) return front;
	const keep = 1 - front.a;
	return {
		r: front.r * front.a + back.r * keep,
		g: front.g * front.a + back.g * keep,
		b: front.b * front.a + back.b * keep,
		a: 1
	};
}

const linear = (c: number): number => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);

export function luminance({ r, g, b }: Rgba): number {
	return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
}

/** The WCAG ratio, 1..21. Order does not matter. */
export function contrastRatio(a: Rgba, b: Rgba): number {
	const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
	return (high + 0.05) / (low + 0.05);
}
