/* The line under a file's Music field that says where the song's name came from.
 *
 * One case per source, because each is its own sentence and each is somebody's reading of where a
 * fact came from: a Site's page (named, or "its download page" where no one Site can be named),
 * AcoustID, another file (a link, or "another file" where this account may not see it). A typed
 * name draws nothing, and only a shared name offers Undo.
 */
import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import MusicSource from './MusicSource.svelte';
import { words } from '$lib/design/testing.svelte';

let host: HTMLElement;

afterEach(() => host?.remove());

function drawn(props: Record<string, unknown>): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	mount(MusicSource, { target: host, props: props as never });
	flushSync();
	return host;
}

function said(): string {
	return host.querySelector('.said')?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
}

it('names the Site whose page named the song, where there is exactly one', () => {
	drawn({ source: 'site', site: 'Bramblecast' });
	expect(said()).toBe("Named from Bramblecast's page");
});

it('says its download page where no one Site can be named', () => {
	drawn({ source: 'site', site: null });
	expect(said()).toBe('Named from its download page');
});

it('says AcoustID named it', () => {
	drawn({ source: 'acoustid' });
	expect(said()).toBe('Named from AcoustID');
});

it('links the file a shared name came from, opened in place', () => {
	drawn({ source: 'shared', from: { id: 'asset-7', name: 'Golden hour' } });
	expect(said()).toBe('Shared with Golden hour');
	expect(host.querySelector('.said a')?.getAttribute('href')).toBe('/asset/asset-7');
});

it('names no file this account may not see', () => {
	drawn({ source: 'shared', from: null });
	expect(said()).toBe('Shared with another file');
	expect(host.querySelector('a')).toBeNull();
});

it('draws nothing for a name somebody typed, or for no name at all', () => {
	for (const source of ['typed', null, 'something newer']) {
		drawn({ source });
		expect(host.textContent?.trim()).toBe('');
		host.remove();
	}
});

it('offers Undo on a shared name, through the door it is handed', () => {
	const onundo = vi.fn();
	drawn({ source: 'shared', from: { id: 'asset-7', name: 'Golden hour' }, onundo });

	// The button wears the undo glyph beside its word; `words` reads the word alone.
	const undo = [...host.querySelectorAll('button')].find((one) => words(one) === 'Undo');
	expect(undo).toBeDefined();
	undo?.click();
	expect(onundo).toHaveBeenCalledTimes(1);
});

it('offers no Undo on a name a Site or AcoustID gave, even when handed a door', () => {
	for (const source of ['site', 'acoustid']) {
		drawn({ source, onundo: vi.fn() });
		expect(host.querySelector('button')).toBeNull();
		host.remove();
	}
});
