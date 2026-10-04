/*
 * A sheet is centred on, and capped by, the window BELOW the desktop app's title strip.
 *
 * The strip (`--window-chrome`, zero in a browser) is drawn over the page above every layer, so a
 * sheet centred on the whole window and capped at its whole height would slide under it: a tall
 * sheet (the cookies list, a long cover chooser) would lose its top edge and corners beneath the
 * strip and read as cut off. It happens only in the desktop app, so a browser never shows it.
 */

import { afterEach, describe, expect, it } from 'vitest';
import { createRawSnippet, flushSync, mount } from 'svelte';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import Modal from './Modal.svelte';

/** The shared `.sheet` rule, as the app's stylesheet declares it. */
function sheetRule(): string {
	const css = readFileSync(resolve('src/app.css'), 'utf8');
	const found = /(?:^|\n)\.sheet \{([^}]*)\}/.exec(css);
	if (!found) throw new Error('app.css declares no top-level .sheet rule');
	return found[1];
}

function declared(rule: string, property: string): string {
	const found = new RegExp(`(?:^|[;\\s])${property}:\\s*([^;]+);`).exec(rule);
	return found ? found[1].trim() : '';
}

let style: HTMLStyleElement | null = null;

afterEach(() => {
	style?.remove();
	style = null;
	document.body.innerHTML = '';
});

describe('the shared sheet rule', () => {
	it('centres the sheet on the room below the title strip', () => {
		expect(declared(sheetRule(), 'top')).toMatch(/var\(--window-chrome\)/);
	});

	it('caps its height by the room below the title strip', () => {
		const cap = declared(sheetRule(), 'max-block-size');
		expect(cap).toMatch(/100dvh/);
		expect(cap).toMatch(/var\(--window-chrome\)/);
	});

	it('lands on the element every dialog draws', () => {
		style = document.createElement('style');
		style.textContent = `.sheet {${sheetRule()}}`;
		document.head.append(style);

		const host = document.createElement('div');
		document.body.append(host);
		mount(Modal, {
			target: host,
			props: {
				open: true,
				title: 'Cookies',
				description: 'A tall sheet.',
				children: createRawSnippet(() => ({ render: () => '<p>contents</p>' }))
			} as never
		});
		flushSync();

		const sheet = document.querySelector('.sheet') as HTMLElement;
		expect(sheet).not.toBeNull();
		expect(getComputedStyle(sheet).position).toBe('fixed');
	});
});
