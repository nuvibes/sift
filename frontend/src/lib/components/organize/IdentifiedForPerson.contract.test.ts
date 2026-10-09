/*
 * One person's three tabs, every count from the one answer the page comes in. Read from the
 * source: a rendering test would pass with lazy counts restored. The counts are pinned on the server.
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
		expect(source).not.toContain('[show]: answer.total');
	});

	it('asks the server once for a page, not once per tab', () => {
		// One call site: a count per tab would be three requests for one screen.
		const asks = source.split('identifiedForPerson(').length - 1;
		expect(asks, 'more than one read of this person').toBe(1);
	});
});

describe('an address that names no tab lands on one with something on it', () => {
	/* An address naming no tab opens the first one with something on it; the guesses win when there are any. */
	it('falls back to the first tab the counts say is not empty', () => {
		expect(source).toContain('asked ??');
		expect(source).toContain('(counts as Record<Shown, number>)[one.key] > 0');
	});

	it('still answers the guesses before the counts have arrived', () => {
		// Null until the first answer lands.
		expect(source).toContain("counts === null\n\t\t\t\t? 'suggested'");
	});
});

describe('the marks on a face card', () => {
	/* Three marks at the foot of the card, which jsdom cannot place. */
	it('marks a reference with a face, and says whose face it helps Sift find', () => {
		expect(source).toContain('<FaceMark kind={learned(face)}');
		expect(source).toContain("Sift uses this one to identify ${first || 'them'}");
		/* Looks for the label, so a comment about the wording cannot trip it. */
		expect(source).not.toContain('label="Sift matches against this one"');
		expect(source).not.toContain(", 'Sift matches against this one'");
	});

	it('marks what Sift suggested and what is still asking, and nothing else', () => {
		expect(source).toContain('<FaceMark kind="recognized"');
		expect(source).toContain('<FaceMark kind="asking"');
		expect(source).not.toContain('<Icon name="help"');
		// A confirmed face wears no mark.
		expect(source).toContain("{#if face.attribution === 'matched' && !face.is_reference}");
		expect(source).toContain("{:else if face.attribution === 'suggested'}");
	});

	it("says a reference Sift took from its own name is Sift's, in the mark History gives faces", () => {
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
		const foot = source.slice(source.indexOf('<span class="foot">'));
		expect(foot.indexOf('class="marks"')).toBeLessThan(foot.indexOf('class="when"'));
		expect(source).not.toContain('.learned {\n\t\tposition: absolute');
	});
});

describe('the way back is the tab it was opened from', () => {
	it('reads the origin off the address and hands it to the trail', () => {
		expect(source).toContain("address.url.searchParams.get('via')");
		expect(source).toContain('tabOpenedFrom(');
	});
});

describe('the tab carries its own two answers', () => {
	/* Confirming what Sift named, per face and for the tab, through the person's doors. */
	it('offers both acts on the tab, through the doors that ask the PERSON', () => {
		// The functions in `$lib/people/faces.svelte` carry the addresses.
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
		/* The whole tab names no faces: that set is the server's to know. */
		expect(source).toContain("scope === 'all' ? { scope } : { scope, track_ids: [...ids] }");
	});

	it("wears the group screen's control, on the tab line", () => {
		/* Shaped like "Add as person" on a group (`PileDetail`). */
		expect(source).toContain('`Yes (${leadCount.toLocaleString()})`');
		expect(source).toContain("{ yes: true, id: 'yes', label: 'Yes'");
		expect(source).toContain("{ yes: false, id: 'no', label: 'No'");
		expect(source).toContain('on this page`');
		expect(source).toContain("'Nothing is selected'");
		expect(source).toContain('in this tab`');
		expect(source).toContain('? tabAnswers');
		expect(source).not.toContain('<div class="bar">');
	});

	it('draws nothing on the tab where everything is settled', () => {
		// A row that can do nothing is worse than none.
		expect(source).toContain(
			"controls={show !== 'confirmed' && !missing && !failed && onThisTab > 0"
		);
	});

	it('lets a single face be confirmed whatever put the name on it', () => {
		/* Any named face can be confirmed, matched as well as suggested. */
		expect(source).toContain("face.attribution !== 'confirmed'");
		expect(source).not.toContain("&& face.attribution === 'suggested');");
	});
});

describe('the No dialog agrees with how many faces it is about', () => {
	/* One selected face is not told "every one of them". */
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
