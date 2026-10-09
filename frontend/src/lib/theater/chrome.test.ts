/* The screen's row fades with the wall's chrome, except while it holds the menus in a window. */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, it } from 'vitest';

import { rowQuiet } from './chrome.svelte';

it('stays drawn while it holds the menus in a window, wherever the pointer is', () => {
	expect(rowQuiet(false, false, false), 'the menus could not be seen or pressed').toBe(false);
	expect(rowQuiet(true, false, false)).toBe(false);
});

it('fades with the chrome in full screen, and in a window whose top bar has the menus', () => {
	expect(rowQuiet(false, true, false)).toBe(true);
	expect(rowQuiet(false, true, true)).toBe(true);
	expect(rowQuiet(false, false, true)).toBe(true);
	expect(rowQuiet(true, true, false)).toBe(false);
});

it('holds the menus whole immediately, so the title under it never slides', () => {
	const source = readFileSync(
		join(dirname(fileURLToPath(import.meta.url)), '../components/shell/FilterBar.svelte'),
		'utf8'
	).replace(/\t/g, '');
	expect(source, 'the row eased in with the menus and carried the title down').toContain(
		'.bar-clip:has(> .bar.handed) {\ngrid-row: 2;\n}'
	);
	expect(source, 'the menus dropped in on the spring inside a still row').toContain(
		'.bar-clip:has(> .bar.handed) > .bar {\ntransition: opacity var(--dur-slow) var(--ease);\n}'
	);
});
