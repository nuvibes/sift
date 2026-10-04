/** What the settings-search gate reads, driven with panes written for the purpose.
 *
 * `scripts/check_settings_search_covers_panes.js` holds every name a settings pane draws on a row
 * or heading to an entry in some pane's `SEARCHABLE`, the words READ through the constant the pane
 * draws them from. A row that is drawn and never declared is a row the settings search cannot
 * find ("create a pers" finding nothing); this is the rule that says so.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';

import {
	declaredNames,
	loadSearchModule,
	namesIn,
	said,
	unfoundIn
} from '../../../scripts/lib/settings-search-covers.js';
import { everySection, grouped, indexOf } from '../settings-ui/search';

const PANES = join(process.cwd(), 'src', 'lib', 'settings-ui');

/** A stand-in for the pane folder: two search modules and whatever a test writes as a pane. */
const MODULES: Record<string, string> = {
	'Made.search': [
		"import { counted } from '$lib/entity/entity-counts';",
		"import { COPY as OTHER } from './Other.search';",
		'export const COPY = {',
		"\tpack: { create: 'Create a person for each name', many: (n: number) => `${counted(n)} people` },",
		'\tborrowed: OTHER.word',
		'} as const;',
		"export const SEARCHABLE = [{ name: COPY.pack.create, section: 'faces' }];"
	].join('\n'),
	'Other.search': "export const COPY = { word: 'Borrowed' };\nexport const SEARCHABLE = [];"
};

const load = (module: string) => loadSearchModule(module, (name: string) => MODULES[name]);
const declared = declaredNames(Object.keys(MODULES), load);

const pane = (markup: string) =>
	[
		'<script lang="ts">',
		"\timport { COPY } from './Made.search';",
		'\tconst PACK = COPY.pack;',
		"\tconst HEADING = 'A heading of its own';",
		'</script>',
		markup
	].join('\n');

it('reads a name through the constant the pane draws it from', () => {
	expect(declared.has(said('Create a person for each name'))).toBe(true);
	expect(
		unfoundIn(pane('<LabelledRow label={COPY.pack.create} help="x" />'), declared, load)
	).toEqual([]);
	expect(unfoundIn(pane('<ActionRow label={PACK.create} action="Go" />'), declared, load)).toEqual(
		[]
	);
});

it('refuses a planted name the index does not declare', () => {
	const found = unfoundIn(
		pane(
			'<SettingGroup heading={HEADING}>\n<FactRow label="A fact nobody declared" />\n</SettingGroup>'
		),
		declared,
		load
	);
	expect(found.map((one) => one.words)).toEqual(['A heading of its own', 'A fact nobody declared']);
	expect(unfoundIn(pane('<SectionHeading>Planted</SectionHeading>'), declared, load)).toHaveLength(
		1
	);
});

it('lets an excused name pass, and skips names decided while the screen runs', () => {
	const markup = pane(
		[
			'<FactRow label="Planted" />',
			'<FactRow label={tunnel.name} />',
			'<LabelledRow label="Rename {user.name}" />',
			'<ActionRow label={busy ? COPY.pack.create : "Other"} action="Go" />',
			'<Button label="A press, not a name" />',
			'<!-- <FactRow label="In a comment" /> -->'
		].join('\n')
	);
	expect(unfoundIn(markup, declared, load, new Set([said('Planted')]))).toEqual([]);
	expect(namesIn(markup).map((one) => one.literal ?? one.expression)).toEqual([
		'Planted',
		'tunnel.name',
		'Rename {user.name}',
		'busy ? COPY.pack.create : "Other"'
	]);
});

it('evaluates a module that imports a sibling and a helper', () => {
	expect((load('Made.search').COPY as { borrowed: string }).borrowed).toBe('Borrowed');
});

/* A search typed part of the way, against the real index: the Faces row it means. */
it('finds "import a structured fol" in the real settings index', () => {
	const real = (name: string) => readFileSync(join(PANES, `${name}.ts`), 'utf8');
	expect(
		declaredNames(['Faces.search'], (module: string) => loadSearchModule(module, real))
	).toContain(said('Import a structured folder to create facial fingerprints'));
	const found = grouped(indexOf([]), everySection(), 'import a structured fol');
	expect(found.map((group) => group.section.id)).toContain('faces');
	expect(found.flatMap((group) => group.entries.map((one) => one.key))).toContain(
		'faces.folder-import'
	);
});
