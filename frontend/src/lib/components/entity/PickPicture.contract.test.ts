/*
 * Everything that has a cover can have one chosen, from its own page.
 *
 * Six things in Sift carry a `cover_asset_id`: a person, a Site, an account, a tag, a photo set and
 * a collection. Five have a page to choose it from; an account has none (see `UNCOVERED`).
 * Right-clicking a file on a Files tab for "Use as the cover" works but is not a way anybody finds,
 * so each page carries the chooser.
 *
 * A "two lists that must agree" guard: the list on the server is the columns, the list here is the
 * pages. A seventh cover column with no page fails, and so does a page that quietly loses its
 * chooser.
 */
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

/*
 * Cover columns with no page to choose them from, each by decision and with its reason. A table
 * leaving `COVERED` has to arrive here, so the count below still names all of them. Empty: every
 * cover column there is has a page.
 */
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
			// One way to set a cover, reached from two places. The right-click on the Files tab and the
			// pencil both go through the same call, so a fix to one is a fix to both, and neither can
			// come to mean something the other does not.
			const source = readFileSync(page, 'utf8');
			expect(source).toMatch(/setCover|\/cover/);
		}
	);

	it('checks every cover column there is', () => {
		/*
		 * A list of pages beside the thing it describes drifts, and a short list looks finished. So
		 * it is counted against the server's list: the schema is where a cover column comes into
		 * existence, and a seventh fails here until it has a page.
		 *
		 * Counted as tables rather than occurrences of the column, which differ: three of the six
		 * arrive by `ALTER` on a table whose `CREATE` also declares it, and there is a
		 * `photo_sets_rebuilt` copy from the migration that reshaped that table.
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
		// Changing the picture does not require Edit first: the gesture for changing a thing you
		// are looking at is to press it, and requiring Edit would hide the sheet from anybody who
		// did not already know it existed.
		const header = readFileSync('src/lib/components/entity/EntityCover.svelte', 'utf8');
		expect(header).toContain('{#if mayEdit && onpicture}');
		expect(header).not.toContain('{#if editing && mayEdit && onpicture}');
	});

	it('is reachable by the keyboard, not only by a pointer', () => {
		// The Reveal register: a control that exists only under a cursor does not exist for
		// half the people using Sift. It is in the tab order whether or not it can be seen, so it has
		// to show itself when it is focused.
		const header = readFileSync('src/lib/components/entity/EntityCover.svelte', 'utf8');
		expect(header).toContain(':global(.pencil:focus-visible)');
	});
});
