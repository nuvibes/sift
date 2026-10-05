/*
 * A CELL'S SAVE ROW IS THE SHARED `save` VERB, not a row of its own.
 *
 * Every right-click in Sift says "Save to device", or "Copy image" where a still goes to the
 * clipboard; a row written out here would be one request with two spellings. The label and the
 * action are declared together in `grid/verbs.ts`; a cell taking the verb cannot come to word it
 * differently.
 *
 * Read off the source, because the menu is the library's and cannot be opened in jsdom (the same
 * limit `PresetPill`'s test records). What is pinned is that the row comes from the declaration and
 * that no hand-written save row is left beside it.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, it } from 'vitest';

const here = dirname(fileURLToPath(import.meta.url));
/*
 * Markup and script only, with comments taken out, so words in a note can never satisfy a match
 * meant for the menu.
 */
const source = readFileSync(join(here, 'CellMenu.svelte'), 'utf8')
	.replace(/<!--[\s\S]*?-->/g, '')
	.replace(/\/\*[\s\S]*?\*\//g, '');

it('takes its save row from the shared verbs', () => {
	expect(source).toMatch(/all\.find\(\(one\) => one\.id === 'save'\)/);
	expect(source).toMatch(/<VerbMenuItems verbs=\{\[save\]\}/);
});

it('writes no save row of its own', () => {
	expect(source, 'a hand-written save row is back beside the shared one').not.toMatch(
		/label="Save[^"]*"/
	);
	expect(source).not.toMatch(/saveToDevice/);
});

it("offers to close the strip from a preview's own menu", () => {
	const preview = source.slice(
		source.indexOf('{#if wall.isPreview(index)}'),
		source.indexOf('{:else}')
	);
	expect(preview).toMatch(/label="Close the strip"[^>]*onselect=\{\(\) => wall\.closeStrip\(\)\}/);
});
