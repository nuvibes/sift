/* A form on a settings pane is drawn as the pane's own rows: no bordered card, each field a row
 * with its box in the wide control column, a hairline between two fields, and the form marked as
 * a ruled row so the row after it draws the line between them. */
import { afterEach, expect, it } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
import { compile } from 'svelte/compiler';
import FormCard from './FormCard.svelte';
import source from './FormCard.svelte?raw';

const css = compile(source, { filename: 'FormCard.svelte', css: 'external' }).css?.code ?? '';

let drawn: Record<string, unknown> | null = null;
afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	document.body.innerHTML = '';
});

it('draws no box of its own around the form', () => {
	const card = /\.card\.svelte-[a-z0-9]+ \{([^}]*)\}/.exec(css)?.[1] ?? '';
	expect(card).not.toBe('');
	expect(card).not.toMatch(/border:|background:/);
});

it('lays each field as a row, the box in the wide control column', () => {
	expect(css).toMatch(
		/\.field:not\(:has\(textarea\)\) \{[^}]*grid-template-columns: minmax\(0, 1fr\) var\(--settings-control-col-wide/
	);
	expect(css).toMatch(/\.field ~ \.field \{\s*border-block-start: 1px solid var\(--sift-line\)/);
});

it('wears the ruled-row mark, so the row after it draws the line between them', () => {
	const host = document.createElement('div');
	document.body.append(host);
	drawn = mount(FormCard, {
		target: host,
		props: {
			onsubmit: () => {},
			children: createRawSnippet(() => ({ render: () => '<div class="field"></div>' }))
		}
	}) as Record<string, unknown>;
	flushSync();
	expect(host.querySelector('form')?.classList.contains('ruled-row')).toBe(true);
});
