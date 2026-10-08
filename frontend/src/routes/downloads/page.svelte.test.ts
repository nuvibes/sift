/* The Downloads screen re-reads its list whenever a download moves. */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import Page from './+page.svelte';
import { DownloadQueue } from './queue.svelte';
import { downloadChanges } from '$lib/library/changes.svelte';
import { noServerAt } from '../../test-setup';

// What the screen reads beside its list, which this file is not about.
noServerAt(
	'/api/settings',
	'/api/settings/interface',
	'/api/library/folders',
	'/api/tunnels',
	'/api/download-routes',
	'/api/supported-sites',
	'/api/site-connections',
	'/api/downloads/seen',
	'/api/site-options'
);

let host: HTMLElement | undefined;
let mounted: Record<string, unknown> | null = null;

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
	vi.restoreAllMocks();
});

describe('a download moving', () => {
	it('reads the list again, once for each move', () => {
		const read = vi.spyOn(DownloadQueue.prototype, 'refresh').mockResolvedValue();
		host = document.createElement('div');
		document.body.append(host);
		mounted = mount(Page, { target: host });
		flushSync();
		const opened = read.mock.calls.length;

		downloadChanges.changed();
		flushSync();
		expect(read).toHaveBeenCalledTimes(opened + 1);

		downloadChanges.changed();
		flushSync();
		expect(read).toHaveBeenCalledTimes(opened + 2);
	});
});
