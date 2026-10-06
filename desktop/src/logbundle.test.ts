/* Download log with no backend: the scrubber started exactly as its one interface line says, here
 * stood in for by a fake process. */

import { EventEmitter } from 'node:events';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ipcRenderer, resetElectronStub, sender } from '../test/electron-stub';
import { SAVE_LOG_ARCHIVE } from './channels';
import { notePress } from './gesture';
import { logTo } from './log';
import {
	type Bundled,
	archiveName,
	BUNDLE_TIMEOUT_MS,
	bundleLogs,
	registerLogArchive,
	type BundleAsk
} from './logbundle';
import type { Reach } from './origins';

class FakeChild extends EventEmitter {
	stderr = new EventEmitter();
	kill = vi.fn();
}

let folder = '';
let child = new FakeChild();
let calls: [string, string[]][] = [];
let factsSeen = '';

const NOW = new Date('2026-10-05T14:30:52Z');

function ask(more: Partial<BundleAsk> = {}): BundleAsk {
	return {
		python: 'C:\\Sift\\resources\\runtime\\python.exe',
		appLogs: 'C:\\Users\\someone\\AppData\\Roaming\\sift-desktop',
		libraryLogs: 'C:\\Lib\\data',
		facts: { sift: '0.2.1', threads: 8 },
		into: folder,
		now: NOW,
		spawn: ((python: string, args: string[]) => {
			calls.push([python, args]);
			const facts = args[args.indexOf('--facts') + 1] ?? '';
			factsSeen = fs.readFileSync(facts, 'utf8');
			return child;
		}) as unknown as BundleAsk['spawn'],
		...more
	} as BundleAsk;
}

beforeEach(() => {
	folder = fs.mkdtempSync(path.join(os.tmpdir(), 'sift-bundle-'));
	child = new FakeChild();
	calls = [];
	factsSeen = '';
});

afterEach(() => {
	vi.useRealTimers();
	fs.rmSync(folder, { recursive: true, force: true });
});

describe('downloading the log with no backend', () => {
	it('starts the scrubber exactly as its interface says, and hands back the archive', async () => {
		const out = path.join(folder, archiveName(NOW));
		const made = bundleLogs(ask());
		fs.writeFileSync(out, 'zip');
		child.emit('close', 0);

		expect(await made).toEqual({ ok: true, file: out });
		const [python, args] = calls[0] ?? ['', []];
		expect(python).toBe('C:\\Sift\\resources\\runtime\\python.exe');
		const facts = args[args.length - 1] ?? '';
		expect(args).toEqual([
			'-I',
			'-u',
			'-m',
			'sift.logbundle',
			'--out',
			out,
			'--app-logs',
			'C:\\Users\\someone\\AppData\\Roaming\\sift-desktop',
			'--library-logs',
			'C:\\Lib\\data',
			'--facts',
			facts
		]);
		expect(factsSeen).toBe('sift: 0.2.1\nthreads: 8\n');
		expect(fs.existsSync(facts)).toBe(false);
	});

	it('leaves out the library logs where there is no library here', async () => {
		const made = bundleLogs(ask({ libraryLogs: null }));
		child.emit('close', 0);
		await made;

		expect(calls[0]?.[1]).not.toContain('--library-logs');
	});

	it("says the scrubber's one line when it refuses", async () => {
		const made = bundleLogs(ask());
		child.stderr.emit('data', Buffer.from('The folder is read-only.\nmore\n'));
		child.emit('close', 2);

		expect(await made).toEqual({
			ok: false,
			reason: 'The folder is read-only.'
		});
	});

	it('says the exit code when it said nothing, or wrote no archive', async () => {
		const made = bundleLogs(ask());
		child.emit('close', 0);

		expect(await made).toEqual({
			ok: false,
			reason: 'The log maker stopped with exit code 0.'
		});
	});

	it('says why it could not start, once', async () => {
		const made = bundleLogs(ask());
		child.emit('error', new Error('spawn ENOENT'));
		child.emit('close', -2);

		expect(await made).toEqual({ ok: false, reason: 'spawn ENOENT' });
	});

	it('ends a scrubber that hangs, and says so', async () => {
		vi.useFakeTimers();
		const made = bundleLogs(ask());
		await vi.advanceTimersByTimeAsync(BUNDLE_TIMEOUT_MS);

		expect(await made).toEqual({
			ok: false,
			reason: 'Making the log file took too long.'
		});
		expect(child.kill).toHaveBeenCalledOnce();
	});

	it('says so when the facts cannot be handed over', async () => {
		const names = ['TEMP', 'TMP', 'TMPDIR'];
		const kept = names.map((name) => process.env[name]);
		for (const name of names) process.env[name] = path.join(folder, 'no', 'such', 'folder');
		try {
			const made = await bundleLogs(ask());
			expect(made.ok).toBe(false);
			expect(calls).toEqual([]);
		} finally {
			names.forEach((name, at) => {
				if (kept[at] === undefined) delete process.env[name];
				else process.env[name] = kept[at];
			});
		}
	});

	it("names the archive as the page does, by this device's own time", () => {
		expect(archiveName(new Date(2026, 9, 5, 4, 3, 52))).toBe('sift-log 2026-10-05 04-03-52.zip');
	});
});

describe('the page asking for the archive', () => {
	const made: string[] = [];
	const archive = async (name: string): Promise<Bundled> => {
		made.push(name);
		return { ok: true, file: `D:\\Saved\\${name}` };
	};
	const at =
		(reach: Reach) =>
		(url: string): Reach | null =>
			url.startsWith('http://') ? reach : null;

	beforeEach(() => {
		made.length = 0;
		resetElectronStub();
	});

	it('makes the archive under the last part of the name only', async () => {
		registerLogArchive(at('local'), archive);

		expect(await ipcRenderer.invoke(SAVE_LOG_ARCHIVE, '..\\..\\Sift log.zip')).toEqual({
			file: 'D:\\Saved\\Sift log.zip'
		});
		expect(await ipcRenderer.invoke(SAVE_LOG_ARCHIVE, 'C:/Windows/x.zip')).toEqual({
			file: 'D:\\Saved\\x.zip'
		});
		for (const refused of ['', '..', 'C:\\', 42]) {
			expect(await ipcRenderer.invoke(SAVE_LOG_ARCHIVE, refused)).toHaveProperty('reason');
		}
		sender.senderFrame = { url: 'https://example.com/', parent: null };
		expect(await ipcRenderer.invoke(SAVE_LOG_ARCHIVE, 'Sift log.zip')).toEqual({
			reason: 'the page is not one this shell serves'
		});
		expect(made).toEqual(['Sift log.zip', 'x.zip']);
	});

	it('writes why it refused, and why the maker threw', async () => {
		const where = path.join(os.tmpdir(), `sift-logbundle-test-${process.pid}-${Date.now()}.log`);
		logTo(where);
		registerLogArchive(at('local'), async () => {
			throw new Error('Downloads is not a folder here');
		});

		sender.senderFrame = { url: 'https://example.com/', parent: null };
		expect(await ipcRenderer.invoke(SAVE_LOG_ARCHIVE, 'Sift log.zip')).toHaveProperty('reason');
		sender.senderFrame = {
			url: 'http://127.0.0.1:5171/settings',
			parent: null
		};
		expect(await ipcRenderer.invoke(SAVE_LOG_ARCHIVE, 'Sift log.zip')).toEqual({
			reason: 'Downloads is not a folder here'
		});

		const lines = fs
			.readFileSync(where, 'utf8')
			.trim()
			.split('\n')
			.map((line) => JSON.parse(line));
		logTo(null);
		fs.rmSync(where, { force: true });
		expect(lines.map((line) => [line.event, line.why ?? line.reason])).toEqual([
			['shell.log_download_refused', 'the page is not one this shell serves'],
			['shell.log_download_failed', 'Downloads is not a folder here']
		]);
	});

	it('makes one for a page from another computer only just after a press', async () => {
		registerLogArchive(at('remote'), archive);
		sender.senderFrame = {
			url: 'http://192.168.1.20:5171/settings',
			parent: null
		};

		expect(await ipcRenderer.invoke(SAVE_LOG_ARCHIVE, 'Sift log.zip')).toEqual({
			reason: 'no press'
		});
		notePress(sender.sender as object);
		expect(await ipcRenderer.invoke(SAVE_LOG_ARCHIVE, 'Sift log.zip')).toEqual({
			file: 'D:\\Saved\\Sift log.zip'
		});
		expect(made).toEqual(['Sift log.zip']);
	});
});
