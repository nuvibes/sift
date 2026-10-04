/* The Logs tab from ANOTHER computer: where the Sift app on the computer running Sift answers
 * through the server, its own log's lines join the list, marked as that app's, and are read through
 * the server rather than this window's bridge. */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

const get = vi.hoisted(() =>
	vi.fn(async () => ({
		lines: [
			{
				raw: '{"event":"scan.started","level":"info","timestamp":"2026-09-30T09:59:00Z"}',
				at: '2026-09-30T09:59:00Z',
				level: 'info',
				event: 'scan.started'
			}
		],
		path: 'sift.log',
		size_bytes: 80,
		present: true,
		searched_bytes: 0,
		whole: true
	}))
);
const shellLog = vi.hoisted(() => vi.fn(async () => null));
const readServerAppLog = vi.hoisted(() =>
	vi.fn(async () => ({
		lines: ['{"event":"backend.started","level":"info","timestamp":"2026-09-30T10:00:00Z"}'],
		path: 'C:\\Sift\\shell.log',
		size: 80,
		present: true
	}))
);

vi.mock('$lib/api/client', () => ({ ApiError: class extends Error {}, api: { get } }));
vi.mock('$lib/bridge', () => ({ bridge: { canReadShellLog: () => false, shellLog } }));
vi.mock('$lib/desktop/server-shell', () => ({
	offersServer: (desk: { has_app?: boolean } | null) => desk?.has_app === true,
	readServerDesktop: async () => ({
		has_app: true,
		machine: 'DESK-ONE',
		starts_with_windows: null,
		sharing: null
	}),
	readServerAppLog
}));

import ApplicationLog from './ApplicationLog.svelte';
import { COPY } from './Logs.search';

let host: HTMLElement;
let shown: ReturnType<typeof mount> | null = null;

beforeEach(async () => {
	host = document.createElement('div');
	document.body.append(host);
	shown = mount(ApplicationLog, { target: host });
	flushSync();
	await vi.waitFor(() => expect(host.querySelectorAll('.line')).toHaveLength(2));
	await tick();
});

afterEach(() => {
	if (shown) unmount(shown);
	shown = null;
	host.remove();
	vi.clearAllMocks();
});

it('merges the app log of the computer running Sift, read there and marked by its own name', () => {
	expect(readServerAppLog).toHaveBeenCalledWith(500);
	expect(shellLog).not.toHaveBeenCalled();
	expect(host.textContent).toContain(COPY.bothThereCovers('DESK-ONE'));
	const rows = [...host.querySelectorAll('.line')].map((one) => [
		one.querySelector('.from')?.textContent,
		one.querySelector('.what')?.textContent
	]);
	expect(rows).toEqual([
		[COPY.library, 'scan.started'],
		[COPY.appThere, 'backend.started']
	]);
});

it('offers no choice of log: there is one list', () => {
	const labels = [...host.querySelectorAll('button')].map((one) => one.textContent?.trim());
	expect(labels).not.toContain(COPY.appThere);
	expect(labels).not.toContain(COPY.library);
});
