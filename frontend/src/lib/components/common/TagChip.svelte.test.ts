/* A tag's chip: its name, and how many files carry it where that is known. */

import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import TagChip from './TagChip.svelte';

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

afterEach(() => {
	if (mounted) void unmount(mounted, { outro: false });
	mounted = null;
	host?.remove();
});

function render(count: number | null) {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(TagChip, { target: host, props: { name: 'Outdoor', count } });
	flushSync();
	return host.querySelector('.count');
}

describe('the count on a tag', () => {
	it('is grouped the way every other count on screen is grouped', () => {
		// Ungrouped, a tag carried by 12,345 files would read "12345" beside a filter panel's
		// grouped figures.
		expect(render(12345)?.textContent).toBe((12345).toLocaleString());
		expect(render(12345)?.textContent).not.toBe('12345');
	});

	it('is not drawn at all where there is none to show', () => {
		expect(render(null)).toBeNull();
	});
});
