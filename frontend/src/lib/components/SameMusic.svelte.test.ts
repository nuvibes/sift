/* The strip of files that share a song with the one on screen.
 *
 * What can only be checked once it is drawn: that it is ABSENT when the group is empty (most files),
 * that the heading carries the server's count and leads to the Files wall filtered to exactly this
 * group with the chip reading the file's name, and that the tiles are the lookalikes' tiles:
 * pictures at the address that really serves one, moved within the sheet when pressed.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import SameMusic from './SameMusic.svelte';
import { facetValueLabel } from '$lib/components/shell/facet-labels';
import { sameMusicChanges } from '$lib/library/changes.svelte';
import type { SameMusic as Group, SameMusicFile } from '$lib/player/music';

const sameMusicOf = vi.fn<(id: string) => Promise<Group>>();

vi.mock('$lib/player/music', async (real) => ({
	...(await real<typeof import('$lib/player/music')>()),
	sameMusicOf: (id: string) => sameMusicOf(id)
}));

const showStranger = vi.fn<(id: string, from: string | null, runs: boolean) => void>();

vi.mock('$lib/player/asset-view', () => ({
	showStranger: (id: string, from: string | null, runs: boolean) => showStranger(id, from, runs)
}));

let host: HTMLElement;

beforeEach(() => {
	// A hovered clip is fetched; "not ok" keeps it out of the blob machinery, as the lookalikes' test.
	vi.stubGlobal('fetch', () => Promise.resolve({ ok: false } as Response));
});

afterEach(() => {
	host?.remove();
	sameMusicOf.mockReset();
	showStranger.mockReset();
	vi.unstubAllGlobals();
});

function file(id: string, art: string | null = `${id}-token`): SameMusicFile {
	return {
		id,
		media_type: 'video',
		width: 1920,
		height: 1080,
		duration_ms: 1000,
		art,
		name: `${id}.mp4`
	};
}

async function shown(group: Group, id = 'asset-1', name: string | null = 'Golden hour') {
	sameMusicOf.mockResolvedValue(group);
	host = document.createElement('div');
	document.body.append(host);
	mount(SameMusic, { target: host, props: { id, name } });
	flushSync();
	await vi.waitFor(() => expect(sameMusicOf).toHaveBeenCalledWith(id));
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
	return host;
}

it('draws nothing at all when no other file shares this song', async () => {
	await shown({ files: [], count: 0 });

	expect(host.querySelector('section')).toBeNull();
	expect(host.textContent).toBe('');
});

it('draws nothing when the read fails, as for a feature nobody turned on', async () => {
	sameMusicOf.mockRejectedValue(new Error('off'));
	host = document.createElement('div');
	document.body.append(host);
	mount(SameMusic, { target: host, props: { id: 'asset-1' } });
	flushSync();
	await vi.waitFor(() => expect(sameMusicOf).toHaveBeenCalled());
	await Promise.resolve();
	await Promise.resolve();
	flushSync();

	expect(host.querySelector('section')).toBeNull();
});

it("says Same music with the server's count in the heading's tail", async () => {
	await shown({ files: [file('abc'), file('def')], count: 2 });

	expect(host.querySelector('h3')?.textContent?.replace(/\s+/g, ' ').trim()).toBe(
		'Same music2 files'
	);
	expect(host.querySelector('h3 .tail')?.textContent).toBe('2 files');
});

it('reads the group again in place when a pairing pass rings, never blanking it first', async () => {
	await shown({ files: [file('abc')], count: 1 });
	let answer: (group: Group) => void = () => {};
	sameMusicOf.mockReturnValue(new Promise((done) => (answer = done)));
	const asked = sameMusicOf.mock.calls.length;

	sameMusicChanges.changed();
	flushSync();

	expect(sameMusicOf.mock.calls.length).toBeGreaterThan(asked);
	expect(host.querySelectorAll('li')).toHaveLength(1);
	answer({ files: [file('abc'), file('def')], count: 2 });
	await vi.waitFor(() => {
		flushSync();
		expect(host.querySelector('h3 .tail')?.textContent).toBe('2 files');
	});
});

it('says one file as one', async () => {
	await shown({ files: [file('abc')], count: 1 });

	expect(host.querySelector('h3 .tail')?.textContent).toBe('1 file');
});

it('leads from the heading to the Files wall narrowed to this group, the chip naming the file', async () => {
	await shown({ files: [file('abc')], count: 1 }, 'asset-9', 'Golden hour');

	const link = host.querySelector('h3 a');
	expect(link?.getAttribute('href')).toBe('/browse?same_music=asset-9');

	// The chip is drawn from the address alone; pressing the heading is what gives it a name.
	link?.addEventListener('click', (event) => event.preventDefault());
	(link as HTMLElement).click();
	expect(facetValueLabel('same_music', 'asset-9')).toBe('Golden hour');
});

it('draws one tile per file, in the order the server sent, at the address that serves a picture', async () => {
	await shown({ files: [file('abc'), file('def', null)], count: 2 });

	const sources = [...host.querySelectorAll('img')].map((img) => img.getAttribute('src'));
	expect(sources).toEqual(['/api/assets/abc/thumb?v=abc-token', '/api/assets/def/thumb']);
});

it('opens a file in the sheet it is already inside, just after this one', async () => {
	await shown({ files: [file('abc')], count: 1 }, 'opened-from-me');

	host.querySelector('li button')?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
	flushSync();
	expect(showStranger).toHaveBeenCalledWith('abc', 'opened-from-me', true);
});

it('folds through the shared fold like the other strips, its count in the tail', async () => {
	await shown({ files: [file('abc'), file('def', null)], count: 2 });
	const fold = host.querySelector('.fold.section');
	expect(fold).not.toBeNull();
	expect(fold?.querySelector('.tail')?.textContent).toBe('2 files');
	const arrow = fold?.querySelector('button[aria-expanded]') as HTMLButtonElement;
	expect(arrow.getAttribute('aria-label')).toBe('Same music');
	arrow.click();
	flushSync();
	await vi.waitFor(() => expect(host.querySelector('.strip')).toBeNull());
});

it('hands Tile the same height the stylesheet calls --strip-tall', () => {
	const source = readFileSync(resolve('src/lib/components/SameMusic.svelte'), 'utf8');
	const inScript = source.match(/const TALL = (\d+);/)?.[1];
	const css = readFileSync(resolve('src/app.css'), 'utf8');
	expect(inScript).toBeDefined();
	expect(css.match(/--strip-tall: (\d+)px;/)?.[1]).toBe(inScript);
});
