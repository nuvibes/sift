/* Four cards are two rows of two, never three and one left over. */
import { expect, it } from 'vitest';
import { compile } from 'svelte/compiler';
import source from './ChoiceGroup.svelte?raw';
import card from './ChoiceCard.svelte?raw';

const css = compile(source, { filename: 'ChoiceGroup.svelte', css: 'external' }).css?.code ?? '';

it('keeps a track of four cards no narrower than half the row', () => {
	expect(css).toMatch(
		/:has\(> :nth-child\(4\):last-child\) \{\s*grid-template-columns: repeat\(auto-fit, minmax\(max\(220px, calc\(50% - var\(--space-3\)\)\), 1fr\)\)/
	);
});

it('stands every card name at the same height across a row, whatever its note', () => {
	const cardCss = compile(card, { filename: 'ChoiceCard.svelte', css: 'external' }).css?.code ?? '';
	expect(cardCss).toMatch(/\.card\.[\w-]+ \{[^}]*display: grid;[^}]*align-content: start;/);
});
