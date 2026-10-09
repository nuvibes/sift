/* Support for the tests, and only for the tests. */

import type { FileFacts } from '$lib/player/facts';

/** What a node reads as on screen, with the icon glyphs taken out. */
export function words(node: Element | null | undefined): string {
	return (node?.textContent ?? '')
		.replace(/[\u{E000}-\u{F8FF}]/gu, ' ')
		.replace(/\s+/g, ' ')
		.trim();
}

/** Props a test can assign to after the component is already mounted. */
export function reactiveProps<T extends object>(initial: T): T {
	// Declared rather than returned directly: the compiler only accepts $state as the initialiser
	// of a declaration.
	const props = $state(initial);
	return props;
}

/** A file's facts, with only the ones a test is about spelled out. */
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
