/* The opening frame: shown over the window at once, gone when the real page has painted, and the
 * look it is drawn in kept for the next start without ever holding a picture of the library. */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BrowserWindow, paths, resetElectronStub, WebContentsView } from '../test/electron-stub';
import {
	DEFAULT_CANVAS,
	FRAME_WAIT_MS,
	keepLook,
	keptLook,
	lookFrom,
	Opening,
	openingAddress,
	UNSAID_MS
} from './opening';

let folder: string;

beforeEach(() => {
	resetElectronStub();
	folder = fs.mkdtempSync(path.join(os.tmpdir(), 'sift-opening-'));
	paths.userData = folder;
});

afterEach(() => {
	vi.useRealTimers();
	fs.rmSync(folder, { recursive: true, force: true });
});

/* The stub stands in for Electron's window; the frame touches only what the stub has. */
const window = (): BrowserWindow => new BrowserWindow({});
const asWindow = (stub: BrowserWindow): ConstructorParameters<typeof Opening>[0] =>
	stub as unknown as ConstructorParameters<typeof Opening>[0];

describe('the look', () => {
	it('takes a mirrored theme and a hex canvas, and nothing else', () => {
		expect(lookFrom({ theme: '{"base":"slate"}', canvas: '#10141c' })).toEqual({
			theme: '{"base":"slate"}',
			canvas: '#10141c'
		});
		expect(lookFrom({ theme: 'not json', canvas: '#10141c' })).toEqual({
			theme: null,
			canvas: '#10141c'
		});
		expect(lookFrom({ theme: '[1]', canvas: '#10141c' })?.theme).toBeNull();
		expect(lookFrom({ theme: `"${'x'.repeat(5000)}"`, canvas: '#10141c' })?.theme).toBeNull();
		expect(lookFrom({ theme: '{}', canvas: 'red' })).toBeNull();
		expect(lookFrom(null)).toBeNull();
	});

	it('is kept for the next start, and the default before anything was kept', () => {
		expect(keptLook()).toEqual({ theme: null, canvas: DEFAULT_CANVAS });
		expect(keepLook({ theme: '{"base":"paper"}', canvas: '#f4f1ea' })).toBe(true);
		expect(keptLook()).toEqual({ theme: '{"base":"paper"}', canvas: '#f4f1ea' });
		expect(keepLook({ theme: '{"base":"paper"}', canvas: '#f4f1ea' })).toBe(false);
	});

	it('rides in the address fragment, which no request carries', () => {
		expect(openingAddress('sift-shell://app', { theme: '{"a":1}', canvas: '#000000' })).toBe(
			'sift-shell://app/opening#%7B%22a%22%3A1%7D'
		);
		expect(openingAddress('sift-shell://app', { theme: null, canvas: '#000000' })).toBe(
			'sift-shell://app/opening#'
		);
	});
});

describe('the frame', () => {
	it('lies over the whole window in the kept canvas colour, drawn from the shell', async () => {
		const shown = window();
		const opening = new Opening(asWindow(shown), 'sift-shell://app/opening#', '#10141c');
		await opening.drawn();

		const view = WebContentsView.instances[0];
		expect(shown.views).toEqual([view]);
		expect(view?.background).toBe('#10141c');
		expect(view?.bounds).toEqual({ x: 0, y: 0, width: 1400, height: 900 });
		expect(view?.loaded).toEqual(['sift-shell://app/opening#']);
		/* No preload: the frame can ask the shell for nothing. */
		expect(JSON.stringify(view?.options)).not.toContain('preload');
	});

	it('goes when the page has painted, once', () => {
		const shown = window();
		const opening = new Opening(asWindow(shown), 'sift-shell://app/opening#', '#10141c');

		opening.remove('painted');
		opening.remove('painted');

		expect(shown.views).toEqual([]);
		expect(WebContentsView.instances[0]?.closed).toBe(true);
		expect(opening.showing).toBe(false);
	});

	it('goes anyway a moment after a page that never says it painted has loaded', () => {
		vi.useFakeTimers();
		const shown = window();
		const opening = new Opening(asWindow(shown), 'sift-shell://app/opening#', '#10141c');

		opening.pageLoaded();
		vi.advanceTimersByTime(UNSAID_MS - 1);
		expect(opening.showing).toBe(true);
		vi.advanceTimersByTime(1);
		expect(opening.showing).toBe(false);
	});

	it('never holds the window back waiting for its own paint', async () => {
		vi.useFakeTimers();
		const shown = window();
		const opening = new Opening(asWindow(shown), 'sift-shell://app/opening#', '#10141c');
		const view = WebContentsView.instances[0];
		view!.webContents.once = () => undefined;

		const drawn = opening.drawn();
		vi.advanceTimersByTime(FRAME_WAIT_MS);
		await expect(drawn).resolves.toBeUndefined();
	});
});
