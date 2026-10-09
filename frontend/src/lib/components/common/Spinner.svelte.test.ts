import { afterEach, describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { flushSync, mount, unmount } from 'svelte';

import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import Spinner from './Spinner.svelte';

/* A spinner is the size it says: `border-box`, or the border adds about five pixels; a source
 * test, as jsdom lays nothing out. */

const SOURCE = readFileSync(
	join(dirname(fileURLToPath(import.meta.url)), 'Spinner.svelte'),
	'utf8'
);

describe('under reduced motion', () => {
	/* Under reduced motion the arc stops, drawn as a whole ring. */
	let drawn: Record<string, unknown> | undefined;
	let host: HTMLElement | undefined;

	afterEach(() => {
		if (drawn) unmount(drawn);
		host?.remove();
		removeStyles();
		delete document.documentElement.dataset.motion;
	});

	function spinner(): HTMLElement {
		host = document.createElement('div');
		document.body.append(host);
		drawn = mount(Spinner, { target: host, props: { label: 'Checking' } });
		flushSync();
		const arc = host.querySelector('.spinner') as HTMLElement;
		applyStyles(SOURCE, arc);
		return arc;
	}

	it('turns while motion is on', () => {
		expect(getComputedStyle(spinner()).animation).toContain('turn');
	});

	it('is still when it is off', () => {
		document.documentElement.dataset.motion = 'reduce';
		const arc = spinner();

		// The ring's colour is not computed here; the gallery draws it.
		expect(getComputedStyle(arc).animation).toBe('none');
	});
});

describe('the arc', () => {
	it('counts its border inside its size', () => {
		const rule = SOURCE.slice(
			SOURCE.indexOf('\n\t.spinner {'),
			SOURCE.indexOf('\n\t}', SOURCE.indexOf('\n\t.spinner {'))
		);

		expect(rule, 'the border is drawn outside the declared size').toContain(
			'box-sizing: border-box;'
		);
	});

	it('still declares a border to draw the arc with', () => {
		// The pair is what makes the rule mean anything.
		expect(SOURCE).toContain('border-style: solid;');
		// One rule reads each rung's custom property.
		expect(SOURCE).toMatch(/border-width:\s*var\(--spinner-border\);/);
		expect(SOURCE).toMatch(/--spinner-border:\s*[\d.]+px;/);
	});
});
