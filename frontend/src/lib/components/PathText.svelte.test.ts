/* A path with the profile folder's name hidden draws it covered, where the name was.
 *
 * The server replaces the name with one marker segment; this component is what turns that segment
 * into the filled box a hidden part of a machine's address is drawn with (`Covered`) instead of
 * printing the marker as a word. The address's drawing and not a blur:
 * the same primitive, so the two cannot come to look different.
 */

import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import PathText, { HIDDEN_NAME, pathPieces } from './PathText.svelte';
import source from './PathText.svelte?raw';
import covering from './common/Covered.svelte?raw';
import address from '$lib/settings-ui/ExitAddress.svelte?raw';

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
	removeStyles();
});

function render(path: string): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(PathText, { target: host, props: { path } });
	flushSync();
	return host;
}

describe('the marker', () => {
	it('is the one the server writes', () => {
		const where = readFileSync(resolve('../src/sift/kernel/where.py'), 'utf8');
		expect(where).toContain(`REDACTED = "${HIDDEN_NAME}"`);
	});

	it('is described on the server as the page draws it: covered, never blurred', () => {
		/* The server's note on the marker says how this component draws it, and a reader of that
		   note goes looking for a blur that is not here. The page never blurs a name. */
		const where = readFileSync(resolve('../src/sift/kernel/where.py'), 'utf8');
		const note = /(?:#:.*\n)+REDACTED = /.exec(where)?.[0] ?? '';
		expect(note).toContain('draws it covered');
		expect(note).not.toMatch(/blur/i);
		expect(source).toContain('<Covered>');
	});
});

describe('cutting a path', () => {
	it('finds the hidden segment on either kind of separator', () => {
		expect(pathPieces('C:\\Users\\[redacted]\\Videos\\clip.mp4')).toEqual([
			{ text: 'C:\\Users\\', hidden: false },
			{ text: HIDDEN_NAME, hidden: true },
			{ text: '\\Videos\\clip.mp4', hidden: false }
		]);
		expect(pathPieces('/home/[redacted]/clip.mp4').map((one) => one.hidden)).toEqual([
			false,
			true,
			false
		]);
	});

	it('leaves a folder whose name only contains the marker alone', () => {
		expect(pathPieces('D:\\x[redacted]y\\clip.mp4')).toEqual([
			{ text: 'D:\\x[redacted]y\\clip.mp4', hidden: false }
		]);
	});

	it('leaves a path with nothing hidden as one piece', () => {
		expect(pathPieces('\\\\nas\\share\\clip.mp4')).toEqual([
			{ text: '\\\\nas\\share\\clip.mp4', hidden: false }
		]);
	});
});

describe('drawing it', () => {
	it('draws the hidden segment covered as an address is, never blurred, and keeps the rest as text', () => {
		const where = render('C:\\Users\\[redacted]\\Videos\\clip.mp4');
		const hidden = where.querySelector('.hidden-name');
		expect(hidden).not.toBeNull();
		applyStyles(source, hidden);

		const standIn = where.querySelector('.stand-in')!;
		expect(standIn.getAttribute('aria-hidden')).toBe('true');
		expect(getComputedStyle(standIn).filter).not.toMatch(/blur/);
		const cover = standIn.querySelector('.covered')!;
		expect(cover).not.toBeNull();
		applyStyles(covering, cover);
		expect(getComputedStyle(cover).color).toMatch(/^(transparent|rgba\(0, 0, 0, 0\))$/);
		expect(getComputedStyle(cover).getPropertyValue('background')).toContain('--sift-surface-3');
		expect(getComputedStyle(where.querySelector('.said')!).opacity).toBe('0');
		expect(where.textContent).toContain('C:\\Users\\');
		expect(where.textContent).toContain('\\Videos\\clip.mp4');
	});

	it('is the one drawing the hidden part of an address uses, and neither blurs', () => {
		for (const file of [source, address]) {
			expect(file).toMatch(/import Covered from '\$lib\/components\/common\/Covered\.svelte'/);
			expect(file).toMatch(/<Covered[\s>]/);
			expect(file).not.toMatch(/filter:\s*blur/);
		}
	});

	it('draws a path with nothing hidden exactly as it was said', () => {
		const where = render('D:\\Videos\\clip.mp4');

		expect(where.querySelector('.hidden-name')).toBeNull();
		expect(where.textContent).toBe('D:\\Videos\\clip.mp4');
	});
});
