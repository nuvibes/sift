/**
 * The boot script paints the first frame in the faces a person chose, even from an older copy.
 *
 * It runs before the module graph exists, so it cannot import the family-to-face table the store
 * reads, and a copy kept by hand is the copy that drifts. `scripts/write_theme_boot.js` writes the
 * table into it from `theme/carried-faces.json` on every `npm run fonts`; this holds the written
 * table to the file, and the script's answer to the store's.
 */

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { beforeEach, describe, expect, it } from 'vitest';

import CARRIED from '$lib/theme/carried-faces.json';
import { BODY_FACES, DISPLAY_FACES, MIRROR_KEY, read } from '$lib/theme/theme.svelte';

const BOOT = resolve(
	dirname(fileURLToPath(import.meta.url)),
	'..',
	'..',
	'..',
	'static',
	'theme-boot.js'
);
const source = () => readFileSync(BOOT, 'utf8');

/** The script, run as the browser runs it: a plain script against this document and storage. */
const boot = () => new Function(source())();

const stamped = () => ({
	faceDisplay: document.documentElement.getAttribute('data-face-display'),
	faceBody: document.documentElement.getAttribute('data-face-body')
});

beforeEach(() => {
	localStorage.clear();
	for (const part of ['base', 'accent', 'face-display', 'face-body']) {
		document.documentElement.removeAttribute(`data-${part}`);
	}
});

describe('the table written into the boot script', () => {
	it('is the one the store reads, not a copy of it', () => {
		const block = /\/\/ BEGIN carried faces[^\n]*\n([\s\S]*?)\/\/ END carried faces/.exec(source());
		expect(block, 'no carried faces block in static/theme-boot.js').not.toBeNull();
		const written = new Function(`${block![1]}; return carried;`)();
		expect(written, 'run `npm run fonts` to write the table again').toEqual({
			display: CARRIED.display,
			body: CARRIED.body
		});
	});

	it('names only faces the menus offer', () => {
		for (const face of Object.values(CARRIED.display)) expect(DISPLAY_FACES).toContain(face);
		for (const face of Object.values(CARRIED.body)) expect(BODY_FACES).toContain(face);
	});
});

describe('the first frame', () => {
	/* Every family in both roles and the older single `face`, each painted by the boot script and
	   read by the store, which must agree: the store repaints a moment later, and any difference
	   between the two is a flash. */
	it.each([
		[{ faceDisplay: 'grotesk', faceBody: 'grotesk' }, 'space-grotesk', 'inter'],
		[{ faceDisplay: 'geist', faceBody: 'archivo' }, 'geist-mono', 'instrument-sans'],
		[{ face: 'manrope' }, 'manrope', 'public-sans'],
		[{ faceDisplay: 'space-grotesk', faceBody: 'dm-sans' }, 'space-grotesk', 'dm-sans']
	])('paints %j as the faces it meant', (stored, display, body) => {
		localStorage.setItem(MIRROR_KEY, JSON.stringify(stored));

		boot();

		expect(stamped()).toEqual({ faceDisplay: display, faceBody: body });
		expect({ faceDisplay: read().faceDisplay, faceBody: read().faceBody }).toEqual(stamped());
	});
});
