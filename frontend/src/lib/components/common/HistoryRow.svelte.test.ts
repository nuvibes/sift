/* One event, drawn.
 *
 * Three things can be wrong here and each of them looks fine: the mark says the wrong person did
 * it, the Undo is offered on something the server would refuse, and a row with no recorded time
 * draws a date nobody wrote down. All three are asserted.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { words } from '$lib/design/testing.svelte';
import codepoints from '$lib/generated/icon-codepoints.json';
import HistoryRow from './HistoryRow.svelte';
import { markWords, spanText, type HistoryEvent, type HistoryPiece } from './history';

let host: HTMLElement;

afterEach(() => {
	host?.remove();
});

/** The character an icon name draws as, so a test can name the glyph rather than the codepoint. */
function glyph(name: keyof typeof codepoints): string {
	return String.fromCodePoint(parseInt(codepoints[name], 16));
}

/** One piece of a line: plain words, or a thing where it sits. */
function words_(text: string): HistoryPiece {
	return { text, kind: null, id: null, href: null, gone: false, rest: [], lead: '' };
}

function thing(
	kind: string,
	id: string,
	text: string,
	over: Partial<HistoryPiece> = {}
): HistoryPiece {
	return { text, kind, id, href: null, gone: false, rest: [], lead: '', ...over };
}

function event(overrides: Partial<HistoryEvent> = {}): HistoryEvent {
	return {
		at: 1757000000,
		actor: 'sift',
		actor_name: 'Sift',
		kind: 'added',
		pieces: [words_('Sift added this file to the library')],
		what: 'Sift added this file to the library',
		means: 'Added to the library',
		undo: null,
		reversed: false,
		detail: [],
		via: null,
		how: null,
		receipt: null,
		since: null,
		away: null,
		more: '',
		...overrides
	};
}

function render(props: Parameters<typeof HistoryRow>[1]) {
	host = document.createElement('div');
	document.body.append(host);
	const component = mount(HistoryRow, { target: host, props });
	return {
		row: host.querySelector('.history-row') as HTMLElement,
		mark: host.querySelector('.mark') as HTMLElement,
		what: host.querySelector('.what') as HTMLElement,
		named: [...host.querySelectorAll('a.named')] as HTMLAnchorElement[],
		moment: host.querySelector('.moment') as HTMLElement,
		undo: host.querySelector('button'),
		component
	};
}

describe('the mark', () => {
	it('is ringed for what this account did and plain for everything else', () => {
		const { mark } = render({ event: event({ actor: 'you', kind: 'renamed' }) });
		expect(mark.classList.contains('mine')).toBe(true);

		unmount(render({ event: event({ actor: 'you' }) }).component);
		host.remove();

		for (const actor of ['sift', 'stash_box', 'another_user', 'somebody']) {
			const drawn = render({ event: event({ actor }) });
			expect(drawn.mark.classList.contains('mine')).toBe(false);
			unmount(drawn.component);
			host.remove();
		}
	});

	it('wears the glyph its family uses elsewhere in the app', () => {
		expect(render({ event: event({ kind: 'named' }) }).mark.textContent).toBe(glyph('person'));
		host.remove();
		expect(render({ event: event({ kind: 'enriched' }) }).mark.textContent).toBe(
			glyph('inventory_2')
		);
	});

	it('wears the workbench tray for a decision, which is where every one of them was taken', () => {
		// Not a tick or a gavel: what the event says is not that something was approved but that it
		// was settled at the workbench, and the reader has already learnt that glyph on the rail.
		expect(render({ event: event({ kind: 'decided' }) }).mark.textContent).toBe(glyph('inbox'));
	});

	it('falls back to a mark that says something happened, for a kind this build is too old to know', () => {
		// The honest case for a self-hosted app somebody upgrades when they feel like it: the
		// server grows a kind and the page in the browser is a version behind. A hole would read as
		// a broken screen; this reads as an event.
		const { mark } = render({ event: event({ kind: 'invented_later' }) });

		expect(mark.textContent).toBe(glyph('info'));
	});

	it('says WHICH of the three ways an attribution arrived, where the row can say', () => {
		// The kind says a tag was put on; it says nothing about whether a stash-box, a face or a
		// folder name did it, which is the question somebody scanning a history is actually asking.
		// The glyphs are the `enriched:` filter's own, so a mark and the filter cannot disagree.
		expect(render({ event: event({ kind: 'named', via: 'folder' }) }).mark.textContent).toBe(
			glyph('folder_supervised')
		);
		host.remove();
		expect(render({ event: event({ kind: 'filed', via: 'stash' }) }).mark.textContent).toBe(
			glyph('inventory_2')
		);
	});

	it('names the mark by what it means, never by the stored kind', () => {
		const one = event({ kind: 'tagged' });
		const label = render({ event: one }).mark.getAttribute('aria-label');
		expect(label).toBe(markWords(one));
		expect(label).not.toBe('tagged');
	});
});

describe('the time', () => {
	it('is a date when there is one', () => {
		const { moment } = render({ event: event({ at: 1757000000 }) });

		expect(moment.classList.contains('unrecorded')).toBe(false);
		expect(moment.textContent).not.toBe('');
	});

	it('says so in words when nothing recorded it, rather than drawing a date nobody wrote', () => {
		const { moment } = render({ event: event({ at: null }) });

		expect(moment.textContent).toBe('Before this was recorded');
		expect(moment.classList.contains('unrecorded')).toBe(true);
	});

	it("says a run's span (a day of downloads) from its first act to its last", () => {
		const noon = new Date(2026, 8, 12, 12, 0, 0).getTime() / 1000;
		const run = { ...event({ at: noon + 3600 }), since: noon } as HistoryEvent;

		const { moment } = render({ event: run });

		expect(moment.textContent).toBe(spanText(noon + 3600, noon));
		expect(moment.textContent).toContain('\u2014');
	});
});

describe('undo', () => {
	it('is offered only when the server said this can be taken back', () => {
		const onundo = vi.fn();
		const { undo } = render({ event: event({ undo: { kind: 'move', id: 'm1' } }), onundo });

		expect(undo).not.toBeNull();
		undo?.click();
		expect(onundo).toHaveBeenCalledTimes(1);
		expect(onundo.mock.calls[0][0].undo).toEqual({ kind: 'move', id: 'm1' });
	});

	it('is not offered on an event that has already been taken back', () => {
		// The server leaves `undo` set on nothing reversed, so this is belt and braces, and it is
		// the half a client can get wrong on its own, by drawing the button off the id alone.
		const { undo, what } = render({
			event: event({ undo: { kind: 'move', id: 'm1' }, reversed: true }),
			onundo: vi.fn()
		});

		expect(undo).toBeNull();
		expect(what.classList.contains('taken-back')).toBe(true);
	});

	it('is not offered when nothing is listening, however undoable the event is', () => {
		// A button that does nothing is worse than no button: the only way to find out is to press
		// it, and pressing it looks like it worked.
		const { undo } = render({ event: event({ undo: { kind: 'move', id: 'm1' } }) });

		expect(undo).toBeNull();
	});

	it('is not offered on an ordinary event', () => {
		expect(render({ event: event(), onundo: vi.fn() }).undo).toBeNull();
	});

	it('cannot be pressed twice while the first press is still in flight', () => {
		const { undo } = render({
			event: event({ undo: { kind: 'move', id: 'm1' } }),
			onundo: vi.fn(),
			undoing: true
		});

		expect(undo?.disabled).toBe(true);
	});
});

describe('what it says', () => {
	it("is the server's pieces, drawn in order and assembled nowhere here", () => {
		const { what } = render({
			event: event({ pieces: [words_('StashDB recognized this file')], what: 'x' })
		});

		expect(words(what)).toBe('StashDB recognized this file');
	});

	it('says who did it in the line and nowhere beside the time', () => {
		/* The line begins with its actor, so there is no grey name beside the time. */
		const { row } = render({ event: event({ actor_name: 'StashDB' }) });
		expect(row.querySelector('.by')).toBeNull();
		expect(words(row.querySelector('.when') as HTMLElement)).not.toContain('StashDB');
	});

	it('says an event was undone, in words as well as in colour', () => {
		const { row } = render({ event: event({ reversed: true }) });

		expect(row.querySelector('.reversed')?.textContent).toBe('Undone');
	});

	it('draws the plain words a reply with no pieces sent', () => {
		const { what, named } = render({ event: event({ pieces: [], what: 'An older line' }) });

		expect(words(what)).toBe('An older line');
		expect(named).toHaveLength(0);
	});
});

describe('the line under', () => {
	const more = 'A new person added, 1 other folder with that name answered.';

	it('says what else a decision wrote under its line', () => {
		render({ event: event({ more } as Partial<HistoryEvent>) });

		expect(host.querySelector('.more')?.textContent).toBe(more);
	});

	it('is not said once the decision has been taken back, nor on a line without one', () => {
		render({ event: event({ more, reversed: true } as Partial<HistoryEvent>) });
		expect(host.querySelector('.more')).toBeNull();
		host.remove();
		render({ event: event() });
		expect(host.querySelector('.more')).toBeNull();
	});
});

describe('the things in the line', () => {
	it('are drawn as the way to the things they name, where the server placed them', () => {
		const { named, what } = render({
			event: event({
				kind: 'named',
				pieces: [
					words_('Sift named '),
					thing('person', 'p1', 'Ada Lumen'),
					words_(' here from a folder name')
				]
			})
		});

		expect(named.map((one) => one.textContent)).toEqual(['Ada Lumen']);
		expect(named[0].getAttribute('href')).toBe('/people/p1');
		expect(words(what)).toBe('Sift named Ada Lumen here from a folder name');
	});

	it('never link a run the server did not place: "d" inside "added" stays a word', () => {
		/* A search for the name would link every "d" in "Added to d" to a Collection called d. */
		const { named } = render({
			event: event({
				pieces: [words_('You added this file to '), thing('collection', 'c1', 'd')]
			})
		});

		expect(named.map((one) => one.textContent)).toEqual(['d']);
	});

	it('are plain words for a kind this build has no page for', () => {
		const { named, what } = render({
			event: event({
				pieces: [thing('something_later', 'x1', 'Someone'), words_(' was named here')]
			})
		});

		expect(named).toHaveLength(0);
		expect(words(what)).toBe('Someone was named here');
	});

	it('are struck through and never a link when the thing has gone', () => {
		const { named, what, row } = render({
			event: event({
				kind: 'deleted',
				pieces: [
					words_('You deleted '),
					thing('asset', 'a1', 'beach-day.mp4', { gone: true }),
					words_(', a file of theirs')
				]
			})
		});

		expect(named).toHaveLength(0);
		expect(row.querySelector('a')).toBe(null);
		expect(row.querySelector('s')?.textContent).toBe('beach-day.mp4');
		expect(words(what)).toBe('You deleted beach-day.mp4, a file of theirs');
	});

	it('are nothing at all when the line names nothing', () => {
		expect(render({ event: event() }).named).toHaveLength(0);
	});
});

describe('a line that names a list and folds the rest', () => {
	/* Past five, the server names five and says "and 3 more"; that run CARRIES the rest, which the
	   row opens in place: one piece, found by nothing. */
	const username = (n: number) =>
		thing('username', `u${n}`, `name${n}`, { href: `/browse?username=u${n}` });
	const folding = () =>
		event({
			kind: 'filed',
			pieces: [
				words_('The usernames '),
				username(0),
				words_(', '),
				username(1),
				words_(', '),
				username(2),
				words_(', '),
				username(3),
				words_(', '),
				username(4),
				{
					...words_('3 more'),
					lead: ' and ',
					rest: [words_(', '), username(5), words_(', '), username(6), words_(' and '), username(7)]
				},
				words_(' were added to it')
			]
		});

	it('names the first five as links and draws the rest as one press away', () => {
		const { named, what } = render({ event: folding() });

		expect(named.map((one) => one.textContent)).toEqual([
			'name0',
			'name1',
			'name2',
			'name3',
			'name4'
		]);
		expect(named[0].getAttribute('href')).toBe('/browse?username=u0');
		expect(words(what)).toBe(
			'The usernames name0, name1, name2, name3, name4 and 3 more were added to it'
		);
		expect(host.querySelector('.each')).toBe(null);
	});

	it('opens the rest in place, as one list', () => {
		const { what } = render({ event: folding() });

		(what.querySelector('button') as HTMLButtonElement).click();
		flushSync();

		expect(words(what)).toBe(
			'The usernames name0, name1, name2, name3, name4, name5, name6 and name7 were added to it'
		);
		expect([...what.querySelectorAll('a.named')].map((one) => one.textContent)).toHaveLength(8);
		expect(what.querySelector('button')).toBe(null);
	});
});

describe('a way out of Sift', () => {
	/* A stash-box's match offers the box's own page for the scene: somebody else's page, so a
	   new tab that cannot reach back, and gone with the line once it no longer stands. */
	it("opens the stash-box's page for the scene in a new tab", () => {
		render({
			event: event({
				kind: 'enriched',
				away: { label: 'Open on StashDB', href: 'https://stashdb.org/scenes/s1' }
			})
		});

		const away = host.querySelector('a.away') as HTMLAnchorElement;
		expect(away.textContent?.trim()).toBe('Open on StashDB');
		expect(away.getAttribute('href')).toBe('https://stashdb.org/scenes/s1');
		expect(away.getAttribute('target')).toBe('_blank');
		expect(away.getAttribute('rel')).toContain('noopener');
	});

	it('draws none on a line with nowhere outside to go', () => {
		render({ event: event({ kind: 'enriched' }) });

		expect(host.querySelector('a.away')).toBeNull();
	});
});

describe('a line that stands for a whole press', () => {
	/*
	 * A stash-box that named seventeen people in one go would be seventeen rows, one under the other,
	 * burying everything that happened to the file before or after them. The server folds them into
	 * one line that counts them; this is where the seventeen can still be reached.
	 */
	it('folds the names under a disclosure, each a way to the thing it names', () => {
		render({
			event: event({
				kind: 'enriched',
				what: 'StashDB recognised this file and wrote 1 person and 1 tag',
				detail: [
					{
						kind: 'person',
						words: '1 person',
						entries: [{ kind: 'person', id: 'p1', name: 'Ada Lumen', href: null, gone: false }]
					},
					{
						kind: 'tag',
						words: '1 tag',
						entries: [{ kind: 'tag', id: 't1', name: 'poolside', href: null, gone: false }]
					}
				]
			})
		});

		const inside = [...host.querySelectorAll('.each li a')] as HTMLAnchorElement[];
		/* The chip's own label and not the whole anchor: the picture in front of it draws the thing's
		   first letter while no cover has loaded, which is text too. */
		expect(inside.map((one) => words(one.querySelector('.label') as HTMLElement))).toEqual([
			'Ada Lumen',
			'poolside'
		]);
		expect(inside[0].getAttribute('href')).toBe('/people/p1');
	});

	it('cuts it into a section per group, headed by the sentence own words and the kind mark', () => {
		/*
		 * A long list of names needs headings saying which are the people and which the tags. The
		 * headings are the server's, the phrases the sentence itself was built from, so a heading
		 * and the count above it cannot disagree.
		 */
		render({
			event: event({
				kind: 'enriched',
				what: 'StashDB recognised this file and wrote 2 people and 1 tag',
				detail: [
					{
						kind: 'person',
						words: '2 people',
						entries: [
							{ kind: 'person', id: 'p1', name: 'Ada Lumen', href: null, gone: false },
							{ kind: 'person', id: 'p2', name: 'Neve Alder', href: null, gone: false }
						]
					},
					{
						kind: 'tag',
						words: '1 tag',
						entries: [{ kind: 'tag', id: 't1', name: 'poolside', href: null, gone: false }]
					}
				]
			})
		});

		const groups = [...host.querySelectorAll('.group')];
		expect(groups).toHaveLength(2);
		expect(groups.map((one) => words(one.querySelector('.heading') as HTMLElement))).toEqual([
			'2 people',
			'1 tag'
		]);
		/* The kind's own mark at the head of each, read from the one table the rail and the walls
		   read theirs from, not a second set of glyphs for the same five kinds. */
		expect(groups.map((one) => one.querySelector('.kind')?.textContent)).toEqual([
			glyph('person'),
			glyph('shoppingmode')
		]);
		expect(groups.map((one) => one.querySelectorAll('li').length)).toEqual([2, 1]);
	});

	it('draws each entry as a chip with a link and its own cover', () => {
		/*
		 * Each name wears its entity's cover to the left. The picture is the chip's rather than an
		 * avatar beside a word, so an entry with a cover and one without measure the same.
		 */
		render({
			event: event({
				kind: 'named',
				what: 'Sift, from a folder name, named 2 people in this file',
				detail: [
					{
						kind: 'person',
						words: '2 people',
						entries: [
							{ kind: 'person', id: 'p1', name: 'Ada Lumen', href: null, gone: false },
							{ kind: 'person', id: 'p2', name: 'Neve Alder', href: null, gone: false }
						]
					}
				]
			})
		});

		const chips = [...host.querySelectorAll('.each li a')] as HTMLAnchorElement[];
		expect(chips.map((one) => one.getAttribute('href'))).toEqual(['/people/p1', '/people/p2']);
		expect(chips.map((one) => one.querySelector('img')?.getAttribute('src'))).toEqual([
			'/api/people/p1/cover',
			'/api/people/p2/cover'
		]);
	});

	it('draws a kind it has no page for as a chip with no link and no picture', () => {
		/* An older client meeting a newer server is the ordinary case for a self-hosted app. The
		   words are still drawn; nothing is guessed about where they go. */
		render({
			event: event({
				kind: 'named',
				what: 'Two of something happened',
				detail: [
					{
						kind: 'newer_thing',
						words: '2 of them',
						entries: [{ kind: 'newer_thing', id: 'x1', name: 'Something', href: null, gone: false }]
					}
				]
			})
		});

		const group = host.querySelector('.group') as HTMLElement;
		expect(group.querySelector('.kind')).toBeNull();
		expect(group.querySelectorAll('a')).toHaveLength(0);
		expect(words(group)).toContain('Something');
	});

	it('draws nothing at all where the line is only itself', () => {
		const drawn = render({ event: event({ detail: [] }) });

		expect(host.querySelector('.each')).toBeNull();
		expect(drawn.row).not.toBeNull();
	});

	it('leaves the names OUT of the line, which counts them', () => {
		/* They arrive as a group under "Show each" rather than as pieces of the line, which says
		   how many. */
		render({
			event: event({
				kind: 'tagged',
				pieces: [words_('Sift added 2 tags here from a folder name')],
				detail: [
					{
						kind: 'tag',
						words: '2 tags',
						entries: [
							{ kind: 'tag', id: 't1', name: 'poolside', href: null, gone: false },
							{ kind: 'tag', id: 't2', name: 'split screen', href: null, gone: false }
						]
					}
				]
			})
		});

		expect((host.querySelector('.what .line') as HTMLElement).textContent).toBe(
			'Sift added 2 tags here from a folder name'
		);
	});
});

describe('what the mark means', () => {
	it("is said in words on hover and on focus: a task's own label, or the server's words", () => {
		/* A glyph is a picture of a category and nothing says which until you already know. A
		   task's mark reads its words from the table its glyph came from; a kind's are the
		   server's (`means`). */
		render({ event: event({ kind: 'named', via: 'folder' }) });

		/* The bubble is only in the page while it is open, so what is asserted here is that the mark
		   is WRAPPED by one at all and that the words it was given are the mark's own. The tooltip's
		   own behaviour (when it opens, what it describes) is `Tooltip`'s test. */
		const mark = host.querySelector('.mark') as HTMLElement;
		expect(mark.closest('.target')).not.toBeNull();
		expect(markWords({ via: 'folder', means: 'x', actor: 'sift' })).toBe(
			'Sift: from a folder name'
		);
	});
});
