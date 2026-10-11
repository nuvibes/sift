/* The screen's row holds a filled window's menus whole, so the title under it never slides. */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, it } from 'vitest';

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
