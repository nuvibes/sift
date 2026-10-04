/*
 * A tag Sift put on a file it produced wears the glyph of the verb that made the file.
 *
 * The Created by line of the tag a compressed copy wears and of the tag an edited copy wears must
 * not be one wand for two acts. Each act draws the glyph its own verb wears on a file's menu, read here
 * from the menu itself, so a verb that changes its glyph moves the Created by line with it or fails
 * this rather than leaving the two to disagree.
 */
import { expect, it } from 'vitest';
import type { VerbPick } from '$lib/components/common/verbs';
import { fileVerbs, type FileVerbHandlers, type Verb } from '$lib/grid/verbs';
import { MADE_ACTS, madeIcon, madeLabel } from './facet-labels';

const NOTHING = () => {};

const PICK: VerbPick = {
	kind: 'tag',
	plural: 'tags',
	ask: async () => ({ choices: [], more: 0 }),
	pick: async () => 'landed'
};

const HANDLERS: FileVerbHandlers = {
	tag: PICK,
	rename: NOTHING,
	collect: PICK,
	assign: PICK,
	site: PICK,
	photoSet: PICK,
	song: PICK,
	favorite: NOTHING,
	pin: NOTHING,
	rate: NOTHING,
	move: NOTHING,
	share: NOTHING,
	visibility: NOTHING,
	hide: NOTHING,
	save: NOTHING,
	link: NOTHING,
	autoEnrich: NOTHING,
	enrich: NOTHING,
	compress: NOTHING,
	edit: NOTHING,
	gif: NOTHING,
	remove: NOTHING
};

/** The menu of one file of this kind, as an admin sees it. */
function menuOf(mediaType: string): Verb[] {
	return fileVerbs(
		{
			isAdmin: true,
			canSave: true,
			showingHidden: false,
			count: 1,
			canMove: true,
			canCompress: true,
			handlers: HANDLERS,
			subject: { media_type: mediaType, favorite: false, concealed: false }
		},
		{ label: () => 'Save', icon: () => 'download' as const }
	);
}

it("draws each act in its own verb's glyph: Compress, and the editor's Modify", () => {
	const compress = menuOf('video').find((verb) => verb.id === 'compress');
	const modify = menuOf('image').find((verb) => verb.id === 'edit');
	expect(modify?.label).toBe('Modify');
	expect(MADE_ACTS.compress.icon).toBe(compress?.icon);
	expect(MADE_ACTS.edit.icon).toBe(modify?.icon);
	// And the two are two marks: one glyph for both acts is the fault this table exists to end.
	expect(MADE_ACTS.compress.icon).not.toBe(MADE_ACTS.edit.icon);
});

it('says each act in the Enriched by grammar, and keeps the general row with no act', () => {
	expect(madeLabel('produced', 'compress')).toBe('Sift: from a file it compressed');
	expect(madeLabel('produced', 'edit')).toBe('Sift: from a file it edited');
	expect(madeLabel('produced', null)).toBe('Sift: from a file it made');
	expect(madeIcon('produced', null)).toBe('auto_fix_high');
	// A word this build has no act for is the general row, never a hole.
	expect(madeIcon('produced', 'later')).toBe('auto_fix_high');
	expect(madeLabel('produced', 'later')).toBe('Sift: from a file it made');
});

/* Every word a row's `created_by_via` may hold, read from the server's closed list, has a Created by
   sentence of its own: a word missing here is drawn bare ("Created by shoot"). */
it('says every pass that makes a row in words, never by its stored word', async () => {
	const { readFileSync } = await import('node:fs');
	const vocabulary = readFileSync('../src/sift/kernel/vocabulary.py', 'utf8');
	const words = Object.fromEntries(
		[...vocabulary.matchAll(/^(VIA_[A-Z_]+) = "([a-z_]+)"$/gm)].map((m) => [m[1], m[2]])
	);
	const listed = /^MADE_VIAS = \(([^)]*)\)/m.exec(vocabulary)?.[1] ?? '';
	const vias = [...listed.matchAll(/VIA_[A-Z_]+/g)].map((m) => words[m[0]]);
	expect(vias.length, 'the server list was not read').toBeGreaterThan(10);
	for (const via of vias) {
		expect(via, 'a name in MADE_VIAS with no word').toBeDefined();
		expect(madeLabel(via), via).not.toBe(via);
	}
	expect(madeLabel('shoot')).toBe('Sift: from a shoot');
});
