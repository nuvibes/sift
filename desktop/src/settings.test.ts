/* The one file that survives between launches, and what happens when it is wrong.
 *
 * It is a plain JSON file in the user's profile, so every case below is reachable by hand: somebody
 * edits it, a disk fills up mid-write, a backup restores half of it. A truncated settings file is
 * indistinguishable from a fresh install, and a fresh install re-points somebody's library, which
 * is why the write is temp-then-rename and why that is asserted here rather than assumed.
 */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { paths as stubPaths, resetElectronStub } from '../test/electron-stub';
import {
	load,
	locations,
	mayShare,
	save,
	withoutMode,
	type DesktopSettings
} from './settings';
import { settingsFile } from './paths';

let profile: string;

beforeEach(() => {
	resetElectronStub();
	profile = fs.mkdtempSync(path.join(os.tmpdir(), 'sift-desktop-'));
	stubPaths.userData = profile;
	stubPaths.appData = path.join(profile, 'Roaming');
});

afterEach(() => {
	fs.rmSync(profile, { recursive: true, force: true });
});

function write(contents: string): void {
	fs.writeFileSync(settingsFile(), contents, 'utf8');
}

describe('load', () => {
	it('reads a fresh install as first run rather than as an error', () => {
		const settings = load();
		expect(settings.mode).toBeNull();
		expect(settings.dataDir).toBeNull();
		expect(settings.servers).toEqual([]);
	});

	it('reads a file that is not JSON as first run', () => {
		write('{ this is not json');
		expect(load().mode).toBeNull();
	});

	/* The setting that decides whether this is a program on one computer or a service on a network.
	 *
	 * A fresh install must be the former, and nothing short of the word `true` may make it the
	 * latter. This file is a plain, hand-editable JSON file, and every other setting here can
	 * afford to be forgiving about a string where a boolean was meant. This one cannot, because
	 * being talked into it by a truthy value is the difference between a machine that answers only
	 * to itself and one that answers to the whole house.
	 */
	it('does not share on the network unless it has been asked to, in so many words', () => {
		expect(load().shareOnNetwork).toBe(false);

		for (const written of ['"true"', '1', '"yes"', '{}', '[]', 'null', '"false"']) {
			write(`{"version":1,"mode":"standalone","shareOnNetwork":${written}}`);
			expect(load().shareOnNetwork, `shareOnNetwork: ${written}`).toBe(false);
		}

		write('{"version":1,"mode":"standalone","shareOnNetwork":true}');
		expect(load().shareOnNetwork).toBe(true);
	});

	/*
	 * The browser links open in, and the reason it is checked at all.
	 *
	 * The value is a path this application will later START AS A PROGRAM. Everything else in this
	 * file describes Sift to itself; this one names something outside it to run. So the same rule
	 * the sharing switch gets applies here: a hand-edited file may say a string or it may say
	 * nothing, and anything else means nothing.
	 *
	 * `null` is what "no choice" has to be. Recording the system default as a path would freeze it,
	 * and somebody who later changed their Windows default would find Sift still opening the old
	 * one, under a setting that says "Default browser" and does not mean it.
	 */
	it('takes a browser only as a string, and treats anything else as no choice', () => {
		expect(load().browser).toBeNull();

		for (const written of ['1', 'true', '{}', '[]', 'null', '""']) {
			write(`{"version":1,"mode":"standalone","browser":${written}}`);
			expect(load().browser, `browser: ${written}`).toBeNull();
		}

		write('{"version":1,"mode":"standalone","browser":"C:\\\\gc\\\\chrome.exe"}');
		expect(load().browser).toBe('C:\\gc\\chrome.exe');
	});

	it('keeps what a good file says', () => {
		write(
			JSON.stringify({
				version: 1,
				mode: 'client',
				dataDir: 'D:\\Sift\\data',
				cacheDir: 'D:\\Sift\\cache',
				servers: [{ label: 'the study', origin: 'http://10.0.0.5:5171' }],
				lastServer: 'http://10.0.0.5:5171'
			})
		);
		const settings = load();
		expect(settings.mode).toBe('client');
		expect(settings.dataDir).toBe('D:\\Sift\\data');
		expect(settings.servers).toEqual([{ label: 'the study', origin: 'http://10.0.0.5:5171' }]);
	});

	/* Hand-editable, so the shape is trusted nowhere. A wrong type here would surface much later as
	 * an unreadable error in whichever screen happened to read it first. */
	it('drops a saved server that is not one, and keeps the ones that are', () => {
		write(
			JSON.stringify({
				servers: [{ label: 'ok', origin: 'http://x:5171' }, 'nonsense', { label: 5 }, null]
			})
		);
		expect(load().servers).toEqual([{ label: 'ok', origin: 'http://x:5171' }]);
	});

	it('reads a servers field that is not a list as no servers', () => {
		write(JSON.stringify({ servers: 'http://x:5171' }));
		expect(load().servers).toEqual([]);
	});

	/* THE DEFENSIVE READING GOES TOWARDS THE SETTING'S OWN DEFAULT, which for this one is ON and
	 * for `shareOnNetwork` above is off. Both rules exist for the same reason (the file is
	 * hand-editable), and reading them as one rule is how a switch ends up defaulting the wrong
	 * way: a copy of Sift whose file predates this field would stop mid-scan on every window close. */
	it('keeps Sift running when the file says nothing at all about it', () => {
		write('{"version":1,"mode":"standalone"}');
		expect(load().keepRunningWhenClosed).toBe(true);
	});

	it('takes only an exact false for off', () => {
		for (const written of ['0', '"false"', 'null', '"no"', '[]']) {
			write(`{"version":1,"keepRunningWhenClosed":${written}}`);
			expect(load().keepRunningWhenClosed, `keepRunningWhenClosed: ${written}`).toBe(true);
		}
		write('{"version":1,"keepRunningWhenClosed":false}');
		expect(load().keepRunningWhenClosed).toBe(false);
	});

	/* Each entry names two folders this application will later start a backend on, so the shape is
	 * trusted nowhere: the same rule the saved servers are filtered by, and for a stronger reason. */
	it('drops a remembered library that is not one, and keeps the ones that are', () => {
		const good = { dataDir: 'D:\\L\\data', cacheDir: 'D:\\L\\cache', name: 'L', lastOpened: 7 };
		write(
			JSON.stringify({
				libraries: [
					good,
					'nonsense',
					null,
					{ dataDir: '', cacheDir: 'x', name: 'y', lastOpened: 1 },
					{ dataDir: 'x', cacheDir: 'y', name: 'z', lastOpened: 'lately' }
				]
			})
		);
		expect(load().libraries).toEqual([good]);
	});

	it('reads a libraries field that is not a list as none', () => {
		write(JSON.stringify({ libraries: 'D:\\L\\data' }));
		expect(load().libraries).toEqual([]);
	});

	it('answers with the current version whatever the file claims', () => {
		write(JSON.stringify({ version: 99, mode: 'standalone' }));
		expect(load().version).toBe(1);
	});
});

describe('save', () => {
	const settings: DesktopSettings = {
		version: 1,
		mode: 'standalone',
		dataDir: 'D:\\data',
		cacheDir: 'D:\\cache',
		servers: [],
		lastServer: null,
		feedUrl: null,
		shareOnNetwork: false,
		browser: null,
		downloadDir: null,
		keepRunningWhenClosed: true,
		libraries: []
	};

	it('writes what load then reads', () => {
		save(settings);
		expect(load()).toEqual(settings);
	});

	it('leaves no temporary file behind', () => {
		save(settings);
		expect(fs.existsSync(`${settingsFile()}.tmp`)).toBe(false);
	});

	it('makes the folder if the profile does not have one yet', () => {
		const nested = path.join(profile, 'not', 'made', 'yet');
		stubPaths.userData = nested;
		save(settings);
		expect(fs.existsSync(path.join(nested, 'desktop.json'))).toBe(true);
	});
});

describe('locations', () => {
	it('uses what first run settled', () => {
		const chosen = locations({ ...load(), dataDir: 'E:\\d', cacheDir: 'E:\\c' });
		expect(chosen).toEqual({ dataDir: 'E:\\d', cacheDir: 'E:\\c' });
	});

	it('falls back to the defaults until it has', () => {
		const chosen = locations(load());
		expect(chosen.dataDir.endsWith(path.join('Local', 'Sift', 'data'))).toBe(true);
	});

	/* Half-settled is reachable: the two questions are asked separately, and somebody can close the
	 * window between them. The unanswered half falls back rather than becoming undefined, which
	 * would be handed to the backend as a literal "undefined" folder. */
	it('falls back for one half while keeping the other', () => {
		const chosen = locations({ ...load(), dataDir: 'E:\\d' });
		expect(chosen.dataDir).toBe('E:\\d');
		expect(chosen.cacheDir.endsWith(path.join('Local', 'Sift', 'cache'))).toBe(true);
	});
});

describe('the sharing switch belongs to a server', () => {
	it('is refused for a copy that is a client, or not yet set up', () => {
		expect(mayShare('client')).toBe(false);
		expect(mayShare(null)).toBe(false);
		expect(mayShare('standalone')).toBe(true);
	});

	/* A machine used as a client and then set up as a server must not boot on every address
	 * unasked: unsaying the mode takes the sharing switch with it.
	 */
	it('goes when the mode is unsaid, with the server last joined', () => {
		const forgotten = withoutMode({
			...load(),
			mode: 'standalone',
			lastServer: 'http://10.0.0.5:5171',
			shareOnNetwork: true
		});
		expect(forgotten.mode).toBeNull();
		expect(forgotten.lastServer).toBeNull();
		expect(forgotten.shareOnNetwork).toBe(false);
	});
});
