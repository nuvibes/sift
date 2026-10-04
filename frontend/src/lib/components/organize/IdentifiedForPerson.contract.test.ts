/*
 * One person's three tabs, and the one answer they are drawn from.
 *
 * Every tab's count is on the row from the first answer: two words with no number read as two empty
 * tabs, which is a wrong answer rather than a missing one. The server counts all three on the read
 * it is already doing (`AppearancePage.waiting/matched/confirmed`; one person's faces are assembled
 * in full whatever the narrowing is).
 *
 * Read from the source: what is guarded is that the three numbers come from one answer rather than
 * three requests, a property of how the screen asks that a rendering test would pass with lazy
 * counts restored as long as the tab under test had been visited. The counts themselves are pinned
 * on the server.
 */
import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

const source = readFileSync('src/lib/components/organize/IdentifiedForPerson.svelte', 'utf8');

describe('the tab counts arrive with the page', () => {
	it('takes all three off the one answer', () => {
		expect(source).toContain('suggested: answer.waiting');
		expect(source).toContain('confirmed: answer.confirmed');
		expect(source).toContain('matched: answer.matched');
	});

	it('never writes only the tab being shown', () => {
		// The shape this guards against. It is one line, and it reads as a cache.
		expect(source).not.toContain('[show]: answer.total');
	});

	it('asks the server once for a page, not once per tab', () => {
		// One call site. A count per tab would need one read per tab, which is the other way this
		// could be "fixed" and is three requests for one screen.
		const asks = source.split('identifiedForPerson(').length - 1;
		expect(asks, 'more than one read of this person').toBe(1);
	});
});

describe('an address that names no tab lands on one with something on it', () => {
	/*
	 * The fallback tab for an address that names none (a typed address, an old link, a door that
	 * forgets): a card leading with the nod has nothing waiting, so the wall's own doors name their
	 * tab.
	 *
	 * The guesses still win whenever there are any: they are the only one of the three that is
	 * work, and nothing in any of them leaves it on the guesses, whose empty state says what this
	 * screen is for.
	 */
	it('falls back to the first tab the counts say is not empty', () => {
		expect(source).toContain('asked ??');
		expect(source).toContain('(counts as Record<Shown, number>)[one.key] > 0');
	});

	it('still answers the guesses before the counts have arrived', () => {
		// Null until the first answer lands. A tab chosen from counts that are not there yet would
		// be a guess wearing the authority of a measurement.
		expect(source).toContain("counts === null\n\t\t\t\t? 'suggested'");
	});
});

describe('the marks on a face card', () => {
	/*
	 * Three marks, one word each, at the foot of the card rather than over the crop, which is the
	 * part being read and judged.
	 *
	 * Read from the source for the same reason as the counts: what is pinned is which glyph means
	 * what and where it sits, and jsdom can say nothing about the second half.
	 */
	it('marks a reference with a face, and says whose face it helps Sift find', () => {
		expect(source).toContain('<FaceMark kind={learned(face)}');
		expect(source).toContain("Sift uses this one to identify ${first || 'them'}");
		/*
		 * The machinery sentence is not said on screen. This looks for the label rather than for
		 * the words, so a comment explaining the wording cannot trip it.
		 */
		expect(source).not.toContain('label="Sift matches against this one"');
		expect(source).not.toContain(", 'Sift matches against this one'");
	});

	it('marks what Sift suggested and what is still asking, and nothing else', () => {
		// The shared marks (`FaceMark`), never a disc drawn here by hand.
		expect(source).toContain('<FaceMark kind="recognized"');
		expect(source).toContain('<FaceMark kind="asking"');
		expect(source).not.toContain('<Icon name="help"');
		// A face you confirmed wears no mark: the mark for "nothing to do here" is the absence of
		// one, and three marks on every tile is a wall that cannot be read at a glance.
		expect(source).toContain("{#if face.attribution === 'matched' && !face.is_reference}");
		expect(source).toContain("{:else if face.attribution === 'suggested'}");
	});

	it("says a reference Sift took from its own name is Sift's, in the mark History gives faces", () => {
		// One mark for both halves on a face Sift named and learns from, never the confirmed
		// reference's sentence, which reads as a face somebody chose.
		expect(source).toContain('<FaceMark kind={learned(face)} label={learnsFrom(face)}');
		expect(source).toContain("(face.attribution === 'matched' ? 'learned' : 'reference')");
		expect(source).toContain('Sift recognized ${whom} and uses it to identify them');
		expect(source).toContain('`${first} in this one`');
	});

	it('names the settled state Confirmed, with who in the words under the tab', () => {
		expect(source).not.toContain('Confirmed by you');
		expect(source).toContain(
			"{ key: 'confirmed', title: 'Confirmed', blurb: 'You confirmed these names.' }"
		);
		expect(source).toContain("if (face.attribution === 'confirmed') return 'Confirmed';");
	});

	it('puts them in the card, to the left of the timestamp', () => {
		// The foot is one row: the marks at its leading edge, the timestamp in a middle column that
		// keeps it centred whether this face wears marks or not.
		const foot = source.slice(source.indexOf('<span class="foot">'));
		expect(foot.indexOf('class="marks"')).toBeLessThan(foot.indexOf('class="when"'));
		expect(source).not.toContain('.learned {\n\t\tposition: absolute');
	});
});

describe('the way back is the tab it was opened from', () => {
	/*
	 * The trail names the tab somebody opened the card from, so the way back lands on a tab they
	 * were on.
	 */
	it('reads the origin off the address and hands it to the trail', () => {
		expect(source).toContain("address.url.searchParams.get('via')");
		expect(source).toContain('tabOpenedFrom(');
	});
});

describe('the tab carries its own two answers', () => {
	/*
	 * The Recognized by Sift tab offers a way to confirm what Sift named on its own, per face and
	 * for the tab, like the Faces to name pages.
	 *
	 * Read from the source, as everything here is: what is pinned is which door each press goes
	 * through and where the row sits; the behaviour behind each door is pinned on the server beside
	 * the write it makes.
	 */
	it('offers both acts on the tab, through the doors that ask the PERSON', () => {
		// The same four doors the wall card presses, each asked of the person with the scope in the
		// body: the functions in `$lib/people/faces.svelte` carry the addresses, so this screen names the
		// functions rather than spelling the routes a second time.
		for (const door of [
			'confirmMatches(personId, body)',
			'rejectMatches(personId, body)',
			'confirmLookAlikes(personId, body)',
			'rejectLookAlikes(personId, body)'
		]) {
			expect(source).toContain(door);
		}
	});

	it('sends the scope for the server to honour, with the faces only when it is not the tab', () => {
		/*
		 * Honour the scopes: a page or a pick names its faces and the server filters them; the
		 * whole tab names none, because that set is the server's to know. Sending a page as "all",
		 * or filtering a whole-tab answer here, would fake the scope.
		 */
		expect(source).toContain("scope === 'all' ? { scope } : { scope, track_ids: [...ids] }");
	});

	it("wears the group screen's control, on the tab line", () => {
		/* Shaped like "Add as person" on a group (see `PileDetail`): the main half answers what is
		   picked or the page, and each answer opens out into the same three scopes, in the same
		   words where the meaning is the same. */
		expect(source).toContain('`Yes (${leadCount.toLocaleString()})`');
		expect(source).toContain("{ yes: true, id: 'yes', label: 'Yes'");
		expect(source).toContain("{ yes: false, id: 'no', label: 'No'");
		expect(source).toContain('on this page`');
		expect(source).toContain("'Nothing is selected'");
		expect(source).toContain('in this tab`');
		// On the tab line, the header's own slot, not a band above the faces.
		expect(source).toContain('? tabAnswers');
		expect(source).not.toContain('<div class="bar">');
	});

	it('draws nothing on the tab where everything is settled', () => {
		// A row that can do nothing is worse than no row: the only way to find that out is to press
		// it, and a confirmed face has already been answered for.
		expect(source).toContain(
			"controls={show !== 'confirmed' && !missing && !failed && onThisTab > 0"
		);
	});

	it('lets a single face be confirmed whatever put the name on it', () => {
		/*
		 * Any named face can be confirmed, matched as well as suggested: a match is Sift putting a
		 * name on a file without being asked, the decision most worth somebody's own yes. The
		 * server confirms a face as whoever is on its row.
		 */
		expect(source).toContain("face.attribution !== 'confirmed'");
		expect(source).not.toContain("&& face.attribution === 'suggested');");
	});
});

describe('the No dialog agrees with how many faces it is about', () => {
	/*
	 * The dialog for a No counts in its sentence as well as its title, both chosen by the same
	 * count, so one selected face is not told "every one of them". Read from the source: what is
	 * pinned is that choice, not how jsdom lays out a dialog.
	 */
	it('says the one face, not every one of them, when there is one', () => {
		expect(source).toContain(
			'pendingNo?.count === 1\n\t\t? "The face loses the name, and Sift won\'t suggest it for this face again. "'
		);
	});

	it('keeps the plural sentence for several', () => {
		expect(source).toContain(
			': "Every one of them loses the name, and Sift won\'t suggest it for them again. ")'
		);
	});
});
