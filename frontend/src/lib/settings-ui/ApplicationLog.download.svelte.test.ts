/* The Logs tab in the desktop app: both logs as one list, and Download log, which writes both
 * through the server's scrubber into one file, each line marked, and says where it went. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

const library = (event: string, at: string) => ({
	raw: JSON.stringify({ event, level: 'info', timestamp: at }),
	at,
	level: 'info',
	event
});

const get = vi.hoisted(() => vi.fn());
const post = vi.hoisted(() => vi.fn());
const shellLog = vi.hoisted(() => vi.fn());
const downloadFolder = vi.hoisted(() => vi.fn());
const show = vi.hoisted(() => vi.fn());
const triggerDownload = vi.hoisted(() => vi.fn());

vi.mock('$lib/api/client', () => ({ ApiError: class extends Error {}, api: { get, post } }));
vi.mock('$lib/bridge', () => ({
	bridge: { canReadShellLog: () => true, shellLog, downloadFolder }
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

beforeEach(async () => {
	get.mockResolvedValue({
		lines: [
			library('scan.started', '2026-10-03T06:00:00Z'),
			library('scan.done', '2026-10-03T06:00:05Z')
		],
		path: 'C:\\Sift\\sift.log',
		size_bytes: 2048,
		present: true,
		searched_bytes: 0,
		whole: true
	});
	shellLog.mockResolvedValue({
		lines: [
			JSON.stringify({ event: 'drag.started', level: 'info', timestamp: '2026-10-03T06:00:02Z' })
		],
		path: 'C:\\Sift\\shell.log',
		size: 100,
		present: true
	});
	post.mockImplementation(async (_path: string, options: { body: { lines: string[] } }) => ({
		lines: options.body.lines.map((one) => one.replace('scan', 'SCRUBBED'))
	}));
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
	it('has every line scrubbed by the server and saves them as one file, each marked', async () => {
		downloadFolder.mockResolvedValue({ path: 'C:\\Users\\someone\\Downloads', chosen: false });
		pressDownload();
		await vi.waitFor(() => expect(show).toHaveBeenCalled());

		expect(post).toHaveBeenCalledWith('/logs/redacted', {
			body: { lines: expect.arrayContaining([expect.stringContaining('drag.started')]) }
		});
		expect(triggerDownload).toHaveBeenCalledWith(
			'blob:log',
			expect.stringMatching(/^sift-log \d{4}-\d\d-\d\d \d\d-\d\d-\d\d\.txt$/)
		);
		const text = await saved!.text();
		expect(
			text
				.split('\n')
				.filter(Boolean)
				.map((one) => one.slice(0, one.indexOf(']') + 1))
		).toEqual([`[${COPY.library}]`, `[${COPY.app}]`, `[${COPY.library}]`]);
		expect(text).toContain('SCRUBBED.started');
		expect(text).not.toContain('"scan.started"');
	});

	it('names the Save files to folder in the desktop app', async () => {
		downloadFolder.mockResolvedValue({ path: 'C:\\Users\\someone\\Downloads', chosen: false });
		pressDownload();
		await vi.waitFor(() => expect(show).toHaveBeenCalled());
		expect(show).toHaveBeenCalledWith('Saved to C:\\Users\\someone\\Downloads', {
			tone: 'success'
		});
	});

	it("says the browser's downloads where there is no folder of the app's", async () => {
		downloadFolder.mockResolvedValue(null);
		pressDownload();
		await vi.waitFor(() => expect(show).toHaveBeenCalled());
		expect(show).toHaveBeenCalledWith(COPY.savedInBrowser, { tone: 'success' });
	});

	it('saves nothing unscrubbed when the server cannot scrub it', async () => {
		post.mockRejectedValue(new Error('refused'));
		pressDownload();
		await vi.waitFor(() => expect(show).toHaveBeenCalled());
		expect(triggerDownload).not.toHaveBeenCalled();
		expect(show).toHaveBeenCalledWith(COPY.cannotDownload, { tone: 'error' });
	});
});
