/*
 * A Hidden thing's face reads as out of focus, and its Hidden mark stays sharp.
 *
 * A blur of a flat fill draws a flat fill, which reads as a picture that failed to load. So the
 * ground carries the frost (made-up patches of light and shade, nothing of any file) and the blur
 * is on that layer alone, never on the mark drawn over it.
 */
import { afterEach, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { compile } from 'svelte/compiler';

import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import Withheld from './Withheld.svelte';
import source from './Withheld.svelte?raw';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/** The global stylesheet, where the frost is declared. */
const tokens = readFileSync(resolve('src/app.css'), 'utf8');

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
	removeStyles();
});

/** The compiled rules of this component, as `selector { body }` pairs, scoping class dropped. */
function rules(): { selector: string; body: string }[] {
	const css = (
		compile(source, { filename: 'Withheld.svelte', css: 'external' }).css?.code ?? ''
	).replace(/\/\*[^]*?\*\//g, '');
	return [...css.matchAll(/([^{}]+)\{([^}]*)\}/g)].map((match) => ({
		selector: match[1].replace(/\.svelte-[a-z0-9]+/g, '').trim(),
		body: match[2]
	}));
}

it('blurs the frosted ground on a layer under the mark, and nothing else', () => {
	const all = rules();
	const ground = all.find((rule) => rule.selector === '.withheld::before');

	expect(ground?.body).toMatch(/filter:\s*blur\(var\(--blur-glass\)\)/);
	expect(ground?.body).toMatch(/background:\s*var\(--frost-picture\),\s*var\(--sift-surface-3\)/);
	// Past the box by the blur's reach, so the edge does not fade to what is behind the face.
	expect(ground?.body).toMatch(/inset:\s*calc\(var\(--blur-glass\) \* -3\)/);
	for (const rule of all.filter((one) => one !== ground)) {
		expect(rule.body, rule.selector).not.toMatch(/filter\s*:/);
	}
});

it('draws the mark sharp, over the ground', () => {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(Withheld, { target: host, props: { label: 'Hidden' } });
	flushSync();
	const face = host.querySelector<HTMLElement>('.withheld')!;
	applyStyles(source, face);

	expect(getComputedStyle(face).overflow).toBe('hidden');
	const mark = face.firstElementChild as HTMLElement;
	expect(mark).not.toBeNull();
	expect(getComputedStyle(mark).position).toBe('relative');
	expect(getComputedStyle(mark).filter).not.toMatch(/blur/);
});

it('takes its frost from the one declaration in the global stylesheet', () => {
	expect(tokens.match(/\n\s*--frost-picture:/g)).toHaveLength(1);
	expect(tokens.match(/\n\s*--frost-shade:/g)).toHaveLength(1);
});
