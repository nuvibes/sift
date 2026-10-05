/* A collection's empty wall names what emptied it, words, bar filters or both, and its press too. */
import { expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

const page = readFileSync('src/routes/collections/[id]/+page.svelte', 'utf8');

it('counts the bar filters apart from the words and the picks', () => {
	expect(page).toContain(
		"const filtered = $derived(Object.keys(filters).some((one) => one !== 'q' && !(one in narrowing)));"
	);
	expect(page).toContain("title={emptyWallSays('files', fileWords.asked, filtered, '')}");
	expect(page).toContain(
		'>{clearWallSays(fileWords.asked, filtered)} to see the whole collection.'
	);
});
