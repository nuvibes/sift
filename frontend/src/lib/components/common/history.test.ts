/*
 * What a history sentence NAMES, and which mark a row wears.
 *
 * Both are pure functions over what the server sent, and both are the kind of thing that is wrong
 * in a way nobody notices: a link drawn over the wrong run of characters still reads as a sentence,
 * and a mark that fell back to its kind still draws a mark. So the two ways the cutting goes wrong
 * (a name that is a prefix of another, and a name that appears twice) are asserted by name.
 */

import { describe, expect, it } from 'vitest';

import {
	hrefOf,
	hrefOfPiece,
	markOf,
	markOfLinkKind,
	markWords,
	sentenceParts,
	sinceOf,
	spanText,
	whenText,
	type HistoryEvent,
	type HistoryLink
} from './history';

function link(over: Partial<HistoryLink> = {}): HistoryLink {
	return { kind: 'person', id: 'p1', name: 'Ada Lumen', href: null, gone: false, ...over };
}

describe('cutting a sentence into what it names', () => {
	it('is the whole sentence when nothing is named', () => {
		expect(sentenceParts('Added to the library', [])).toEqual([
			{ text: 'Added to the library', link: null }
		]);
	});

	it('leaves every character where it was, so a client with no links reads the same words', () => {
		const parts = sentenceParts('Ada Lumen was named in this file', [link()]);

		expect(parts.map((part) => part.text).join('')).toBe('Ada Lumen was named in this file');
	});

	it('wraps the name and leaves the rest as words', () => {
		const named = link();
		const parts = sentenceParts('Ada Lumen was named in this file', [named]);

		expect(parts).toEqual([
			{ text: 'Ada Lumen', link: named },
			{ text: ' was named in this file', link: null }
		]);
	});

	it('takes the LONGEST name at a position, so a prefix does not win', () => {
		// A library with both `Ada` and `Ada Lumen` in it. Replacing the shorter first would link
		// `Ada` and leave the stray word `Lumen` behind it: a sentence naming a different person.
		const shorter = link({ id: 'p2', name: 'Ada' });
		const longer = link({ id: 'p1', name: 'Ada Lumen' });

		const parts = sentenceParts('Ada Lumen was named in this file', [shorter, longer]);

		expect(parts[0]).toEqual({ text: 'Ada Lumen', link: longer });
	});

	it('links EVERY occurrence of a name that appears twice', () => {
		// A sentence that named the same thing twice and linked one of them would read as two
		// different things sharing a word, which is the opposite of what it says.
		const tag = link({ kind: 'tag', id: 't1', name: 'beach' });

		const parts = sentenceParts('beach was merged into beach', [tag]);

		expect(parts.filter((part) => part.link !== null)).toHaveLength(2);
		expect(parts.map((part) => part.text).join('')).toBe('beach was merged into beach');
	});

	it('links a name only where it stands WHOLE, never inside a word of the line', () => {
		// A collection called `d`: "Added to d" must not be drawn as three links inside "Added" and
		// a fourth on its own. A letter or a digit touching the run means it is a piece of another
		// word.
		const one = link({ kind: 'collection', id: 'c1', name: 'd' });

		const parts = sentenceParts('Added to d', [one]);

		expect(parts).toEqual([
			{ text: 'Added to ', link: null },
			{ text: 'd', link: one }
		]);
	});

	it('still links a name with a space in it, and one beside punctuation', () => {
		const spaced = link({ kind: 'collection', id: 'c2', name: 'no face' });
		const tag = link({ kind: 'tag', id: 't3', name: 'Ada' });

		expect(sentenceParts('Added to no face', [spaced])[1]).toEqual({
			text: 'no face',
			link: spaced
		});
		expect(sentenceParts('Ada, Adam and Ada', [tag]).filter((p) => p.link)).toHaveLength(2);
	});

	it('treats a name as characters and not as a pattern', () => {
		// A name is arbitrary text somebody typed. As a regular expression `A.B (x)` matches almost
		// anything; as characters it matches itself.
		const odd = link({ kind: 'tag', id: 't2', name: 'A.B (x)' });

		const parts = sentenceParts('Tagged A.B (x)', [odd]);

		expect(parts[1]).toEqual({ text: 'A.B (x)', link: odd });
	});

	it('ignores a link with an empty name rather than looping on it', () => {
		// It would match at every position and consume nothing. Nothing sends one; the guard is what
		// makes that a property of this function rather than of the server.
		expect(sentenceParts('Tagged beach', [link({ name: '' })])).toEqual([
			{ text: 'Tagged beach', link: null }
		]);
	});

	it('leaves a name the sentence does not contain out of the parts entirely', () => {
		expect(sentenceParts('Added to the library', [link()])).toEqual([
			{ text: 'Added to the library', link: null }
		]);
	});
});

describe('where a named thing lives', () => {
	it('is the entity page for each kind that has one', () => {
		expect(hrefOf(link({ kind: 'person', id: 'p1' }))).toBe('/people/p1');
		expect(hrefOf(link({ kind: 'site', id: 'pf1' }))).toBe('/sites/pf1');
		expect(hrefOf(link({ kind: 'tag', id: 't1' }))).toBe('/tags/t1');
		expect(hrefOf(link({ kind: 'collection', id: 'c1' }))).toBe('/collections/c1');
		// The one address a hand-written path gets wrong: the kind says `photo_set` and the page
		// says `photo-sets`.
		expect(hrefOf(link({ kind: 'photo_set', id: 's1' }))).toBe('/photo-sets/s1');
	});

	it('never sends a username to a page of its own, which no longer exists', () => {
		// A username is stored and is not a place. The server names where its link goes (the person
		// behind it, or its files) and that address wins; without one, the fallback is the files
		// posted under the username, which exist for every username.
		expect(hrefOf(link({ kind: 'username', id: 'ac 1' }))).toBe('/browse?username=ac%201');
		expect(
			hrefOf(
				link({
					kind: 'username',
					id: 'ac1',
					name: 'esmewrenfield',
					href: '/people/p1',
					gone: false
				})
			)
		).toBe('/people/p1');
		expect(hrefOf(link({ kind: 'username', id: 'ac1' }))).not.toContain('/accounts/');
	});

	it('is the library narrowed to it for a folder, because a folder has no page', () => {
		expect(hrefOf(link({ kind: 'folder', id: 'holiday/2024' }))).toBe('/browse?in=holiday%2F2024');
	});

	it("is the server's own address where the line hands one over", () => {
		// A NUMBER is not a thing with a page, it is a SET, and the only place that knows which set
		// a line counted is the read that counted it. So the server sends the address and this
		// prefers it; building one here out of an id would be a second vocabulary for the query
		// language, drifting quietly the day a facet is renamed.
		expect(
			hrefOf(
				link({
					kind: 'files',
					id: 'p1',
					name: '1,200 files',
					href: '/browse?people=Ada%20Lumen',
					gone: false
				})
			)
		).toBe('/browse?people=Ada%20Lumen');
	});

	it('takes the address over the page even for a kind that has one', () => {
		// The filtered wall a line counted is not the same list as the thing's own page, and the
		// server is the half that knows which was meant.
		expect(hrefOf(link({ kind: 'person', id: 'p1', href: '/browse?people=Ada%20Lumen' }))).toBe(
			'/browse?people=Ada%20Lumen'
		);
	});

	it('is nothing at all for one of those that arrives without one', () => {
		// There is no page of "some files" to fall back to, so the row draws the number as the plain
		// words it already was rather than as a link somewhere that does not exist.
		expect(hrefOf(link({ kind: 'files', id: 'p1', name: '1,200 files' }))).toBeNull();
	});

	it('is nothing at all for a kind this build has no page for', () => {
		// An older client meeting a newer server is the ordinary case for a self-hosted app. The row
		// draws the plain words it already had rather than a link somewhere it cannot name.
		expect(hrefOf(link({ kind: 'something_later' }))).toBeNull();
	});
});

describe('the mark', () => {
	it('says WHICH of the three ways it arrived, when the row can say', () => {
		// Read from the `enriched:` filter's own table, so a mark on a row and the filter that finds
		// that row cannot come to mean different things.
		expect(markOf('named', 'folder')).toBe('folder_supervised');
		expect(markOf('tagged', 'stash')).toBe('inventory_2');
		expect(markOf('face_run', 'faces')).toBe('familiar_face_and_zone');
	});

	it('falls back to the kind when the row cannot say', () => {
		expect(markOf('named', null)).toBe('person');
		expect(markOf('tagged')).toBe('shoppingmode');
		expect(markOf('filed', undefined)).toBe('public');
	});

	it('falls back to the kind for a `via` this build does not know', () => {
		expect(markOf('named', 'something_later')).toBe('person');
	});

	it("wears the act's own glyph and words on a tag Sift made for a copy", () => {
		// The glyph the act's verb wears on a file's menu, the one its Created by line wears too.
		expect(markOf('added', 'produced', 'compress')).toBe('compress');
		expect(markOf('added', 'produced', 'edit')).toBe('design_services');
		expect(markWords({ via: 'produced', how: 'compress', means: 'x', actor: 'sift' })).toBe(
			'Sift: from a file it compressed'
		);
		expect(markWords({ via: 'produced', how: 'edit', means: 'x', actor: 'sift' })).toBe(
			'Sift: from a file it edited'
		);
		// No act kept: the pass's general mark and words.
		expect(markOf('added', 'produced', null)).toBe('auto_fix_high');
		expect(markWords({ via: 'produced', means: 'x', actor: 'sift' })).toBe(
			'Sift: from a file it made'
		);
	});

	it('says WHICH VERB made a copy, the same way at both ends of the row', () => {
		// The editor's own marks: the scissors on a trimmed copy are the scissors on the button that
		// trimmed it, and the direction the row is read from changes the sentence and not the mark.
		expect(markOf('copied_into', null, 'trim')).toBe('content_cut');
		expect(markOf('copied_from', null, 'trim')).toBe('content_cut');
		expect(markOf('copied_into', null, 'compress')).toBe('compress');
		expect(markOf('copied_into', null, 'gif')).toBe('gif');
	});

	it('falls back to the copy glyph for a verb this build has no mark for', () => {
		// `edit` is several verbs together and `resize` has no glyph that is not a crop. Both are
		// still copies, which is what the fallback says.
		expect(markOf('copied_into', null, 'edit')).toBe('file_copy');
		expect(markOf('copied_from', null, 'resize')).toBe('file_copy');
		expect(markOf('copied_into', null, null)).toBe('file_copy');
	});

	it('says "something happened" for a kind this build does not know', () => {
		expect(markOf('something_later')).toBe('info');
	});
});

describe('where a group of unnamed faces goes', () => {
	it('is the pile screen in the organizer, which is where a name is put to one', () => {
		// A group of faces has no entity page: it has a place in the organizer, and that place is
		// what somebody wants when a history says a face was found here and cannot say whose.
		expect(hrefOf(link({ kind: 'face_pile', id: 'g1', name: 'a face' }))).toBe(
			'/organize/faces-to-name/g1'
		);
	});
});

describe('a field a stash-box filled in', () => {
	it('is a chip with nothing to press, under the mark an edit wears', () => {
		// "FansDB filled in 10 details" opens to the ten; a field of the record has no page.
		expect(hrefOf(link({ kind: 'field', id: 'birthdate', name: 'birthdate' }))).toBeNull();
		expect(markOfLinkKind('field')).toBe('edit');
	});
});

describe('what a mark means, in words', () => {
	/* The KIND's words are the server's (`means`), sent beside the line; what the client still
	   answers is a task's mark, whose words come from the same facet table as its glyph. */
	it("reads a task's words out of the same table its glyph came from", () => {
		expect(markWords({ via: 'folder', means: 'A person named in a file', actor: 'sift' })).toBe(
			'Sift: from a folder name'
		);
		expect(markWords({ via: 'stash', means: 'x', actor: 'stash_box' })).toBe('A stash-box');
	});

	it("says the server's words for a kind, and for a task whose glyph it does not know", () => {
		expect(markWords({ via: null, means: 'Paused', actor: 'you' })).toBe('Paused');
		expect(markWords({ via: 'something_later', means: 'Renamed', actor: 'sift' })).toBe('Renamed');
	});

	it('says something rather than nothing for a reply that sent no words', () => {
		expect(markWords({ via: null, means: '', actor: 'sift' })).toBe('Something happened');
		expect(markWords({ via: null, means: '', actor: 'you' })).toBe('You did this');
	});
});

describe('where one piece of a line goes', () => {
	const piece = (over: Partial<Parameters<typeof hrefOfPiece>[0]> = {}) => ({
		text: 'Ada Lumen',
		kind: 'person' as string | null,
		id: 'p1' as string | null,
		href: null as string | null,
		gone: false,
		rest: [],
		lead: '',
		...over
	});

	it("goes to the kind's own page, or to the server's address where it sent one", () => {
		expect(hrefOfPiece(piece())).toBe(
			hrefOf({ kind: 'person', id: 'p1', name: 'x', href: null, gone: false })
		);
		expect(hrefOfPiece(piece({ kind: 'files', href: '/browse?people=x' }))).toBe(
			'/browse?people=x'
		);
	});

	it('goes nowhere for plain words or a thing that has gone', () => {
		expect(hrefOfPiece(piece({ kind: null, id: null }))).toBeNull();
		expect(hrefOfPiece(piece({ gone: true }))).toBeNull();
	});

	it('goes to the address of a place the server named without a kind', () => {
		expect(hrefOfPiece(piece({ kind: null, id: null, href: '/organize/faces' }))).toBe(
			'/organize/faces'
		);
	});

	it('picks a download out on the Downloads queue', () => {
		expect(hrefOfPiece(piece({ kind: 'download', id: 'd1' }))).toBe('/downloads?row=d1');
	});
});

describe('when a line that stands for a run happened', () => {
	/* Noon, local, so both ends of a short run sit on one day whatever zone the suite runs in. */
	const noon = new Date(2026, 8, 12, 12, 0, 0).getTime() / 1000;

	it('says the span with the date once, from the first of the run to the last', () => {
		const said = spanText(noon + 3600, noon);

		expect(said.startsWith(`${whenText(noon)} \u2014 `)).toBe(true);
		expect(said).not.toBe(whenText(noon + 3600));
		expect(said.split(String(new Date(noon * 1000).getFullYear())).length).toBe(2);
	});

	it('is the one moment for a line that is one act', () => {
		expect(spanText(noon, null)).toBe(whenText(noon));
		expect(spanText(noon, noon)).toBe(whenText(noon));
		expect(spanText(null, noon)).toBe(whenText(null));
	});

	it("reads the run's start off the event, and nothing where the event has none", () => {
		const event = { at: noon, what: 'x' } as unknown as HistoryEvent;

		expect(sinceOf(event)).toBeNull();
		expect(sinceOf({ ...event, since: noon - 60 } as HistoryEvent)).toBe(noon - 60);
	});
});
