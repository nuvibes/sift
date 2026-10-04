/* A list on the pane's edges draws the line between two rows on those edges too: the item is
 * pulled out to the hover ground's extent, so the line is painted inset by the same amounts. */
import { expect, it } from 'vitest';
import { compile } from 'svelte/compiler';
import source from './DataRows.svelte?raw';

const css = compile(source, { filename: 'DataRows.svelte', css: 'external' }).css?.code ?? '';

it('paints the line inside the pane edges, never at the hover extent', () => {
	const rule = [
		...css.matchAll(/\.rows\.edges(?:\.svelte-[a-z0-9]+)? > \.line \+ \.line \{([^}]*)\}/g)
	]
		.map((found) => found[1])
		.find((body) => body.includes('background'));
	expect(rule).toBeDefined();
	expect(rule).toContain('border-block-start-color: transparent');
	expect(rule).toContain('background-position: var(--space-2) 0');
	expect(rule).toContain('calc(100% - var(--space-2) - var(--space-3)) 1px');
});
