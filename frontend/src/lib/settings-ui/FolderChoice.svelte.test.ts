/*
 * A folder setting is chosen by pointing at it: the operating system's folder dialog where the
 * application offers one, never a box to type a path into.
 */
import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

import FolderChoice from './FolderChoice.svelte';
import type { SettingEntry } from '$lib/settings-ui/settings';

const entry = {
	key: 'backup.folder',
	label: 'Backup folder',
	help: 'Where backups go.',
	value: '',
	default: ''
} as unknown as SettingEntry;

let drawn: Record<string, unknown> | null = null;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	document.body.innerHTML = '';
	delete (window as { sift?: unknown }).sift;
});

function draw(value: string, onchange: (folder: string) => void): HTMLElement {
	const host = document.createElement('div');
	document.body.append(host);
	drawn = mount(FolderChoice, {
		target: host,
		props: { entry, value, empty: "Beside Sift's own data", reset: 'Use the default', onchange }
	}) as Record<string, unknown>;
	flushSync();
	return host;
}

function press(host: HTMLElement, words: string): HTMLButtonElement | undefined {
	return [...host.querySelectorAll('button')].find((one) => one.textContent?.includes(words));
}

it('draws no box to type into, and says what an empty folder means', () => {
	const host = draw('', vi.fn());
	expect(host.querySelector('input')).toBeNull();
	expect(host.textContent).toContain("Beside Sift's own data");
	expect(press(host, 'Use the default')).toBeUndefined();
});

it("chooses through the operating system's folder dialog where the application has one", async () => {
	(window as { sift?: unknown }).sift = { chooseFolder: vi.fn().mockResolvedValue('D:\\Backups') };
	const onchange = vi.fn();
	const host = draw('', onchange);

	press(host, 'Choose')?.click();
	await tick();
	await tick();

	expect(onchange).toHaveBeenCalledWith('D:\\Backups');
});

it('offers the way back to the default place once a folder is chosen', () => {
	const onchange = vi.fn();
	const host = draw('D:\\Backups', onchange);
	press(host, 'Use the default')?.click();
	expect(onchange).toHaveBeenCalledWith('');
});
