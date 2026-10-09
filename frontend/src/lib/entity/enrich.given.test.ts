/* Where each field of a record came from, as its hover says it: read off the links' `gave`,
 * which the server holds only while a value is still the one that box gave. */
import { expect, it } from 'vitest';
import { givenBy, type StashBoxLink } from './enrich.svelte';

function link(box_name: string, gave: string[], fetched_at: number): StashBoxLink {
	return { box_name, gave, fetched_at } as unknown as StashBoxLink;
}

it('says which box gave a field and when it fetched it, the first box to claim it winning', () => {
	const said = givenBy([
		link('StashDB', ['height_cm', 'birth_date'], 1_758_800_000),
		link('FansDB', ['height_cm', 'measurements'], 1_758_800_000)
	]);

	expect(Object.keys(said).sort()).toEqual(['birth_date', 'height_cm', 'measurements']);
	expect(said.height_cm).toMatch(/^From StashDB, fetched \w{3} \d{1,2}, \d{4}$/);
	expect(said.measurements).toMatch(/^From FansDB, fetched /);
});

it('says nothing for a record no box gave anything to', () => {
	expect(givenBy([link('StashDB', [], 1)])).toEqual({});
	expect(givenBy([])).toEqual({});
});
