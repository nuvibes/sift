/* The sentence over People Sift can recognize says who is on the list. */
import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';

import { COPY } from './Faces.search';

it('says a person with starter pictures is on the list, as the list shows', () => {
	const pane = readFileSync('src/lib/settings-ui/Faces.svelte', 'utf8');
	expect(pane).toMatch(/\{ id: 'starters', label: 'Starters'/);
	expect(COPY.lists.people.help).toContain('confirmed faces or starter pictures');
	expect(COPY.lists.people.help).not.toContain('A person with none');
});
