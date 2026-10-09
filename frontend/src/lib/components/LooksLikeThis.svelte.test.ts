/*
 * The lookalikes strip, drawn: the addresses it builds, one heading whose tooltip says how it was
 * answered, and a video moving under the cursor.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import LooksLikeThis from './LooksLikeThis.svelte';
import type { SimilarItem, SimilarPage } from '$lib/search/semantic.svelte';
import { session, type Viewer } from '$lib/shell/session.svelte';

const findSimilar = vi.fn<(id: string) => Promise<SimilarPage>>();

vi.mock('$lib/search/semantic.svelte', () => ({
	findSimilar: (id: string) => findSimilar(id),
	SEMANTIC_ENABLED_KEY: 'semantic.enabled'
}));

const settingValues = vi.fn<() => Promise<Map<string, unknown>>>();

vi.mock(import('$lib/settings-ui/settings'), async (importOriginal) => ({
	...(await importOriginal()),
	fetchSettingValues: () => settingValues()
}));

const showStranger = vi.fn<(id: string, from: string | null, runs: boolean) => void>();

vi.mock('$lib/player/asset-view', () => ({
	showStranger: (id: string, from: string | null, runs: boolean) => showStranger(id, from, runs)
}));

/* Every address asked for; a clip answered "not ok" keeps this out of the blob machinery. */
const asked: string[] = [];

let host: HTMLElement;

beforeEach(() => {
	asked.length = 0;
	vi.stubGlobal('fetch', (input: RequestInfo | URL) => {
		asked.push(String(input));
		return Promise.resolve({ ok: false } as Response);
	});
});

afterEach(() => {
	host?.remove();
	// Portalled tooltips outlive the host.
	for (const bubble of document.querySelectorAll('[role="tooltip"]')) bubble.remove();
	findSimilar.mockReset();
	showStranger.mockReset();
	settingValues.mockReset();
	session.viewer = undefined;
	vi.unstubAllGlobals();
});

/* `art` is the token that lets the thumbnail assertion fail. */
function like(id: string, art: string | null = `${id}-token`): SimilarItem {
	return { id, media_type: 'video', width: 1920, height: 1080, duration_ms: 1000, art };
}

async function shown(page: SimilarPage, id = 'asset-1', cannotCompare: string | null = null) {
	findSimilar.mockResolvedValue(page);
	host = document.createElement('div');
	document.body.append(host);
	mount(LooksLikeThis, { target: host, props: { id, cannotCompare } });
	flushSync();
	await vi.waitFor(() => expect(findSimilar).toHaveBeenCalledWith(id));
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
	return host;
}

it('draws nothing at all when there is nothing like this file', async () => {
	await shown({ tier: 'matches', items: [] });

	expect(host.querySelector('section')).toBeNull();
	expect(host.textContent).toBe('');
});

it('says why the strip is empty when this file can never be compared', async () => {
	/* A file the decoder refused would otherwise read as "found nothing like it". */
	await shown(
		{ tier: 'matches', items: [] },
		'asset-1',
		'this file could not be decoded (Invalid NAL unit size)'
	);

	expect(host.textContent).toContain("can't be compared");
});

it('and still draws the matches it has, rather than hiding them behind the sentence', async () => {
	/* The sentence does not replace the strip. */
	await shown({ tier: 'matches', items: [like('abc')] }, 'asset-1', 'the frames would not decode');

	expect(host.textContent).toContain("can't be compared");
	expect(host.querySelectorAll('img')).toHaveLength(1);
});

it('and says nothing of the kind for a file that simply has no lookalikes', async () => {
	await shown({ tier: 'matches', items: [] });

	expect(host.textContent).toBe('');
});

it('points every picture at the address that really serves a thumbnail', async () => {
	// With a token and without: two addresses.
	await shown({ tier: 'looks', items: [like('abc'), like('def', null)] });

	const sources = [...host.querySelectorAll('img')].map((img) => img.getAttribute('src'));
	expect(sources).toEqual(['/api/assets/abc/thumb?v=abc-token', '/api/assets/def/thumb']);
});

it('opens a lookalike in the sheet it is already inside, rather than navigating', async () => {
	/* Inside the file's sheet, so asserted through the one function that moves it. */
	await shown({ tier: 'looks', items: [like('abc')] });

	expect(host.querySelector('ul a'), 'a lookalike is not a link out of this sheet').toBeNull();

	(host.querySelector('ul button') as HTMLButtonElement | null)?.click();
	flushSync();
	expect(showStranger).toHaveBeenCalledWith('abc', 'asset-1', true);
});

it('gives the lookalike a place in the run, just after the file it was opened from', async () => {
	/* `from` keeps the bar's outer pair for a lookalike. */
	await shown({ tier: 'looks', items: [like('abc')] }, 'opened-from-me');

	(host.querySelector('ul button') as HTMLButtonElement | null)?.click();
	flushSync();
	expect(showStranger).toHaveBeenCalledWith('abc', 'opened-from-me', true);
});

it('says a photograph does not play, so a run steps over it rather than waiting on it', async () => {
	/* `runs` is the file's fact a run reads. */
	await shown({
		tier: 'looks',
		items: [{ ...like('pic'), media_type: 'image', duration_ms: null }]
	});

	(host.querySelector('ul button') as HTMLButtonElement | null)?.click();
	flushSync();
	expect(showStranger).toHaveBeenCalledWith('pic', 'asset-1', false);
});

it('asks for the clip of a video the pointer arrives at', async () => {
	await shown({ tier: 'looks', items: [like('abc')] });

	host.querySelector('ul button')?.dispatchEvent(new PointerEvent('pointerenter'));
	flushSync();
	await vi.waitFor(() => expect(asked).toContain('/api/assets/abc/preview?v=abc-token'));
});

it('asks for nothing when the pointer arrives at a photograph', async () => {
	await shown({
		tier: 'looks',
		items: [{ ...like('pic'), media_type: 'image', duration_ms: null }]
	});

	host.querySelector('ul button')?.dispatchEvent(new PointerEvent('pointerenter'));
	flushSync();
	await Promise.resolve();
	expect(asked).toEqual([]);
});

async function answeredBy(): Promise<string | null | undefined> {
	host.querySelector('h3 .wrap')?.dispatchEvent(new PointerEvent('pointermove', { bubbles: true }));
	await vi.waitFor(() => expect(document.querySelector('[role="tooltip"]')).not.toBeNull());
	return document.querySelector('[role="tooltip"]')?.textContent?.trim();
}

it('is called Similar to this and says a model answered when a model answered', async () => {
	await shown({ tier: 'looks', items: [like('abc')] });

	expect(host.querySelector('h3')?.textContent?.trim()).toBe('Similar to this');
	expect(await answeredBy()).toBe('By Smart Search: other files that look alike.');
});

it('keeps the one name when the perceptual hash answered, and says so in the tooltip', async () => {
	await shown({ tier: 'matches', items: [like('abc')] });

	expect(host.querySelector('h3')?.textContent?.trim()).toBe('Similar to this');
	expect(await answeredBy()).toBe('By perceptual hash: files whose frames nearly match.');
});

it('opens the Files wall filtered to what is similar to this file', async () => {
	await shown({ tier: 'matches', items: [like('abc')] }, '01HX0000000000000000000001');

	expect(host.querySelector('h3 a')?.getAttribute('href')).toBe(
		'/browse?like=01HX0000000000000000000001'
	);
});

it('says nothing when the feature is off and the request fails', async () => {
	findSimilar.mockRejectedValue(new Error('off'));
	host = document.createElement('div');
	document.body.append(host);
	mount(LooksLikeThis, { target: host, props: { id: 'asset-1' } });
	flushSync();
	await vi.waitFor(() => expect(findSimilar).toHaveBeenCalled());
	await Promise.resolve();
	await Promise.resolve();
	flushSync();

	expect(host.querySelector('section')).toBeNull();
});

/* SMART SEARCH OFF, said to an admin with the switch as the link. */
async function withSwitch(role: 'admin' | 'guest', on: boolean, tier: SimilarPage['tier']) {
	session.viewer = { role } as Viewer;
	settingValues.mockResolvedValue(new Map([['semantic.enabled', on]]));
	await shown({ tier, items: [like('abc')] });
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
}

it('tells an admin Smart Search is off, with the switch linked', async () => {
	await withSwitch('admin', false, 'matches');

	await vi.waitFor(() => expect(host.textContent).toContain('Smart Search is off'));
	const link = host.querySelector('a.setting-link');
	expect(link?.textContent?.trim()).toBe('Turn it on');
	expect(link?.getAttribute('href')).toBe('/settings/tasks#semantic.enabled');
});

it('says nothing of the switch while Smart Search is on', async () => {
	await withSwitch('admin', true, 'matches');

	expect(settingValues).toHaveBeenCalled();
	expect(host.textContent).not.toContain('Smart Search is off');
});

it('and tells a guest nothing it cannot act on, and asks nothing', async () => {
	await withSwitch('guest', false, 'matches');

	expect(settingValues).not.toHaveBeenCalled();
	expect(host.textContent).not.toContain('Smart Search is off');
});

it('hands Tile the same height the stylesheet calls --strip-tall', () => {
	/* The stylesheet's and the script's copies kept one number. */
	const source = readFileSync(resolve('src/lib/components/LooksLikeThis.svelte'), 'utf8');
	const inScript = source.match(/const TALL = (\d+);/)?.[1];
	const css = readFileSync(resolve('src/app.css'), 'utf8');
	const inStylesheet = css.match(/--strip-tall: (\d+)px;/)?.[1];
	expect(inScript).toBeDefined();
	expect(inStylesheet).toBe(inScript);
});

it('keeps its lookalikes through a record read again for the same file', async () => {
	/* A replaced record object must not empty the strip. */
	findSimilar.mockResolvedValue({ tier: 'matches', items: [like('b'), like('c')] });
	const shown = $state({ record: { id: 'asset-1', views: 0 } });
	host = document.createElement('div');
	document.body.append(host);
	mount(LooksLikeThis, {
		target: host,
		props: {
			get id() {
				return shown.record.id;
			},
			cannotCompare: null
		}
	});
	flushSync();
	await vi.waitFor(() => expect(host.querySelector('section')).not.toBeNull());
	expect(findSimilar).toHaveBeenCalledTimes(1);

	let emptied = false;
	const watch = new MutationObserver(() => {
		if (host.querySelector('section') === null) emptied = true;
	});
	watch.observe(host, { childList: true, subtree: true });
	shown.record = { id: 'asset-1', views: 1 };
	flushSync();
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
	watch.disconnect();

	expect(findSimilar).toHaveBeenCalledTimes(1);
	expect(emptied).toBe(false);

	shown.record = { id: 'asset-2', views: 0 };
	flushSync();
	await vi.waitFor(() => expect(findSimilar).toHaveBeenCalledWith('asset-2'));
	expect(findSimilar).toHaveBeenCalledTimes(2);
});
