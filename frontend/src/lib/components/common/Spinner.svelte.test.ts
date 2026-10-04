import { afterEach, describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { flushSync, mount, unmount } from 'svelte';

import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import Spinner from './Spinner.svelte';

/*
 * A spinner is the size it says it is.
 *
 * The size classes set `inline-size` and a `border-width`; without `box-sizing: border-box` the
 * default `content-box` adds the border outside the declared box, and every rung draws about five
 * pixels wider than its name. The number in the markup is right while the number on screen is not.
 *
 * A source test, like `pills.test.ts`: the failure is a missing declaration, which renders
 * perfectly well, and jsdom lays nothing out, so a mounted spinner reports the same geometry either
 * way.
 */

const SOURCE = readFileSync(
	join(dirname(fileURLToPath(import.meta.url)), 'Spinner.svelte'),
	'utf8'
);

describe('under reduced motion', () => {
	/* Nothing repeats under reduced motion, so the arc stops; it is drawn as a whole ring rather
	   than a still arc, which reads as a ring with a piece missing. */
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

		// The whole ring is a logical border colour, which this environment does not compute; the
		// gallery draws it.
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
		// The pair is what makes the rule mean anything: border-box with no border is a circle of
		// nothing, and a border with no border-box is the fault above.
		expect(SOURCE).toContain('border-style: solid;');
		// The width comes through a custom property each rung sets, so the one rule reads the same
		// for every size; the rung is where the number is.
		expect(SOURCE).toMatch(/border-width:\s*var\(--spinner-border\);/);
		expect(SOURCE).toMatch(/--spinner-border:\s*[\d.]+px;/);
	});
});
