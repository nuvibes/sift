/* Song names on the Music section: the one switch that sends a file's sound to AcoustID.
 *
 * What is worth pinning is what makes it safe to have on the screen at all. It is off unless the
 * server says it is on, and the switch sits under the sentence saying what it sends, in the
 * server's words and never folded away. The key goes into a password box, goes out through its own
 * route, and is never drawn again: the block says a key is saved, and nothing more. The switch
 * and the route are written through the ordinary settings write, and the test of the key says what
 * came back in the server's words.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { words as wordsOn } from '$lib/design/testing.svelte';
import { flushSync, mount, tick, unmount } from 'svelte';

import { api } from '$lib/api/client';
import { jobChanges, libraryChanges } from '$lib/library/changes.svelte';
import type { SettingEntry } from '$lib/settings-ui/settings';

const mocks = vi.hoisted(() => ({ fetchSettings: vi.fn(), saveSettings: vi.fn() }));

vi.mock('$lib/settings-ui/settings', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/settings-ui/settings')>()),
	fetchSettings: mocks.fetchSettings,
	saveSettings: mocks.saveSettings
}));

import MusicLookup from './MusicLookup.svelte';
import DrilldownPage from './DrilldownPage.svelte';
import { drilldown } from './drilldown.svelte';
import { COPY, MusicLookup as Lookup } from './music-lookup.svelte';

/* The server's declarations, word for word as `slices/music/settings.py` registers them. */
const DISCLOSURE =
	"Sends a fingerprint of the file's sound and its length to AcoustID, never the file. Off unless you turn it on.";

const SWITCH: SettingEntry = {
	key: 'music.lookup',
	value: false,
	default: false,
	label: 'Name songs with AcoustID',
	help: 'Sift asks AcoustID which song a full-length file uses.',
	disclosure: DISCLOSURE
};

const ROUTE: SettingEntry = {
	key: 'music.lookup_route',
	value: null,
	default: null,
	label: 'Connect to AcoustID through',
	help: 'A tunnel you added under Sites, or nothing to connect directly.'
};

/* The whole wire shape: a fixture without `key_ready` is a key that cannot be opened. */
const OFF = { on: false, key_set: false, key_ready: false, route: null };
const KEYED = { on: false, key_set: true, key_ready: true, route: null };

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;
/* The reader the block was handed, so a test can read what it holds. */
let current: Lookup | null = null;

function drawnLookup(): Lookup {
	expect(current).not.toBeNull();
	return current as Lookup;
}
/* The sub-page the pane draws over itself: the route lives on it. */
let page: Record<string, unknown> | null = null;

async function settle(): Promise<void> {
	for (let turn = 0; turn < 4; turn += 1) {
		flushSync();
		await tick();
		await Promise.resolve();
	}
	flushSync();
}

async function shown(state: Record<string, unknown> = OFF): Promise<HTMLElement> {
	/* Answered per route: the block reads the tunnels too, to name the ways out, and a catch-all
	   shape handed to that read puts an object where a list goes. */
	vi.spyOn(api, 'get').mockImplementation(async (path: string) => {
		if (path.startsWith('/tunnels')) return [] as never;
		if (path.startsWith('/download-routes'))
			return { default: 'direct', sites: {}, available: [] } as never;
		if (path === '/music/lookup') return state as never;
		throw new Error(`unexpected read of ${path}`);
	});
	host = document.createElement('div');
	document.body.append(host);
	current = new Lookup();
	drawn = mount(MusicLookup, { target: host, props: { lookup: current } }) as Record<
		string,
		unknown
	>;
	page = mount(DrilldownPage, { target: host, props: { behind: 'Music' } }) as Record<
		string,
		unknown
	>;
	await settle();
	return host;
}

function words(): string {
	return (host.textContent ?? '').replace(/\s+/g, ' ');
}

function button(name: string): HTMLButtonElement {
	const found = [...host.querySelectorAll('button')].find((one) => wordsOn(one) === name);
	expect(found, `no button reading ${name}`).toBeDefined();
	return found as HTMLButtonElement;
}

beforeEach(() => {
	vi.restoreAllMocks();
	mocks.fetchSettings.mockResolvedValue([{ name: 'Music', settings: [SWITCH, ROUTE] }]);
	mocks.saveSettings.mockResolvedValue(undefined);
});

afterEach(() => {
	if (page) unmount(page);
	page = null;
	drilldown.close();
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
	vi.restoreAllMocks();
});

it("is off out of the box, under the server's own sentence saying what it sends", async () => {
	await shown();

	const toggle = host.querySelector('[id="music.lookup"] [role="switch"]');
	expect(toggle, 'the switch is not drawn').not.toBeNull();
	expect(toggle?.getAttribute('aria-checked')).toBe('false');

	// The disclosure, word for word, in the shaded status box under the switch rather than behind
	// "More about": it is what this switch sends off the device, and the reason it is off.
	const box = host.querySelector('.recognition-note');
	expect(box, 'the status is not in the Recognition box').not.toBeNull();
	expect(box?.textContent).toContain(COPY.status.off);
	expect(box?.querySelector('.more p')?.textContent?.trim()).toBe(DISCLOSURE);
	// The switch opens the block: no heading or paragraph stands before it.
	const block = host.querySelector('[id="music.acoustid"]')?.closest('section');
	expect(block?.querySelector('.lede')).toBeNull();
});

/* Where the switch stands is said under it, and what stands between On and anything being sent
   (no key, or a key locked by a restart) is named there rather than left to be worked out. */
it('says where the switch stands in the Note under it', async () => {
	for (const [state, said] of [
		[OFF, COPY.status.off],
		[{ ...OFF, on: true }, COPY.status.noKey],
		[{ ...KEYED, on: true, key_ready: false }, COPY.status.locked],
		[{ ...KEYED, on: true }, COPY.status.on]
	] as const) {
		await shown(state);
		const block = host.querySelector('[id="music.acoustid"]')?.closest('section');
		expect(block?.textContent, JSON.stringify(state)).toContain(said);
		if (page) unmount(page);
		page = null;
		if (drawn) unmount(drawn);
		drawn = null;
		host.remove();
	}
});

it("draws the switch from the server's answer, whichever it is", async () => {
	await shown({ ...OFF, on: true });

	const toggle = host.querySelector('[id="music.lookup"] [role="switch"]');
	expect(toggle?.getAttribute('aria-checked')).toBe('true');
});

it('turns on through the ordinary settings write', async () => {
	await shown();

	host.querySelector<HTMLElement>('[id="music.lookup"] [role="switch"]')?.click();
	await settle();

	expect(mocks.saveSettings).toHaveBeenCalledWith({ 'music.lookup': true });
});

it('takes the key in a password box, sends it through its own route, and never draws it', async () => {
	const put = vi.spyOn(api, 'put').mockResolvedValue(KEYED as never);
	await shown();

	const box = host.querySelector<HTMLInputElement>('input[type="password"]');
	expect(box, 'the key is not typed into a password box').not.toBeNull();
	expect(box?.value).toBe('');

	box!.value = 'app-key-7f3a9';
	box!.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
	host.querySelector('form')?.requestSubmit();
	await settle();

	expect(put).toHaveBeenCalledWith('/music/lookup/key', { body: { key: 'app-key-7f3a9' } });
	expect(words()).toContain('Key saved');
	expect(host.innerHTML).not.toContain('app-key-7f3a9');
	for (const one of host.querySelectorAll('input')) expect(one.value).not.toBe('app-key-7f3a9');
});

it('deletes the key through its own route, and offers the box again', async () => {
	const del = vi.spyOn(api, 'del').mockResolvedValue(OFF as never);
	await shown(KEYED);

	button('Delete key').click();
	await settle();

	expect(del).toHaveBeenCalledWith('/music/lookup/key');
	expect(host.querySelector('input[type="password"]')).not.toBeNull();
	expect(words()).not.toContain('Key saved');
});

it('says a key locked by a restart is locked, rather than that it is saved', async () => {
	await shown({ ...KEYED, key_ready: false });

	expect(words()).toContain('Key locked');
	expect(words()).not.toContain('Key saved');
	expect(words()).toContain('Enter your password in Unlock');
});

it("says what the test came back with, in the server's words", async () => {
	const post = vi
		.spyOn(api, 'post')
		.mockResolvedValueOnce({ ok: false, said: 'AcoustID refused the key.' } as never)
		.mockResolvedValueOnce({ ok: true, said: 'AcoustID accepted the key.' } as never);
	await shown(KEYED);

	button('Test').click();
	await settle();
	expect(post).toHaveBeenCalledWith('/music/lookup/check');
	expect(words()).toContain('AcoustID refused the key.');

	button('Test').click();
	await settle();
	expect(words()).toContain('AcoustID accepted the key.');
	expect(words()).toContain('Connected');
});

it('offers no test until there is a key to test', async () => {
	await shown();

	expect([...host.querySelectorAll('button')].map((one) => one.textContent?.trim())).not.toContain(
		'Test'
	);
});

it('draws the route under its own address on More settings, starting from your own connection', async () => {
	await shown();
	// Set once, if ever, so it is one level in rather than on the pane.
	expect(host.querySelector('[id="music.lookup_route"]')).toBeNull();

	button('Edit').click();
	await settle();

	const row = host.querySelector('[id="music.lookup_route"]');
	expect(row, 'the route row has no address a search can ring').not.toBeNull();
	expect(row?.textContent).toContain('Connect to AcoustID through');
	expect(row?.textContent).toContain('Direct \u2014 your own connection');
});

it('writes the route through the ordinary settings write, direct as nothing', async () => {
	vi.spyOn(api, 'get').mockResolvedValue(OFF as never);
	const lookup = new Lookup();
	await lookup.load();

	await lookup.route('tunnel-1');
	expect(mocks.saveSettings).toHaveBeenLastCalledWith({ 'music.lookup_route': 'tunnel-1' });
	await lookup.route(null);
	expect(mocks.saveSettings).toHaveBeenLastCalledWith({ 'music.lookup_route': null });
});

it('puts the switch back when the server refuses it', async () => {
	mocks.saveSettings.mockRejectedValue(new Error('refused'));
	vi.spyOn(api, 'get').mockResolvedValue(OFF as never);
	const lookup = new Lookup();
	await lookup.load();

	await lookup.turn(true);
	expect(lookup.state?.on).toBe(false);
	expect(lookup.problem).not.toBeNull();
});

it('asks AcoustID about nothing itself: the lookup is a task, pressed on Tasks', async () => {
	/* Turning the switch on says what MAY be sent. When anything is sent is the lookup task's own
	 * When, and its press is on Tasks: this block draws no press that sends the library's
	 * fingerprints, and says so in the line under the switch. */
	const OWED = { on: true, key_set: true, key_ready: true, route: null, owed: 12 };
	const post = vi.spyOn(api, 'post');
	await shown(OWED);

	expect(words()).toContain(COPY.status.on);
	expect(words()).toContain('when the task below runs');
	expect(words()).not.toContain('Look up the files already fingerprinted');
	expect([...host.querySelectorAll('button')].map((one) => wordsOn(one))).not.toContain('Run now');
	expect(post).not.toHaveBeenCalled();
});

it('reads its counts again as the work moves, on the work bell and the library bell', async () => {
	/* A block that re-read only when a setting moved would leave the count beside the lookup task
	 * standing still through a whole run. Every answer kept rings the work bell, and a named song
	 * the library bell. */
	let owed = 63;
	await shown({ on: true, key_set: true, key_ready: true, route: null, owed });
	const lookups = () =>
		vi.mocked(api.get).mock.calls.filter(([path]) => path === '/music/lookup').length;
	const before = lookups();
	vi.mocked(api.get).mockImplementation(async (path: string) => {
		if (path.startsWith('/tunnels')) return [] as never;
		if (path === '/music/lookup')
			return { on: true, key_set: true, key_ready: true, route: null, owed } as never;
		throw new Error(`unexpected read of ${path}`);
	});

	owed = 41;
	jobChanges.changed();
	await settle();
	expect(lookups()).toBe(before + 1);
	expect(drawnLookup().state?.owed).toBe(41);

	owed = 40;
	libraryChanges.changed();
	await settle();
	expect(lookups()).toBe(before + 2);
	expect(drawnLookup().state?.owed).toBe(40);
});
