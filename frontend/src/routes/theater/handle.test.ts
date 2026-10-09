/* The handle at the foot of Theater: the whole mark, in the band under the wall. */
import { afterEach, expect, it } from 'vitest';

import source from './+page.svelte?raw';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';

afterEach(() => {
	removeStyles();
	document.body.replaceChildren();
});

it('is as tall as the band under the wall, with the mark centred in it', () => {
	const handle = document.createElement('span');
	handle.className = 'handle svelte-handle1';
	document.body.append(handle);
	applyStyles(source, handle);

	const style = getComputedStyle(handle);
	expect(style.blockSize).toBe('var(--page-pad)');
	expect(style.alignItems).toBe('center');
});

it('draws a mark small enough to show whole inside that band', () => {
	expect(source).toMatch(
		/<span class="handle" aria-hidden="true">\s*<Icon name="expand_circle_up" size=\{20\} \/>/
	);
});
