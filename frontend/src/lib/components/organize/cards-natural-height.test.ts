/*
 * Every Organize list of cards is the one wall (`CardWall`): one column rule, one row height, and
 * no grid of a wall's own beside it.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { expect, it } from 'vitest';
import wall from './CardWall.svelte?raw';
import filed from './FiledPanel.svelte?raw';
import shoots from './ShootsPanel.svelte?raw';
import studios from './StudiosPanel.svelte?raw';
import suggestions from './SuggestionsPanel.svelte?raw';
import identified from './IdentifiedPanel.svelte?raw';
import groups from '../faces/FaceGroups.svelte?raw';
import folders from '../suggestions/FolderSuggestions.svelte?raw';
import board from '../../../routes/organize/+page.svelte?raw';

/* Read from the file: a stylesheet imported `?raw` arrives EMPTY in this environment. */
const tokens = readFileSync(resolve('src/app.css'), 'utf8');

it('lays every Organize wall of cards on the one wall, with no grid of its own', () => {
	for (const [name, source] of Object.entries({
		filed,
		shoots,
		studios,
		suggestions,
		identified,
		groups,
		folders,
		board
	})) {
		expect(source, name).toContain('<CardWall');
		// The walls' old columns: a card strip inside a card keeps its own grid.
		expect(source, name).not.toMatch(
			/minmax\((min\()?(22|16|15\.25)rem|columns: \d|board-column-min/
		);
	}
});

it('gives every row one height and every card one width, at the cards column', () => {
	expect(wall).toContain(
		'grid-template-columns: repeat(auto-fill, minmax(min(var(--wall-column), 100%), 1fr));'
	);
	expect(wall).toContain('grid-auto-rows: 1fr;');
	expect(wall).toContain('--wall-column: var(--card-column-min);');
	expect(tokens).toContain('--card-column-min: 22rem;');
	expect(tokens).not.toContain('--decision-card-height');
});
