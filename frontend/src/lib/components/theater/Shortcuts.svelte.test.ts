/*
 * THE KEYS PANEL LISTS EVERY KEY THAT WORKS IN THEATER, generated from the declarations.
 *
 * A key named by hand is a key list that misses others, such as Escape, read by the Theater screen
 * itself, and the three lock keys. Two halves are held here. The panel is exactly the declared keys
 * of two areas (Theater's, and "Anywhere", which the shell handles on every screen). And every key
 * the screen READS is among them, found in the screen's own source: a key the screen starts reading
 * from some other area fails here instead of silently going unlisted.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterEach, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import Shortcuts from './Shortcuts.svelte';
import { SHORTCUTS } from '$lib/shell/shortcuts';

let host: HTMLElement | undefined;
let running: Record<string, unknown> | undefined;

afterEach(() => {
	if (running) void unmount(running);
	running = undefined;
	host?.remove();
});

/** What the panel draws, one row per key: the keys as shown and the sentence beside them. */
function listed(): string[] {
	host = document.createElement('div');
	document.body.append(host);
	running = mount(Shortcuts, { target: host });
	flushSync();
	return [...host.querySelectorAll('dt')].map(
		(key) => `${key.textContent}: ${key.nextElementSibling?.textContent}`
	);
}

const FOR_THEATER = SHORTCUTS.filter((one) => one.area === 'Theater' || one.area === 'Anywhere');

it('lists exactly the keys declared for Theater and for anywhere', () => {
	const wanted = FOR_THEATER.map((one) => `${one.shown}: ${one.does}`);
	const drawn = listed();
	expect([...drawn].sort()).toEqual([...wanted].sort());
});

it('lists every key the Theater screen reads', () => {
	const here = dirname(fileURLToPath(import.meta.url));
	const screen = readFileSync(join(here, '../../../routes/theater/+page.svelte'), 'utf8');
	// A key is read two ways: matched or looked up by name, and as a row of the surface's own table
	// of actions (the same table the phone's remote reads).
	const read = [
		...screen.matchAll(
			/(?:matches\(event, |shortcut\()'([a-zA-Z.]+)'|'(theater\.[a-zA-Z]+)':\s*\(/g
		)
	].map((one) => one[1] ?? one[2]);
	// Known positives, so a pattern that stopped matching cannot pass on nothing found.
	expect(read).toContain('theater.fill');
	expect(read).toContain('app.dismiss');

	const onThePanel = FOR_THEATER.map((one) => one.id as string);
	const missing = read.filter((id) => !onThePanel.includes(id));
	expect(missing, 'the screen reads keys its own key panel never lists').toEqual([]);
});

/*
 * THE PANEL STAYS UNDER THE BAR.
 *
 * A box wearing a class the app's stylesheet gives to its dialogs (fixed, centred on the window, as
 * tall as the window) would lift the panel out of the bar and run it off the bottom of the screen
 * with nothing to scroll it. No class on the panel's own box may be one the app's stylesheet
 * dresses globally.
 */
it('wears no class the app stylesheet dresses globally', () => {
	const here = dirname(fileURLToPath(import.meta.url));
	const css = readFileSync(join(here, '../../../app.css'), 'utf8');
	const global = new Set([...css.matchAll(/^\.([a-z][a-z0-9-]*)/gm)].map((one) => one[1]));
	// A known positive, so a pattern that stopped matching cannot pass on an empty set.
	expect(global).toContain('sheet');

	listed();
	const root = host?.firstElementChild;
	const own = [...(root?.classList ?? [])].filter((name) => !name.startsWith('svelte-'));
	expect(own.length).toBeGreaterThan(0);
	expect(own.filter((name) => global.has(name))).toEqual([]);
});

it('scrolls its rows inside itself rather than growing past the window', () => {
	listed();
	const root = host?.firstElementChild as HTMLElement;
	expect(
		root.querySelector('.scroll-root dl'),
		'the rows are not inside the scroller'
	).not.toBeNull();
	expect(root.style.getPropertyValue('--key-panel-top')).toMatch(/^\d+(\.\d+)?px$/);
});
