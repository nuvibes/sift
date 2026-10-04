/*
 * Keeping a filter records which wall it was kept on: a wall draws back only the filters kept on
 * it, so a filter saved without its wall would come back over the library and nowhere else.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';

import { savedSearches } from '$lib/search/saved-searches.svelte';
import KeepFilter from './KeepFilter.svelte';

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host.remove();
	vi.restoreAllMocks();
});

it('keeps the filter on the wall it was made on', async () => {
	const save = vi.spyOn(savedSearches, 'save').mockResolvedValue();
	drawn = mount(KeepFilter, {
		target: host,
		props: {
			open: true,
			query: 'hair_color:red',
			subject: 'person',
			chips: createRawSnippet(() => ({ render: () => '<span>Hair color: Red</span>' }))
		}
	}) as Record<string, unknown>;
	flushSync();

	const name = document.querySelector<HTMLInputElement>('#keep-filter input');
	expect(name, 'the sheet draws no name to type').not.toBeNull();
	name!.value = 'Redheads';
	name!.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
	document.querySelector<HTMLFormElement>('#keep-filter')!.requestSubmit();
	await Promise.resolve();

	expect(save).toHaveBeenCalledWith('Redheads', 'hair_color:red', 'person');
});
