/*
 * Every kind of row the search list can draw has a glyph and a word for it.
 *
 * The list draws a row for every field of the query language (a filter row, or a match inside a
 * token), for a remembered search, a file, the words themselves and Show more. The word is the
 * glyph's tooltip and its name to a screen reader, and a kind missing from the table draws a
 * glyph that says nothing to anybody who does not know the shape. The field list is
 * read from the server's own declaration, so a field added there without a word here fails here.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { ICON_NAMES } from '$lib/design/icons';
import { QUERY_KIND, type Row } from '$lib/search/search.svelte';
import { FILE_KIND, MORE_KIND, ROW_KINDS, WORDS_KIND, kindOf, rowKind } from './search-kinds';

const here = dirname(fileURLToPath(import.meta.url));

/** The tokens `filter_fields.py` declares on `Field`, in the order it declares them. */
function serverFields(): string[] {
	const source = readFileSync(
		join(here, '../../../../../src/sift/slices/search/filter_fields.py'),
		'utf8'
	);
	const body = source.slice(source.indexOf('class Field(StrEnum):'));
	const end = body.search(/\n(?:class |def |[A-Z_]+ *[:=])/);
	return [...body.slice(0, end).matchAll(/^ {4}[A-Z_]+ = "([a-z_]+)"$/gm)].map((one) => one[1]);
}

describe('the kinds of row on the search list', () => {
	it('reads the server field list, so the check below has something to hold', () => {
		const fields = serverFields();
		expect(fields).toContain('people');
		expect(fields).toContain('acodec');
		expect(fields.length).toBeGreaterThan(30);
	});

	it('has a word and a glyph of its own for every field the query language has', () => {
		const missing = serverFields().filter((field) => ROW_KINDS[field] === undefined);
		expect(missing).toEqual([]);
		for (const field of serverFields()) {
			const kind = kindOf(field);
			expect(kind.name.trim(), field).not.toBe('');
			expect(kind.name, field).not.toBe('Filter');
			expect(ICON_NAMES as readonly string[], field).toContain(kind.icon);
		}
	});

	it('has a word for the rows that are no field', () => {
		expect(kindOf(QUERY_KIND).name).toBe('Recent search');
		expect(kindOf(FILE_KIND).name).toBe('File');
		expect(kindOf(WORDS_KIND).name).toBe('Words');
		expect(kindOf(MORE_KIND).name).toBe('More');
	});

	it('names the things a row offers in the singular and the rest by their filter label', () => {
		expect(kindOf('people').name).toBe('Person');
		expect(kindOf('sites').name).toBe('Site');
		expect(kindOf('photo_sets').name).toBe('Photo Set');
		expect(kindOf('in').name).toBe('Folder');
		expect(kindOf('acodec').name).toBe('Audio codec');
		expect(kindOf('vcodec').name).toBe('Video codec');
	});

	it('reads each row by what it is', () => {
		const person: Row = {
			kind: 'match',
			match: { value: 'Ada Lumen', field: 'people' } as never
		};
		const file: Row = { kind: 'match', match: { value: 'a.mp4', opens: 'file' } as never };
		const searched: Row = {
			kind: 'recent',
			remembered: { kind: QUERY_KIND, subject: 'beach', label: 'beach' }
		};
		const picked: Row = {
			kind: 'recent',
			remembered: { kind: 'sites', subject: '01X', label: 'Somewhere' }
		};
		const filter: Row = {
			kind: 'filter',
			filter: { field: 'acodec', label: 'Audio codec', hint: '', example: '' }
		};
		expect(rowKind(person)).toEqual({ name: 'Person', icon: 'person' });
		expect(rowKind(file).name).toBe('File');
		expect(rowKind(searched)).toEqual({ name: 'Recent search', icon: 'history' });
		expect(rowKind(picked).name).toBe('Site');
		expect(rowKind(filter).name).toBe('Audio codec');
		expect(rowKind({ kind: 'text', query: 'beach' }).name).toBe('Words');
		expect(rowKind({ kind: 'more', group: 'match', reveals: 5 }).name).toBe('More');
	});

	it('says a field it does not know is a filter rather than dressing it as a tag', () => {
		expect(kindOf('no_such_field')).toEqual({ name: 'Filter', icon: 'filter_alt' });
	});
});
