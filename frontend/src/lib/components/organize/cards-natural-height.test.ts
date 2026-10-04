/*
 * Every Organize card stands at its own height: no floor on a card, so a short one carries no band
 * of empty ground, and the grid gives the cards of one row one height.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { expect, it } from 'vitest';
import filed from './FiledPanel.svelte?raw';
import shoots from './ShootsPanel.svelte?raw';
import folders from '../suggestions/FolderSuggestions.svelte?raw';

/* Read from the file: a stylesheet imported `?raw` arrives EMPTY in this environment, and the
   check at the foot would then pass on nothing. */
const tokens = readFileSync(resolve('src/app.css'), 'utf8');

/** The declarations of the one rule `selector` names in a component's stylesheet. */
function ruleOf(source: string, selector: string): string {
	const style = /<style>([\s\S]*)<\/style>/.exec(source)?.[1] ?? '';
	const at = style.indexOf(`${selector} {`);
	expect(at, selector).toBeGreaterThan(-1);
	return style.slice(at, style.indexOf('}', at));
}

it('puts no floor under any card', () => {
	for (const [source, selector] of [
		[filed, '.people > li'],
		[shoots, '.wall > li'],
		[folders, '.cards > li']
	] as const) {
		const rule = ruleOf(source, selector);
		expect(rule, selector).toContain('display: grid');
		expect(rule, selector).not.toMatch(/min-block-size|min-height/);
	}
	expect(tokens).toContain('--board-column-min');
	expect(tokens).not.toContain('--decision-card-height');
});
