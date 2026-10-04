/* Support for the tests, and only for the tests.
 *
 * A mounted component reacts to its props when those props are reactive state, and state can only be
 * declared in a module the compiler treats as one, which a .test.ts file is not. So a test that
 * needs to change a prop after mounting and watch what the component does about it gets its props
 * from here.
 *
 * Nothing in the app imports this, and it is not in the bundle.
 */

import type { FileFacts } from '$lib/player/facts';

/**
 * What a node reads as on screen, with the icon glyphs taken out.
 *
 * An icon in this app is a LIGATURE: the glyph occupies a real character of the element's text, in
 * the font's private use area. So the text of a button showing a mark and a word is not "Filter",
 * it is `\uEF4F Filter`, and neither `trim()` nor collapsing whitespace touches it, because a
 * private-use character is not whitespace. An equality check against the word can therefore never
 * match, on an element where every assertion looks like it should.
 *
 * Prettier reflowing prose across lines lands in `textContent` too, so the whitespace is collapsed
 * with it: without that an assertion is really about where the formatter chose to wrap.
 *
 * Here rather than in each file: it is the kind of helper that gets rewritten slightly differently
 * each time, and a copy that loses the `u` flag off its range no longer reads `\u{...}` as a code point,
 * so it strips no glyph at all.
 */
export function words(node: Element | null | undefined): string {
	return (node?.textContent ?? '')
		.replace(/[\u{E000}-\u{F8FF}]/gu, ' ')
		.replace(/\s+/g, ' ')
		.trim();
}

/**
 * Props a test can assign to after the component is already mounted.
 *
 * ```ts
 * const props = reactiveProps({ items: [a, b] });
 * mount(List, { target, props });
 * props.items = [b, a]; // the component sees it
 * ```
 */
export function reactiveProps<T extends object>(initial: T): T {
	// Declared rather than returned directly: the compiler only accepts $state as the initialiser of
	// a declaration.
	const props = $state(initial);
	return props;
}

/**
 * A file's facts, with only the ones a test is about spelled out.
 *
 * Every one of the eight is sent on every detail response, so the type asks for all of them and a
 * test that cares about the frame rate should not have to write down seven nulls to say so. The
 * base here is those seven nulls: nothing known, which is the honest resting state for a file
 * nobody has read yet.
 */
export function fileFacts(known: Partial<FileFacts> = {}): FileFacts {
	return {
		width: null,
		height: null,
		container: null,
		size_bytes: null,
		vcodec: null,
		acodec: null,
		fps: null,
		bit_depth: null,
		...known
	};
}
