/* The dialog after Sift has stopped: what went wrong and what to do, the start without the optional
 * features, the log archive, and choosing the library's place again only when it isn't there. The
 * archive maker and the network-drive question are stood in for. */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
	appCalls,
	dialog,
	dialogAnswers,
	dialogCalls,
	resetElectronStub,
	revealed
} from '../test/electron-stub';
import type { Bundled } from './logbundle';
import { fresh, type DesktopSettings } from './settings';
import { FAULTED, libraryMissing, NEVER_STARTED, wayBack, whatItMeans } from './wayback';

const bundle = vi.hoisted(() => ({ asked: [] as unknown[], answer: null as unknown }));
vi.mock('./logbundle', () => ({
	bundleLogs: async (ask: unknown) => {
		bundle.asked.push(ask);
		return bundle.answer as Bundled;
	}
}));
const drive = vi.hoisted(() => ({ remote: false }));
vi.mock('./drives', async (original) => ({
	...(await original<typeof import('./drives')>()),
	isRemoteDrive: async () => drive.remote
}));

let folder = '';
let settings: DesktopSettings;
const saved: DesktopSettings[] = [];
const startAgain = vi.fn();

function way() {
	return wayBack({
		settings: () => settings,
		save: (next) => {
			saved.push(next);
			settings = next;
		},
		startAgain
	});
}

const settle = () => new Promise((done) => setTimeout(done, 5));

/** This copy's record of having opened the library at the set-up place. */
const opened = () => [
	{ dataDir: settings.dataDir ?? '', cacheDir: settings.cacheDir ?? '', name: 'A', lastOpened: 1 }
];

beforeEach(() => {
	resetElectronStub();
	folder = fs.mkdtempSync(path.join(os.tmpdir(), 'sift-wayback-'));
	const dataDir = path.join(folder, 'data');
	fs.mkdirSync(dataDir);
	fs.writeFileSync(path.join(dataDir, 'sift.sqlite3'), 'a library');
	settings = { ...fresh(), mode: 'standalone', dataDir, cacheDir: path.join(folder, 'cache') };
	saved.length = 0;
	startAgain.mockClear();
	drive.remote = false;
	bundle.asked.length = 0;
	bundle.answer = { ok: true, file: 'C:\\Saved\\Sift log.zip' };
});

afterEach(() => {
	fs.rmSync(folder, { recursive: true, force: true });
});

describe('what an exit code means', () => {
	it('says a sentence for the codes Windows ends a program with when it could not start or faulted', () => {
		for (const code of NEVER_STARTED) expect(whatItMeans(code)).toContain("couldn't start");
		for (const code of FAULTED) expect(whatItMeans(code)).toContain('inside one of the libraries');
		expect(whatItMeans(-1073741819)).toContain('inside one of the libraries');
		expect(whatItMeans(1)).toBe('');
		expect(whatItMeans(null)).toBe('');
	});

	/* The Python tree words the same codes for a tool that never started; the two say one thing. */
	it("covers every code the Python tree says couldn't start, in its words", () => {
		const source = fs.readFileSync(
			path.join(__dirname, '..', '..', 'src', 'sift', 'kernel', 'subprocess.py'),
			'utf8'
		);
		const codes = /_NEVER_STARTED = frozenset\(\{([^}]*)\}\)/.exec(source)?.[1] ?? '';
		const listed = codes.split(',').map((one) => Number(one.trim()));
		expect(listed.length).toBeGreaterThan(0);
		for (const code of listed) expect(NEVER_STARTED.has(code)).toBe(true);
		const words = /NEVER_STARTED = "it ([^"]*)"/.exec(source)?.[1] ?? 'missing';
		expect(whatItMeans(0xc0000135)).toContain(`It ${words}.`);
	});
});

describe('whether the library is where this copy was told', () => {
	it('is, or says why not: no library where one was opened, or the folder refused', async () => {
		expect(await libraryMissing(settings)).toBeNull();
		fs.rmSync(path.join(settings.dataDir ?? '', 'sift.sqlite3'));
		/* Never opened: a new library, whose database comes with its first start. */
		expect(await libraryMissing(settings)).toBeNull();
		settings = { ...settings, libraries: opened() };
		expect(await libraryMissing(settings)).toBe(`There's no Sift library in ${settings.dataDir}.`);
		drive.remote = true;
		expect(await libraryMissing({ ...settings, libraries: [] })).toContain('network');
		expect(await libraryMissing({ ...settings, mode: 'client' })).toBeNull();
		expect(await libraryMissing({ ...settings, dataDir: null })).toBeNull();
	});
});

describe('the way back', () => {
	it('says what to do and offers no setup when the library is there', async () => {
		const back = way();
		dialogAnswers.push(1);

		back.offer('Sift could not start', 'boom', false, 0xc0000005);
		await settle();

		expect(dialogCalls[0]?.buttons).toEqual(['Download log', 'Quit']);
		expect(dialogCalls[0]?.detail).toContain('github.com/nuvibes/sift/issues');
		expect(appCalls.quit).toBe(1);
		expect(saved).toEqual([]);
	});

	it('holds the optional features off once asked, says so, and offers it no more', async () => {
		const back = way();
		back.started();
		expect(dialogCalls).toHaveLength(0);
		dialogAnswers.push(0);

		back.offer('Sift has stopped', 'it stopped four times', true);
		await settle();

		expect(dialogCalls[0]?.buttons).toEqual(['Start without them', 'Download log', 'Quit']);
		expect(back.holding()).toBe(true);
		expect(startAgain).toHaveBeenCalledOnce();
		back.started();
		expect(dialogCalls[1]?.message).toBe(
			'Sift started without face recognition, Smart Search and watermark reading'
		);
		dialogAnswers.push(1);
		back.offer('Sift has stopped', 'and again', true);
		await settle();
		expect(dialogCalls[2]?.buttons).toEqual(['Download log', 'Quit']);
	});

	it('downloads the log, shows it, and asks again', async () => {
		settings = { ...settings, downloadDir: 'E:\\Saved' };
		dialogAnswers.push(0, 1);

		way().offer('Sift has stopped', 'it stopped');
		await settle();

		expect(bundle.asked[0]).toMatchObject({ into: 'E:\\Saved', libraryLogs: settings.dataDir });
		expect(revealed).toEqual(['C:\\Saved\\Sift log.zip']);
		expect(dialogCalls[1]?.title).toBe('Sift has stopped');
		expect(appCalls.quit).toBe(1);
	});

	it('says why the log could not be made', async () => {
		bundle.answer = { ok: false, reason: 'no room' };
		dialogAnswers.push(0, 0, 1);

		way().offer('Sift has stopped', 'it stopped');
		await settle();

		expect(dialogCalls[1]?.message).toBe("Sift couldn't make the log file");
		expect(dialogCalls[1]?.detail).toBe('no room');
		expect(revealed).toEqual([]);
	});
});

describe('the library not where it was', () => {
	beforeEach(() => {
		drive.remote = true;
	});

	it('says where it looked and what each press does', async () => {
		dialogAnswers.push(3);

		way().offer('Sift has stopped', 'it stopped', true);
		await settle();

		expect(dialogCalls[0]?.message).toBe(`Sift can't open your library at ${settings.dataDir}`);
		expect(dialogCalls[0]?.buttons).toEqual([
			'Start again',
			'Choose again',
			'Download log',
			'Quit'
		]);
		expect(dialogCalls[0]?.detail).toContain('plug the drive in and press Start again');
		expect(dialogCalls[0]?.detail).toContain('a new, empty library there, and your library stays');
		expect(appCalls.quit).toBe(1);
	});

	it('starts again on the same place, or asks for the place again and nothing else', async () => {
		dialogAnswers.push(0, 1);
		const back = way();

		back.offer('Sift has stopped', 'it stopped', true);
		await settle();
		expect(startAgain).toHaveBeenCalledOnce();
		expect(saved).toEqual([]);

		back.offer('Sift has stopped', 'it stopped', true);
		await settle();
		expect(saved).toHaveLength(1);
		expect(saved[0]).toMatchObject({ mode: 'standalone', dataDir: null, cacheDir: null });
		expect(startAgain).toHaveBeenCalledTimes(2);
	});

	it('downloads the log, asks again, and opens its folder only once that is answered', async () => {
		dialogAnswers.push(2, 3);
		const shownBefore: number[] = [];
		const real = dialog.showMessageBoxSync;
		vi.spyOn(dialog, 'showMessageBoxSync').mockImplementation((options) => {
			shownBefore.push(revealed.length);
			return real(options);
		});

		way().offer('Sift has stopped', 'it stopped', true);
		await settle();

		expect(revealed).toEqual(['C:\\Saved\\Sift log.zip']);
		expect(dialogCalls[1]?.buttons?.[0]).toBe('Start again');
		expect(dialogCalls[1]?.detail).toContain('The log is saved as C:\\Saved\\Sift log.zip.');
		expect(shownBefore).toEqual([0, 0]);
	});
});

describe('the order the dialog decides in', () => {
	const PORT = 'Something else is already using port 5171.';
	beforeEach(() => {
		fs.rmSync(path.join(settings.dataDir ?? '', 'sift.sqlite3'));
	});

	it('says a new library whose first start was refused for the port as the port', async () => {
		dialogAnswers.push(1);
		way().offer(PORT, 'Close the other copy.');
		await settle();
		expect(dialogCalls[0]).toMatchObject({ message: PORT, buttons: ['Download log', 'Quit'] });
	});

	it('says a new library whose first start crashed as the crash, with the held start', async () => {
		dialogAnswers.push(2);
		way().offer('Sift stopped while it was starting up.', 'its lines', true, 0xc0000005);
		await settle();
		expect(dialogCalls[0]?.buttons).toEqual(['Start without them', 'Download log', 'Quit']);
		expect(dialogCalls[0]?.detail).toContain('inside one of the libraries');
	});

	it('says a library opened before whose database is gone is missing', async () => {
		settings = { ...settings, libraries: opened() };
		dialogAnswers.push(3);
		way().offer('Sift stopped while it was starting up.', 'its lines', true, 1);
		await settle();
		expect(dialogCalls[0]?.message).toBe(`Sift can't open your library at ${settings.dataDir}`);
	});

	it('says a refused folder is where the library cannot be opened', async () => {
		drive.remote = true;
		dialogAnswers.push(3);
		way().offer('Sift stopped while it was starting up.', 'its lines', true, 1);
		await settle();
		expect(dialogCalls[0]?.detail).toContain('network');
	});

	it('says the port as the port even where the library opened before is gone', async () => {
		settings = { ...settings, libraries: opened() };
		dialogAnswers.push(1);
		way().offer(PORT, 'Close the other copy.');
		await settle();
		expect(dialogCalls[0]).toMatchObject({ message: PORT, buttons: ['Download log', 'Quit'] });
	});
});

describe('the archive for the page', () => {
	it('answers its path, or null, with no library logs in client mode', async () => {
		settings = { ...settings, mode: 'client' };
		const back = way();

		expect(await back.archive('Sift log.zip')).toBe('C:\\Saved\\Sift log.zip');
		bundle.answer = { ok: false, reason: 'no room' };
		expect(await back.archive('Sift log.zip')).toBeNull();

		expect(bundle.asked[0]).toMatchObject({ name: 'Sift log.zip', libraryLogs: null });
		expect(dialogCalls).toHaveLength(0);
	});
});
