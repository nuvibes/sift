/* The stash-box block on the Stash-boxes section.
 *
 * Two things are worth a test here and the rest is layout. The first is the key: it is typed into
 * a box that is never re-filled from anything, and no part of what this screen holds may carry one
 * afterwards. The second is the look-up, which is the surface somebody uses to find out whether a
 * key works, so what it does with a box that could not be reached, and with a box that answered
 * with nothing, have to read as two different sentences rather than as the same blank.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { api } from '$lib/api/client';
import { compile } from 'svelte/compiler';
import StashBoxes from './StashBoxes.svelte';
import source from './StashBoxes.svelte?raw';

/* The whole wire shape, not the half this file happens to read.
 *
 * `key_ready` above all. A box with a key that cannot be OPENED is a different state from a box
 * with no key, and the pane has a third sentence for it, so a fixture that leaves the field out is
 * a box whose key is sealed, and a test asking whether a saved key is announced would be reading
 * the sentence about a locked one. A partial fixture is a fixture that answers a
 * question nobody asked. */
const A_BOX = {
	id: 'b1',
	name: 'StashDB',
	endpoint: 'https://stashdb.org/graphql',
	enabled: true,
	has_key: true,
	key_ready: true,
	route: null,
	sites_are: 'site',
	requests_per_minute: 240
};

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

async function shown(boxes: unknown[] = [A_BOX]): Promise<HTMLElement> {
	/* Answered per route rather than with one shape for everything.
	 *
	 * This pane reads the TUNNELS too, to name the ways out of the machine. A catch-all that
	 * handed `{ boxes }` to that request as well would put an object where a list goes, and the
	 * screen would throw while mapping it: unhandled errors in a run whose own assertions all
	 * pass, because the throw happens inside an effect rather than inside a test. */
	vi.spyOn(api, 'get').mockImplementation(async (path: string) => {
		if (path.startsWith('/tunnels')) return [] as never;
		if (path.startsWith('/download-routes'))
			return { default: 'direct', sites: {}, available: [] } as never;
		if (path.startsWith('/stash-boxes/look-up')) return { answers: [] } as never;
		return { boxes } as never;
	});
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(StashBoxes, { target: host }) as Record<string, unknown>;
	flushSync();
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
	return host;
}

beforeEach(() => {
	vi.restoreAllMocks();
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
	vi.restoreAllMocks();
});

it('offers the key as a password box, so it is never on screen as it is typed', async () => {
	const screen = await shown();

	const key = screen.querySelector('input[type="password"]');
	expect(key).not.toBeNull();
	// And it starts empty rather than pre-filled from anything. There is nothing to pre-fill it
	// from (the server has no shape that carries a key outward), and a box that looked filled
	// would say otherwise.
	expect((key as HTMLInputElement).value).toBe('');
});

it('says a box has a key rather than showing one', async () => {
	const screen = await shown();

	expect(screen.textContent).toContain('Key saved');
	expect(screen.textContent).not.toContain('api_key');
});

it('tells a key that cannot be opened from a box that has none', async () => {
	// Three states, not two. A restart leaves every saved key sealed and nothing else wrong, and
	// "No key" there sends somebody to type in one they already have, while "Key saved" beside a
	// box that answers nothing is a screen saying it is healthy while it refuses every question.
	const screen = await shown([{ ...A_BOX, has_key: true, key_ready: false }]);

	expect(screen.textContent).toContain('Key locked');
	expect(screen.textContent).not.toContain('Key saved');
	expect(screen.textContent).not.toContain('No key');
});

it('says so plainly when a box has no key, because an unkeyed box is simply not asked', async () => {
	const screen = await shown([{ ...A_BOX, has_key: false }]);

	expect(screen.textContent).toContain('No key');
});

it('says a switched-off box by its switch, because off is a state and not a failure', async () => {
	const screen = await shown([{ ...A_BOX, enabled: false }]);

	// The switch is the state and the way back, as on a tunnel's row: no pill beside it saying
	// the same thing again.
	const use = screen.querySelector('[role="switch"][aria-label="Use StashDB"]');
	expect(use?.getAttribute('aria-checked')).toBe('false');
	expect(screen.textContent).not.toContain('Turned off');
});

it('draws each record as a row of the pane, never a box of its own', () => {
	// The record's rule, as compiled: no border around it and no rounded box, and the only line a
	// hairline between two records, drawn by the lower one.
	const css = compile(source, { filename: 'StashBoxes.svelte', css: 'external' }).css?.code ?? '';
	const record = [...css.matchAll(/([^{}]*)\{([^}]*)\}/g)]
		.filter((found) => /\.boxes\b[^,{]*> li\b/.test(found[1]) && !found[1].includes('+'))
		.map((found) => found[2])
		.join('');
	expect(record).toContain('padding-block');
	expect(record).not.toMatch(/border|radius/);
	expect(css).toMatch(/> li[^{]*\+ li[^{]*\{\s*border-block-start: 1px solid/);
});

it('offers the three boxes Sift has been checked against as a starting point', async () => {
	const screen = await shown([]);

	const names = [...screen.querySelectorAll('button')].map((one) => one.textContent?.trim());
	expect(names).toEqual(expect.arrayContaining(['StashDB', 'FansDB', 'PMVStash']));
});

it('says nothing is configured rather than drawing an empty list', async () => {
	// A blank space where a list belongs reads as a screen that failed to load.
	const screen = await shown([]);

	expect(screen.textContent).toContain('None yet');
});

it('promises, in its own words, that nothing here is written to a stash-box', async () => {
	// The copy is the feature. Somebody handing a third-party key to a program has to be able to
	// read what it will do with it, and "only ever reads" is the whole of the answer.
	const screen = await shown();

	// Whitespace flattened first: the copy is wrapped across lines in the markup, so the words are
	// separated by a newline and two tabs on screen and by one space to a reader.
	const said = (screen.textContent ?? '').replace(/\s+/g, ' ');
	expect(said).toContain('only ever reads');
	expect(said).toContain('Nothing here is saved to your library');
});
