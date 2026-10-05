/* An empty Browse names what emptied it, words, filters or both, and its press clears just that. */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

import { clearWallSays, emptyWallSays } from '$lib/components/shell/wall-words';
import { cleared, filteredBy, questionIn } from './question';

function said(search: string): { sentence: string; press: string } {
	const question = questionIn(new URLSearchParams(search), ['folders']);
	const words = (question.q ?? '').trim();
	const filters = filteredBy(question);
	return {
		sentence: emptyWallSays('files', words, filters, 'Nothing here matches.'),
		press: clearWallSays(words, filters)
	};
}

describe('an empty Browse', () => {
	it('names the words alone, whatever order or page the address is at', () => {
		expect(said('?q=wren&sort=oldest&seed=7&offset=80&meaning=1&from=01ABC&near=4')).toEqual({
			sentence: 'No files match "wren".',
			press: 'Clear the search'
		});
	});

	it('names the filters alone', () => {
		expect(said('?tags=beach&sort=oldest&folders=1')).toEqual({
			sentence: 'No files match these filters.',
			press: 'Clear the filters'
		});
	});

	it('names both', () => {
		expect(said('?q=wren&people=01PERSON')).toEqual({
			sentence: 'No files match "wren" with these filters.',
			press: 'Clear the search and filters'
		});
	});

	it('counts a folder as a filter and its depth as part of it', () => {
		expect(filteredBy({ in: 'f1', depth: '1' })).toBe(true);
		expect(filteredBy({ depth: '1', sort: 'oldest' })).toBe(false);
	});

	it('hands the wall those words, read from the page', () => {
		const page = readFileSync('src/routes/browse/+page.svelte', 'utf8');
		expect(page).toContain('const filters = $derived(filteredBy(query));');
		expect(page).toContain("const filtered = $derived(typedWords !== '' || filters);");
		expect(page).toContain(
			"? emptyWallSays('files', typedWords, filters, 'Nothing here matches.')"
		);
		expect(page).toContain('>{clearWallSays(typedWords, filters)}</Button>');
	});
});

describe('what each clear press keeps', () => {
	const after = (search: string, words: boolean, filters: boolean) =>
		`${cleared(new URLSearchParams(search), { words, filters }, ['folders'])}`;

	it('Clear the search takes the words and keeps the order', () => {
		expect(
			after('?q=wren&sort=random&seed=7&meaning=1&offset=80&from=01ABC&near=4', true, false)
		).toBe('sort=random&seed=7&meaning=1');
	});

	it('Clear the filters takes every filter and keeps the order and the open folder', () => {
		expect(
			after(
				'?tags=beach&in=f1&depth=1&people=01PERSON&sort=oldest&folders=f1&from=01ABC',
				false,
				true
			)
		).toBe('sort=oldest&folders=f1');
	});

	it('Clear the search and filters takes both and keeps the order', () => {
		expect(after('?q=wren&tags=beach&sort=oldest', true, true)).toBe('sort=oldest');
	});

	it('is what the press goes to', () => {
		const page = readFileSync('src/routes/browse/+page.svelte', 'utf8');
		expect(page).toContain('<Button onclick={() => void goto(clearedTo)}>');
		expect(page).toContain(
			"cleared(page.url.searchParams, { words: typedWords !== '', filters }, [EXPLORER])"
		);
	});
});
