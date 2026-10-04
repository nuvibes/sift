/*
 * The Faces page is five tabs, each reading its own tier of the one list, so it is clear which part
 * of the list somebody has reached and what was put aside is a tab rather than a dropdown.
 *
 * Read from the source: what matters is how each panel asks (filtered on the server, to the tab's
 * tier), and a rendering test cannot tell a filter made in the read from one made over a page
 * already fetched. The difference shows only when one kind outnumbers a page, where filtering
 * afterwards asks for the wrong rows and the pager counts a population the screen is not drawing.
 * The filtering itself is pinned on the server, beside the read that does it.
 */
import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

const read = (name: string) => readFileSync(`src/lib/components/organize/${name}.svelte`, 'utf8');

describe('each tab asks the one door for its own tier', () => {
	it('asks for the people Sift is proposing, and the cards of groups beside them', () => {
		// Two tiers of the one list: a person's standing questions and a card of groups that may be
		// them, asked for together so the server orders them.
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
	/*
	 * The question above already carries the number, so the affirmative is a plain yes or no rather
	 * than "Answer N".
	 */
	const source = read('SuggestionsPanel');

	it('leads with the word that answers the question', () => {
		expect(source).toContain("'Yes'");
		expect(source).not.toContain('Answer ${');
	});

	it('carries the refusal beside it, and writes it in one press', () => {
		/*
		 * A blanket no is offered beside the yes: the yes writes against the same faces from the
		 * same press, and the card shows the whole question. The door that opens them one at a time
		 * stays on the menu for somebody who wants to look first.
		 */
		expect(source).toContain("label: 'No'");
		expect(source).toContain('refuse(person)');
		expect(source).toContain('No, one at a time');
		expect(source).toContain('goto(reviewHref(person))');
	});

	it('refuses them the way it agrees: asked of the person, never of a list', () => {
		// The card is ranked and pages, so a list sent from it could be a page rather than the
		// pile the button names. Both doors send nothing at all.
		expect(source).toContain('rejectLookAlikes(person.id)');
		expect(source).toContain('confirmLookAlikes(person.id)');
	});

	it('offers Undo on the receipt any of the four presses wrote', () => {
		// A bulk yes or no over a whole pile is a press somebody may want back within seconds; the
		// reply carries the receipt and `decided` turns it into the toast's Undo. Four presses: yes
		// and no on a person's questions, yes and no on a card of groups.
		expect(source.match(/decided\(/g)?.length).toBe(4);
		expect(source.match(/answer\.decision_id \?\? null/g)?.length).toBe(4);
	});
});

describe('what was put aside is a tab rather than a dropdown', () => {
	/* Putting a group aside is the same question answered no, but a control has to be opened
	   before it can be read: as a dropdown, the state somebody reaches for when they think they hid
	   something by mistake would be the hardest of the five to reach. */
	it('draws no Select anywhere on the faces tabs', () => {
		for (const name of ['SuggestionsPanel', 'DisagreementsPanel', 'FaceGroupsPanel']) {
			expect(read(name)).not.toContain('<Select');
		}
	});
});

describe('every card on the wall is the same size', () => {
	/*
	 * Every card is the same height: the room is reserved in the card's own cells (see
	 * `FaceCovers.most`) rather than by a height, because every cell is square and the columns
	 * divide the card, so a count of cells is a height at every window size.
	 */
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
	/*
	 * The origin travels in the address rather than being guessed from the queue the detail belongs
	 * to: one screen draws a group however it was reached, and a group pressed on Ignored must get
	 * back to Ignored.
	 */
	it("names the tab in the person's address", () => {
		expect(read('SuggestionsPanel')).toContain('&via=faces');
	});

	it('hands the groups wall the tab it is being drawn on', () => {
		expect(read('FaceGroupsPanel')).toContain('tab={queue}');
	});
});

describe('the line at the foot of the groups tab', () => {
	/*
	 * The line says which groups the number counts, and pressing it opens them; "groups" alone
	 * would say nothing on the one line of that tab whose job is to say so.
	 */
	it('says how many small groups there are, in exactly those words', () => {
		const source = read('FaceGroupsPanel');
		expect(source).toContain("'1 small group'");
		expect(source).toContain('} small groups`');
	});
});
