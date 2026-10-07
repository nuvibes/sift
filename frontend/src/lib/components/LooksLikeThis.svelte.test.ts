/* The strip of lookalikes under a file.
 *
 * Three things about it can only be checked once it is drawn, and the server tests can see none.
 *
 * The pictures are addresses this component assembles itself. The tests behind it assert the ids
 * that come back and never what the browser is then sent to ask for, so one wrong word in that
 * string would be a strip of broken pictures under a green suite.
 *
 * The heading is ONE name however the strip was answered, and its tooltip says which way did: a
 * model deciding two pictures resemble each other, or two files' perceptual hashes nearly matching.
 * Two headings for one strip read as two sections.
 *
 * And a video in this strip MOVES under the cursor, the way it does on every other wall, which
 * bare pictures would not; nothing here can say so except by watching what the strip asks the
 * server for when a pointer arrives.
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

/* The settings an admin's strip reads for the Smart Search switch. */
const settingValues = vi.fn<() => Promise<Map<string, unknown>>>();

vi.mock(import('$lib/settings-ui/settings'), async (importOriginal) => ({
	...(await importOriginal()),
	fetchSettingValues: () => settingValues()
}));

const showStranger = vi.fn<(id: string, from: string | null, runs: boolean) => void>();

vi.mock('$lib/player/asset-view', () => ({
	showStranger: (id: string, from: string | null, runs: boolean) => showStranger(id, from, runs)
}));

/* Every address the strip asked the server for, in order.
 *
 * A clip is fetched rather than pointed at, so that a tile whose clip is still being built shows
 * its still instead of a broken picture. "Not ok" is the honest answer here: it is what the server
 * says for a file with no clip, and it keeps this out of the blob and object-url machinery that
 * has nothing to do with what is being checked. */
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
	// A tooltip is portalled to the body, so it outlives the host and would answer the next test.
	for (const bubble of document.querySelectorAll('[role="tooltip"]')) bubble.remove();
	findSimilar.mockReset();
	showStranger.mockReset();
	settingValues.mockReset();
	session.viewer = undefined;
	vi.unstubAllGlobals();
});

/*
 * `art` is the picture token, and it is what lets the thumbnail assertion below fail. Without it
 * the helper's address and a hand-written one are identical, so the test would agree with the
 * component whichever it used; with it, a component building its own address and dropping the token
 * (so the browser revalidates every lookalike on every use) is caught.
 */
function like(id: string, art: string | null = `${id}-token`): SimilarItem {
	return { id, media_type: 'video', width: 1920, height: 1080, duration_ms: 1000, art };
}

/** Mount it and let the one request it makes on the way up finish. */
async function shown(page: SimilarPage, id = 'asset-1', cannotCompare: string | null = null) {
	findSimilar.mockResolvedValue(page);
	host = document.createElement('div');
	document.body.append(host);
	mount(LooksLikeThis, { target: host, props: { id, cannotCompare } });
	flushSync();
	await vi.waitFor(() => expect(findSimilar).toHaveBeenCalledWith(id));
	// The answer lands a microtask after the call; two turns and a flush is what puts it on screen.
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
	/*
	 * The rule above (absent when there is nothing to draw) is right for a library nobody has
	 * fingerprinted yet and wrong for a file whose frames the decoder refused, and the two produce
	 * the identical empty screen. Silence there reads as "Sift looked and found nothing like it",
	 * which is the opposite of what happened.
	 */
	await shown(
		{ tier: 'matches', items: [] },
		'asset-1',
		'this file could not be decoded (Invalid NAL unit size)'
	);

	expect(host.textContent).toContain("can't be compared");
});

it('and still draws the matches it has, rather than hiding them behind the sentence', async () => {
	/*
	 * A verdict is written the moment the decoder refuses, and the numbers the file was given while
	 * it still decoded are kept rather than blanked, so a file can carry the sentence and real
	 * matches at the same time. The sentence must not replace the strip.
	 */
	await shown({ tier: 'matches', items: [like('abc')] }, 'asset-1', 'the frames would not decode');

	expect(host.textContent).toContain("can't be compared");
	expect(host.querySelectorAll('img')).toHaveLength(1);
});

it('and says nothing of the kind for a file that simply has no lookalikes', async () => {
	// The known negative for the sentence above: most of a library draws nothing at all here.
	await shown({ tier: 'matches', items: [] });

	expect(host.textContent).toBe('');
});

it('points every picture at the address that really serves a thumbnail', async () => {
	// One file with a token and one without, because those are two different addresses and the
	// component has to get both right. A file whose derivatives predate the digest being recorded
	// has no token, and it still has to draw.
	await shown({ tier: 'looks', items: [like('abc'), like('def', null)] });

	const sources = [...host.querySelectorAll('img')].map((img) => img.getAttribute('src'));
	expect(sources).toEqual(['/api/assets/abc/thumb?v=abc-token', '/api/assets/def/thumb']);
});

it('opens a lookalike in the sheet it is already inside, rather than navigating', async () => {
	/*
	 * This strip is drawn inside the file's own sheet, so following a route would tear the sheet
	 * down and build it again around the next file, which is what a bare link would do.
	 *
	 * Asserted through the one function that moves the sheet, so a lookalike wired to anything else
	 * fails here rather than looking right and reloading the screen.
	 */
	await shown({ tier: 'looks', items: [like('abc')] });

	// The heading is a link to the wall on purpose; a TILE in the strip never is.
	expect(host.querySelector('ul a'), 'a lookalike is not a link out of this sheet').toBeNull();

	(host.querySelector('ul button') as HTMLButtonElement | null)?.click();
	flushSync();
	expect(showStranger).toHaveBeenCalledWith('abc', 'asset-1', true);
});

it('gives the lookalike a place in the run, just after the file it was opened from', async () => {
	/*
	 * Opening a lookalike keeps the bar's outer pair. A lookalike is not in the list the panel was
	 * opened over, so the sheet would have no previous and no next for it. `from` supplies them
	 * (the file it was opened from is what Previous goes back to), and it is the second argument
	 * rather than something the module works out, because only this strip knows which file the row
	 * was drawn under.
	 */
	await shown({ tier: 'looks', items: [like('abc')] }, 'opened-from-me');

	(host.querySelector('ul button') as HTMLButtonElement | null)?.click();
	flushSync();
	expect(showStranger).toHaveBeenCalledWith('abc', 'opened-from-me', true);
});

it('says a photograph does not play, so a run steps over it rather than waiting on it', async () => {
	/* `runs` is a fact about the FILE and it is what a run advancing by itself reads. A still with
	   `true` on it stops a run dead: there is no `ended` to fire. Worked out from the row here
	   because this strip is the one place that holds it. */
	await shown({
		tier: 'looks',
		items: [{ ...like('pic'), media_type: 'image', duration_ms: null }]
	});

	(host.querySelector('ul button') as HTMLButtonElement | null)?.click();
	flushSync();
	expect(showStranger).toHaveBeenCalledWith('pic', 'asset-1', false);
});

it('asks for the clip of a video the pointer arrives at', async () => {
	// A video must play here as it does everywhere else. The address carries the picture token off
	// the row, so a clip fetched without it is one the browser has to revalidate on every visit.
	await shown({ tier: 'looks', items: [like('abc')] });

	host.querySelector('ul button')?.dispatchEvent(new PointerEvent('pointerenter'));
	flushSync();
	await vi.waitFor(() => expect(asked).toContain('/api/assets/abc/preview?v=abc-token'));
});

it('asks for nothing when the pointer arrives at a photograph', async () => {
	// A still has no clip, and asking anyway is one refused request per picture on a wall of them.
	await shown({
		tier: 'looks',
		items: [{ ...like('pic'), media_type: 'image', duration_ms: null }]
	});

	host.querySelector('ul button')?.dispatchEvent(new PointerEvent('pointerenter'));
	flushSync();
	await Promise.resolve();
	expect(asked).toEqual([]);
});

/** The words the heading's tooltip opens with, once a pointer has moved over the heading. */
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

/*
 * SMART SEARCH OFF. The server then answers by the perceptual hash alone, and an admin (the one
 * account that can turn it on) is told so beside the strip, with the switch as the link.
 */
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
	expect(link?.getAttribute('href')).toBe('/settings/importing#semantic.enabled');
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
	/* Two copies of one number, on purpose: the stylesheet's is the design's and the script's is
	   what `Tile` takes as an intrinsic size. This is what keeps them one number. */
	const source = readFileSync(resolve('src/lib/components/LooksLikeThis.svelte'), 'utf8');
	const inScript = source.match(/const TALL = (\d+);/)?.[1];
	const css = readFileSync(resolve('src/app.css'), 'utf8');
	const inStylesheet = css.match(/--strip-tall: (\d+)px;/)?.[1];
	expect(inScript).toBeDefined();
	expect(inStylesheet).toBe(inScript);
});

it('keeps its lookalikes through a record read again for the same file', async () => {
	/* The caller hands `id` through an object that is replaced whenever the record is read again
	   (a playing clip's view counted, a job's bell): the strip must not empty and refill. */
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
