/*
 * A covered run of text is painted over, and the frost on it is a look, never a blur of the text.
 *
 * The box wears the veils' softness (an edge faded along a gaussian and a blurred band of light)
 * so it reads as a line of text out of focus rather than a hard tile. What must never follow from
 * that is the text itself becoming partly legible: it stays transparent on an opaque ground, and
 * the only thing blurred is the made-up light on a layer of its own.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
import { compile } from 'svelte/compiler';

import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import Covered from './Covered.svelte';
import source from './Covered.svelte?raw';
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

function render(shown: boolean): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	const children = createRawSnippet(() => ({ render: () => '<span>0.2.15:5171</span>' }));
	mounted = mount(Covered, { target: host, props: { shown, children } });
	flushSync();
	const box = host.querySelector<HTMLElement>('.covered')!;
	applyStyles(source, box);
	return box;
}

/** The compiled rules of this component, as `selector { body }` pairs, scoping class dropped. */
function rules(): { selector: string; body: string }[] {
	const css = (
		compile(source, { filename: 'Covered.svelte', css: 'external' }).css?.code ?? ''
	).replace(/\/\*[^]*?\*\//g, '');
	return [...css.matchAll(/([^{}]+)\{([^}]*)\}/g)].map((match) => ({
		selector: match[1].replace(/\.svelte-[a-z0-9]+/g, '').trim(),
		body: match[2]
	}));
}

describe('covered', () => {
	it('draws the text transparent on an opaque ground, with the frost edge', () => {
		const box = render(false);
		const style = getComputedStyle(box);

		expect(style.color).toMatch(/^(transparent|rgba\(0, 0, 0, 0\))$/);
		expect(style.getPropertyValue('background')).toContain('--sift-surface-3');
		expect(style.getPropertyValue('mask-image')).toContain('--frost-edge');
		expect(box.textContent).toBe('0.2.15:5171');
	});

	it('blurs only the light on its own layer, never the box or the text in it', () => {
		const all = rules();
		const onTheBox = all.filter((rule) => !/::(after|before)/.test(rule.selector));
		for (const rule of onTheBox) {
			expect(rule.body, rule.selector).not.toMatch(/filter\s*:/);
			for (const shadow of rule.body.matchAll(/text-shadow:\s*([^;]+)/g)) {
				expect(shadow[1].trim(), rule.selector).toBe('none');
			}
		}

		const light = all.find((rule) => rule.selector === '.covered::after');
		expect(light?.body).toMatch(/filter:\s*blur\(var\(--blur-veil\)\)/);
		expect(light?.body).toMatch(/background:\s*var\(--frost-line\)/);
	});
});

describe('shown', () => {
	it('draws the text as it is, with no ground, no edge and no light', () => {
		const box = render(true);
		const style = getComputedStyle(box);

		expect(style.color).not.toMatch(/^(transparent|rgba\(0, 0, 0, 0\))$/);
		expect(style.getPropertyValue('background')).toBe('none');
		expect(style.getPropertyValue('mask-image')).toBe('none');
		const off = rules().find((rule) => rule.selector === '.covered.shown::after');
		expect(off?.body).toMatch(/content:\s*none/);
	});
});

describe('the frost', () => {
	it('is declared once in the global stylesheet, beside the veil', () => {
		for (const name of [
			'--frost-fade',
			'--frost-ramp',
			'--frost-edge',
			'--frost-light',
			'--frost-line'
		]) {
			expect(tokens.match(new RegExp(`\\n\\s*${name}:`, 'g')), name).toHaveLength(1);
		}
		// Built on the veil's blur, so a softer or harder veil moves the cover with it.
		expect(tokens).toMatch(/--frost-fade:\s*calc\(var\(--blur-veil\) \* 2\)/);
		// A few percent of the ink: a light, never a colour that could carry anything.
		expect(tokens).toMatch(
			/--frost-light:\s*color-mix\(in srgb, var\(--sift-ink\) \d%, transparent\)/
		);
	});
});
