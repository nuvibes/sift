/* The window's own buttons, painted in the colours the application is painting itself in.
 *
 * ## What this is for
 *
 * The desktop window is created with its operating-system title bar HIDDEN and the minimise,
 * maximise and close overlaid on the page, so they sit inside Sift's own top bar instead of on a
 * strip of Windows grey above it. That is what makes the window look like one application rather
 * than an application inside somebody else's frame.
 *
 * The strip the buttons sit in has a colour, and only the page knows what it should be: a person can
 * pick between the bases and the accents while the window is open, and the shell that created the
 * window cannot see any of it. So the page reads what it is actually drawing and says so.
 *
 * ## Why the colour is READ rather than known
 *
 * The obvious alternative is a small table here mapping each base to its top-bar colour. That is a
 * second copy of something `app.css` already declares, and the two would drift the first time a
 * surface was retuned, with the failure being caption buttons that are almost the right colour,
 * which is worse to look at than plainly wrong ones. `getComputedStyle` on the root asks the
 * stylesheet the same question the bar itself asks, so there is one answer.
 *
 * ## Nothing happens in a browser
 *
 * There is no shell, so `canDressTitleBar` is false and this does nothing at all. It is not an error
 * and there is no fallback to arrange: a browser tab has no caption buttons of its own to paint.
 */

import { bridge } from '$lib/bridge';

/** The two things a caption button is: its ground, and the mark drawn on it. */
const GROUND = '--sift-surface-1';
const MARK = '--sift-ink';

/**
 * A browser's `rgb(r, g, b)` as the hex the shell will accept, or null for anything that is not a
 * plain opaque colour.
 *
 * The shell refuses anything that is not hex, so this is where a browser's answer is turned into
 * one: `getComputedStyle` resolves a custom property to whatever was written, and every colour in
 * `app.css` is already hex, but a computed VALUE comes back in the browser's own serialisation
 * rather than the source text.
 *
 * Null rather than a guess when it cannot be read. A colour that could not be worked out leaves the
 * buttons as the shell drew them, which is a sensible dark; inventing one would be a confident
 * wrong answer painted onto the window.
 */
export function asHex(value: string): string | null {
	const said = value.trim();
	if (/^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(said)) return said;
	const parts = said.match(/^rgba?\(([^)]+)\)$/);
	if (!parts) return null;
	const tokens = parts[1].split(/[\s,/]+/).filter((one) => one !== '');
	const numbers = tokens.map((one) => Number.parseFloat(one));
	if (numbers.length < 3 || numbers.slice(0, 3).some((one) => !Number.isFinite(one))) return null;
	/*
	 * A partly transparent caption bar is not something the site can draw, and a colour Sift
	 * asked for and did not get is worse than the one already there.
	 *
	 * The alpha is read out of its TOKEN rather than out of the parsed number, because the two
	 * modern spellings disagree about scale: `rgba(30, 32, 36, 0.5)` says a half as `0.5` and
	 * `rgb(30 32 36 / 50%)` says it as `50%`. Parsed alone the second is fifty, which is not less
	 * than one. So a half-transparent colour would read as opaque and go to the window. The test
	 * below holds both spellings.
	 */
	if (tokens.length > 3) {
		const raw = tokens[3];
		const alpha = raw.endsWith('%') ? numbers[3] / 100 : numbers[3];
		if (!Number.isFinite(alpha) || alpha < 1) return null;
	}
	const hex = numbers
		.slice(0, 3)
		.map((one) =>
			Math.max(0, Math.min(255, Math.round(one)))
				.toString(16)
				.padStart(2, '0')
		)
		.join('');
	return `#${hex}`;
}

/**
 * Read what the top bar is drawn in and tell the shell to match it.
 *
 * Safe to call whenever the theme might have moved: it is two style reads and one message, and the
 * shell simply answers false where there is nothing to paint.
 */
export async function dressTitleBar(): Promise<boolean> {
	if (typeof document === 'undefined' || !bridge.canDressTitleBar()) return false;
	const style = getComputedStyle(document.documentElement);
	const color = asHex(style.getPropertyValue(GROUND));
	const symbolColor = asHex(style.getPropertyValue(MARK));
	if (color === null || symbolColor === null) return false;
	return bridge.dressTitleBar({ color, symbolColor });
}

/**
 * Whether this window has the caption buttons drawn over its page.
 *
 * Stamped on the root as `data-window="overlaid"` so the stylesheet can answer it: the top bar has
 * to leave room for the buttons at its right-hand end and become the thing you drag the window by,
 * and neither of those may happen in a browser, where the same rule would swallow clicks on the
 * bar's own controls.
 *
 * An attribute rather than a media query because there is no media query for this. `env(titlebar-area-*)`
 * exists and gives the geometry, and it is not a switch: it resolves to its fallback in a browser,
 * so a rule guarded only by `env()` still applies everywhere.
 */
export function markOverlaidWindow(): void {
	if (typeof document === 'undefined') return;
	if (bridge.canDressTitleBar()) document.documentElement.setAttribute('data-window', 'overlaid');
}
