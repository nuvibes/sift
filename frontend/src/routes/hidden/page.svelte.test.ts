/*
 * Hidden, for somebody with no PIN yet.
 *
 * Nothing can be hidden or shown again without a PIN, so the first press on Hidden is where one
 * is made: the screen asks at once, and its button names what pressing it will do.
 */

import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import Page from './+page.svelte';
import { vault, vaultPrompt } from '$lib/shell/vault.svelte';

let host: HTMLElement | undefined;
let mounted: Record<string, unknown> | null = null;

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
	vaultPrompt.creating = null;
	vaultPrompt.asking = false;
	vault.loaded = false;
	vault.pinSet = false;
});

function render(): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(Page, { target: host });
	flushSync();
	return host;
}

describe('arriving at Hidden', () => {
	it('asks for a PIN at once when there is none', () => {
		vault.loaded = true;
		vault.pinSet = false;

		const page = render();

		expect(vaultPrompt.creating).toBe('first');
		expect(page.textContent).toContain('Create a PIN');
	});

	it('asks nothing of somebody who has a PIN until they press', () => {
		vault.loaded = true;
		vault.pinSet = true;

		const page = render();

		expect(vaultPrompt.creating).toBeNull();
		expect(vaultPrompt.asking).toBe(false);
		expect(page.textContent).toContain('Unlock');
	});
});

describe('Hidden while it is locked', () => {
	it("is the screen's own title over an empty page, its glyph in the empty page's disc", () => {
		vault.loaded = true;
		vault.pinSet = true;

		const page = render();

		expect(page.querySelector('h1')?.textContent?.trim()).toContain('Hidden');
		expect(page.querySelector('.empty .glyph')).not.toBeNull();
		expect(page.textContent).toContain('Enter your PIN to view your hidden items.');
	});
});
