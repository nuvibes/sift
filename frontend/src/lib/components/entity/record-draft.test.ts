import { describe, expect, it, vi } from 'vitest';

/* The registry is the real question's input, so it is stood in for with a short list of keys: what
   is under test is which of them reach the entity's own row, not what the server's registry holds
   today. */
const registry = vi.hoisted(() => ({
	keys: {
		person: ['name', 'details', 'aliases', 'birthdate', 'nationality', 'age', 'tags'],
		site: ['name', 'url']
	} as Record<string, string[]>
}));
vi.mock('$lib/entity/records.svelte', () => ({
	fields: { of: (subject: string) => (registry.keys[subject] ?? []).map((key) => ({ key })) }
}));

const { recordFrom } = await import('./record-draft');

describe("a record form's draft, split for the entity's own row", () => {
	it('keeps out what a person saves somewhere else, and sends every column of the row', () => {
		const record = recordFrom('person', {
			name: 'Someone',
			details: 'words',
			aliases: ['Other'],
			birthdate: '1990-01-01',
			age: 35,
			tags: ['t1']
		});

		// The name and details go as named arguments, the aliases and tags as rows of their own, and
		// the age is computed: none of them is a column the record write may carry.
		expect(record).toEqual({ birthdate: '1990-01-01', nationality: null });
	});

	it('sends an emptied or absent field as null, because silence means leave it alone', () => {
		expect(recordFrom('person', { birthdate: undefined })).toEqual({
			birthdate: null,
			nationality: null
		});
	});

	it('keeps every field for a kind with nothing saved apart', () => {
		expect(recordFrom('site', { name: 'A site' })).toEqual({ name: 'A site', url: null });
	});
});
