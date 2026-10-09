/*
 * THE KEYS PANEL lists exactly Theater's and "Anywhere"'s keys, and every key the screen reads is
 * among them.
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
	const read = [
		...screen.matchAll(
			/(?:matches\(event, |shortcut\()'([a-zA-Z.]+)'|'(theater\.[a-zA-Z]+)':\s*\(/g
		)
	].map((one) => one[1] ?? one[2]);
	// Known positives, so a pattern that stopped matching cannot pass.
	expect(read).toContain('theater.fill');
	expect(read).toContain('app.dismiss');

	const onThePanel = FOR_THEATER.map((one) => one.id as string);
	const missing = read.filter((id) => !onThePanel.includes(id));
	expect(missing, 'the screen reads keys its own key panel never lists').toEqual([]);
});

/* THE PANEL STAYS UNDER THE BAR: no class on its box may be one the app dresses globally. */
it('wears no class the app stylesheet dresses globally', () => {
	const here = dirname(fileURLToPath(import.meta.url));
	const css = readFileSync(join(here, '../../../app.css'), 'utf8');
	const global = new Set([...css.matchAll(/^\.([a-z][a-z0-9-]*)/gm)].map((one) => one[1]));
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
