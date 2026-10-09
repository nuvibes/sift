/* The Faces page is five tabs, each reading its own tier on the server; read from the source,
 * since a render cannot tell a server filter from one over a fetched page. */
import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

const read = (name: string) => readFileSync(`src/lib/components/organize/${name}.svelte`, 'utf8');

describe('each tab asks the one door for its own tier', () => {
	it('asks for the people Sift is proposing, and the cards of groups beside them', () => {
		// Two tiers asked together, so the server orders them.
		const source = read('SuggestionsPanel');
		expect(source).toContain("const TIERS: readonly ToCheckKind[] = ['person', 'may_be'];");
		expect(source).toContain("toCheck(query, 'waiting', TIERS, asked)");
	});

	it('asks for the names a pass filed that the face disagrees with, gathered by person', () => {
		const source = read('DisagreementsPanel');
		expect(source).toContain('disagreeingPeople()');
		expect(source).toContain('disagreementsOf(personId');
	});

	it('asks for the groups, or for the ones put aside, off the address it is drawn at', () => {
		const source = read('FaceGroupsPanel');
		expect(source).toContain("queue === 'discarded-faces'");
		expect(source).toContain("aside ? undefined : 'group'");
	});
});

describe('a suggestion is answered yes or no', () => {
	// The question carries the number, so the answer is a plain yes or no.
	const source = read('SuggestionsPanel');

	it('leads with the word that answers the question', () => {
		expect(source).toContain("'Yes'");
		expect(source).not.toContain('Answer ${');
	});

	it('carries the refusal beside it, and writes it in one press', () => {
		// A blanket no beside the yes; the one-at-a-time door stays on the menu.
		expect(source).toContain("label: 'No'");
		expect(source).toContain('refuse(person)');
		expect(source).toContain('No, one at a time');
		expect(source).toContain('goto(reviewHref(person))');
	});

	it('refuses them the way it agrees: asked of the person, never of a list', () => {
		// The card pages, so both doors send nothing but the person.
		expect(source).toContain('rejectLookAlikes(person.id)');
		expect(source).toContain('confirmLookAlikes(person.id)');
	});

	it('offers Undo on the receipt any of the four presses wrote', () => {
		// Each bulk yes or no turns its receipt into the toast's Undo: four presses.
		expect(source.match(/decided\(/g)?.length).toBe(4);
		expect(source.match(/answer\.decision_id \?\? null/g)?.length).toBe(4);
	});
});

describe('what was put aside is a tab rather than a dropdown', () => {
	// Putting a group aside is a tab, never a dropdown that hides it.
	it('draws no Select anywhere on the faces tabs', () => {
		for (const name of ['SuggestionsPanel', 'DisagreementsPanel', 'FaceGroupsPanel']) {
			expect(read(name)).not.toContain('<Select');
		}
	});
});

describe('every card on the wall is the same size', () => {
	// Every card one height, reserved in crop cells (`FaceCovers.most`).
	it('reserves the same number of crop cells on all three faces walls', () => {
		for (const name of ['SuggestionsPanel', 'IdentifiedPanel']) {
			expect(read(name)).toContain('most={CROPS_ON_A_CARD}');
		}
		expect(readFileSync('src/lib/components/faces/FaceGroups.svelte', 'utf8')).toContain(
			'most={CROPS_ON_A_CARD}'
		);
	});
});

describe('a detail opened from a tab knows which tab it was', () => {
	// The origin travels in the address, so a group from Ignored returns there.
	it("names the tab in the person's address", () => {
		expect(read('SuggestionsPanel')).toContain('&via=faces');
	});

	it('hands the groups wall the tab it is being drawn on', () => {
		expect(read('FaceGroupsPanel')).toContain('tab={queue}');
	});
});

describe('the line at the foot of the groups tab', () => {
	// The line says which groups the number counts.
	it('says how many small groups there are, in exactly those words', () => {
		const source = read('FaceGroupsPanel');
		expect(source).toContain("'1 small group'");
		expect(source).toContain('} small groups`');
	});
});
