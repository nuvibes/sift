/**
 * Where a moment sits along a slider's track, in pixels and back again.
 *
 * A range input's handle travels from half a thumb in to half a thumb from the end, so anything
 * drawn on the same track (the fill, the loop brackets, the replay curve, the frame under the
 * pointer) uses that travel too. The thumb's size comes from the `--slider-thumb` token.
 */

/** The slider handle's width in pixels, from the token every slider is drawn with. */
export function thumbWidth(within: Element): number {
	const held = getComputedStyle(within).getPropertyValue('--slider-thumb');
	const found = Number.parseFloat(held);
	// A stylesheet not loaded yet answers an empty string: zero means no correction, not a guess.
	return Number.isFinite(found) ? found : 0;
}

/**
 * Where a fraction of the way along the track is, as a length to put in a style.
 *
 * Built per element, not as a `:root` custom property, because a token holding `var()` is
 * substituted where it is declared (the note beside the tokens in `app.css`). A string because the
 * arithmetic mixes a CSS length with a percentage only the browser can measure.
 */
export function alongTrack(fraction: number): string {
	const along = Math.min(Math.max(fraction, 0), 1);
	return `calc(var(--slider-thumb) / 2 + (100% - var(--slider-thumb)) * ${along})`;
}

/**
 * How far along the handle's travel a point on the track is, from 0 to 1. The way back.
 *
 * The frame under the pointer must be the moment a press there would seek to. Clamped, because a
 * press past either end of the travel means the first or the last moment.
 */
export function fractionAt(across: number, width: number, thumb: number): number {
	const travel = width - thumb;
	if (travel <= 0) return 0;
	return Math.min(Math.max((across - thumb / 2) / travel, 0), 1);
}
