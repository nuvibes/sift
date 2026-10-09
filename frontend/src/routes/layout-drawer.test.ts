/* A drawer beside the page (swap mode's) says how wide it is, and the main column keeps clear of
   it. */
import { compile } from 'svelte/compiler';
import { expect, it } from 'vitest';

import source from './+layout.svelte?raw';

it('keeps a full-bleed screen clear of a drawer beside the page', () => {
	const css = compile(source, { filename: '+layout.svelte', css: 'external' })
		.css!.code.replace(/\/\*[\s\S]*?\*\//g, '')
		.replace(/\.svelte-[\w-]+/g, '');
	const rules = [...css.matchAll(/([^{}]+)\{([^}]*)\}/g)];
	const bleed = rules.find((one) => one[1].trim() === 'main.full-bleed');
	expect(bleed?.[2]).toBeDefined();
	const body = bleed![2];
	// The drawer's room is declared after the reset, so the reset cannot take it back.
	expect(body.indexOf('padding-inline-end: var(--drawer-beside, 0px)')).toBeGreaterThan(
		body.indexOf('padding: 0')
	);
});
