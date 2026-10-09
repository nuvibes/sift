/* Everything with a cover can have one chosen from its own page: the server's cover columns
 * checked against the pages that carry the chooser. */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

/** Every entity that carries a cover, and the page somebody sets it from. */
const COVERED: { what: string; page: string }[] = [
	{ what: 'a person', page: 'src/routes/people/[id]/+page.svelte' },
	{ what: 'a Site', page: 'src/routes/sites/[id]/+page.svelte' },
	{ what: 'a tag', page: 'src/routes/tags/[id]/+page.svelte' },
	{ what: 'a photo set', page: 'src/routes/photo-sets/[id]/+page.svelte' },
	{ what: 'a collection', page: 'src/routes/collections/[id]/+page.svelte' },
	{ what: 'a song', page: 'src/routes/songs/[id]/+page.svelte' }
];

/* Cover columns with no page, each with its reason; empty today. */
const UNCOVERED: { table: string; why: string }[] = [];

describe('a cover can be chosen wherever there is one', () => {
	it.each(COVERED)('$what opens the chooser from its own page', ({ page }) => {
		const source = readFileSync(page, 'utf8');
		expect(source, 'no chooser on this page').toContain('<PickPicture');
		// The pencil is what opens it. A sheet nothing raises is the same as no sheet.
		expect(source, 'nothing opens the chooser').toContain('onpicture=');
	});

	it.each(COVERED)(
		'$what writes its cover through a route rather than a second store',
		({ page }) => {
			// One way to set a cover, from the pencil and the Files tab alike.
			const source = readFileSync(page, 'utf8');
			expect(source).toMatch(/setCover|\/cover/);
		}
	);

	it('checks every cover column there is', () => {
		/*
		 * Counted as tables in the schema, not as occurrences of the column (ALTERs, a rebuilt
		 * copy).
		 */
		const schema = readFileSync('../src/sift/kernel/access/schema.py', 'utf8');
		const tables = new Set<string>();
		for (const block of schema.split(/CREATE TABLE (?:IF NOT EXISTS )?/).slice(1)) {
			const name = block.split(/[\s(]/)[0];
			const body = block.slice(0, block.indexOf(')\n'));
			if (body.includes('cover_asset_id') && !name.endsWith('_rebuilt')) tables.add(name);
		}
		for (const match of schema.matchAll(/ALTER TABLE (\w+) ADD COLUMN cover_asset_id/g)) {
			tables.add(match[1]);
		}
		expect([...tables].sort()).toEqual([
			'collections',
			'people',
			'photo_sets',
			'sites',
			'songs',
			'tags'
		]);
		expect(tables.size).toBe(COVERED.length + UNCOVERED.length);
		for (const { table } of UNCOVERED) expect(tables.has(table), table).toBe(true);
	});
});

describe('the pencil is findable', () => {
	it('does not wait for the record to be put into edit mode', () => {
		// The picture is changed by pressing it, without Edit first.
		const header = readFileSync('src/lib/components/entity/EntityCover.svelte', 'utf8');
		expect(header).toContain('{#if mayEdit && onpicture}');
		expect(header).not.toContain('{#if editing && mayEdit && onpicture}');
	});

	it('is reachable by the keyboard, not only by a pointer', () => {
		// The Reveal register: in the tab order, so it shows itself on focus.
		const header = readFileSync('src/lib/components/entity/EntityCover.svelte', 'utf8');
		expect(header).toContain(':global(.pencil:focus-visible)');
	});
});
