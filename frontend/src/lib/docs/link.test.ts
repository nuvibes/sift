import { afterEach, describe, expect, it, vi } from 'vitest';

const copyText = vi.hoisted(() => vi.fn<(text: string) => Promise<boolean>>());
vi.mock('$lib/shell/clipboard', () => ({ copyText }));

import { toasts } from '$lib/shell/toasts.svelte';
import { copySectionLink, sectionAddress } from './link';

afterEach(() => {
	toasts.clear();
	copyText.mockReset();
});

describe("a heading's link in the Documentation pane", () => {
	it('opens the page at that heading', () => {
		expect(
			sectionAddress(
				'/settings/documentation?show=library%2Ftheater',
				'choose-a-layout',
				'http://sift.example:8000'
			)
		).toBe(
			'http://sift.example:8000/settings/documentation?show=library%2Ftheater#doc-choose-a-layout'
		);
	});

	it('is copied in one press, and says so', async () => {
		copyText.mockResolvedValue(true);
		expect(await copySectionLink('http://sift.example/a#doc-b')).toBe(true);
		expect(copyText).toHaveBeenCalledWith('http://sift.example/a#doc-b');
		expect(toasts.items.map((one) => [one.message, one.tone])).toEqual([['Link copied', 'info']]);
	});

	it('says when the clipboard refused it', async () => {
		copyText.mockResolvedValue(false);
		expect(await copySectionLink('http://sift.example/a#doc-b')).toBe(false);
		expect(toasts.items.map((one) => one.tone)).toEqual(['error']);
	});
});
