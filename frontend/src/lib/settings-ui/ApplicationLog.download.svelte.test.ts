/* The Logs tab in the desktop app: both logs as one list, and Download log, which shares every log
 * whole as one redacted archive and says where it went. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

const library = (event: string, at: string) => ({
	raw: JSON.stringify({ event, level: 'info', timestamp: at }),
	at,
	level: 'info',
	event
});

const get = vi.hoisted(() => vi.fn());
const shellLog = vi.hoisted(() => vi.fn());
const downloadFolder = vi.hoisted(() => vi.fn());
const saveLogArchive = vi.hoisted(() => vi.fn());
const shellArchives = vi.hoisted(() => ({ on: false }));
const show = vi.hoisted(() => vi.fn());
const triggerDownload = vi.hoisted(() => vi.fn());

vi.mock('$lib/api/client', () => ({ ApiError: class extends Error {}, api: { get } }));
vi.mock('$lib/bridge', () => ({
	bridge: {
		canReadShellLog: () => true,
		shellLog,
		downloadFolder,
		canSaveLogArchive: () => shellArchives.on,
		saveLogArchive
	}
}));
vi.mock('$lib/desktop/server-shell', () => ({
	offersServer: () => false,
	readServerDesktop: async () => null,
	readServerAppLog: vi.fn()
}));
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show } }));
vi.mock('$lib/capture/copy-out', () => ({ triggerDownload }));

import ApplicationLog from './ApplicationLog.svelte';
import { COPY } from './Logs.search';

let host: HTMLElement;
let shown: ReturnType<typeof mount> | null = null;
let saved: Blob | null = null;
const zip = new Blob(['PK'], { type: 'application/zip' });

beforeEach(async () => {
	shellArchives.on = false;
	get.mockImplementation(async (path: string) => (path === '/logs/archive' ? zip : page()));
	shellLog.mockResolvedValue({
		lines: [
			JSON.stringify({ event: 'drag.started', level: 'info', timestamp: '2026-10-03T06:00:02Z' })
		],
		path: 'C:\\Sift\\shell.log',
		size: 100,
		present: true
	});
	vi.spyOn(URL, 'createObjectURL').mockImplementation((blob) => {
		saved = blob as Blob;
		return 'blob:log';
	});
	vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
	host = document.createElement('div');
	document.body.append(host);
	shown = mount(ApplicationLog, { target: host });
	flushSync();
	await vi.waitFor(() => expect(host.querySelectorAll('.line')).toHaveLength(3));
	await tick();
});

function page() {
	return {
		lines: [
			library('scan.started', '2026-10-03T06:00:00Z'),
			library('scan.done', '2026-10-03T06:00:05Z')
		],
		path: 'C:\\Sift\\sift.log',
		size_bytes: 2048,
		present: true,
		searched_bytes: 0,
		whole: true
	};
}

afterEach(() => {
	if (shown) unmount(shown);
	shown = null;
	host.remove();
	saved = null;
	vi.clearAllMocks();
	vi.restoreAllMocks();
});

function pressDownload() {
	const button = host.querySelector<HTMLButtonElement>('.download button');
	expect(button?.textContent).toContain(COPY.download);
	expect(button, 'the download button').toBeTruthy();
	button?.click();
	flushSync();
}

describe('the one list', () => {
	it('is both logs in the order they were written, each line marked', () => {
		const rows = [...host.querySelectorAll('.line')].map(
			(one) =>
				`${one.querySelector('.from')?.textContent}:${one.querySelector('.what')?.textContent}`
		);
		expect(rows).toEqual([
			`${COPY.library}:scan.started`,
			`${COPY.app}:drag.started`,
			`${COPY.library}:scan.done`
		]);
		expect(host.textContent).toContain(COPY.bothCovers);
	});

	it('draws the line under the download button', () => {
		expect(host.querySelector('.download .trailer')?.textContent).toBe(COPY.downloadTrailer);
	});
});

describe('Download log', () => {
	it("saves the server's archive of the library's log, asked for whole", async () => {
		downloadFolder.mockResolvedValue(null);
		pressDownload();
		await vi.waitFor(() => expect(show).toHaveBeenCalled());

		expect(get).toHaveBeenCalledWith('/logs/archive', { asBlob: true });
		expect(saved).toBe(zip);
		expect(triggerDownload).toHaveBeenCalledWith(
			'blob:log',
			expect.stringMatching(/^sift-log \d{4}-\d\d-\d\d \d\d-\d\d-\d\d\.zip$/)
		);
		expect(show).toHaveBeenCalledWith(COPY.savedInBrowser, { tone: 'success' });
	});

	it('names the Save files to folder in the desktop app', async () => {
		downloadFolder.mockResolvedValue({ path: 'C:\\Users\\someone\\Downloads', chosen: false });
		pressDownload();
		await vi.waitFor(() => expect(show).toHaveBeenCalled());
		expect(show).toHaveBeenCalledWith('Saved to C:\\Users\\someone\\Downloads', {
			tone: 'success'
		});
	});

	it('has the shell make the archive of both places where it can, and names the file', async () => {
		shellArchives.on = true;
		saveLogArchive.mockResolvedValue({ file: 'C:\\Saved\\sift-log.zip' });
		pressDownload();
		await vi.waitFor(() => expect(show).toHaveBeenCalled());

		expect(saveLogArchive).toHaveBeenCalledWith(expect.stringMatching(/^sift-log .*\.zip$/));
		expect(get).not.toHaveBeenCalledWith('/logs/archive', expect.anything());
		expect(show).toHaveBeenCalledWith('Saved to C:\\Saved\\sift-log.zip', { tone: 'success' });
	});

	it("says why when the shell couldn't make it", async () => {
		shellArchives.on = true;
		saveLogArchive.mockResolvedValue({ reason: 'the log maker took too long' });
		pressDownload();
		await vi.waitFor(() => expect(show).toHaveBeenCalled());
		expect(show).toHaveBeenCalledWith(
			"Couldn't create the log file: the log maker took too long.",
			{
				tone: 'error'
			}
		);
	});

	it('saves nothing when the server refuses the archive', async () => {
		get.mockImplementation(async (path: string) => {
			if (path === '/logs/archive') throw new Error('refused');
			return page();
		});
		pressDownload();
		await vi.waitFor(() => expect(show).toHaveBeenCalled());
		expect(triggerDownload).not.toHaveBeenCalled();
		expect(show).toHaveBeenCalledWith(COPY.cannotDownload, { tone: 'error' });
	});
});
