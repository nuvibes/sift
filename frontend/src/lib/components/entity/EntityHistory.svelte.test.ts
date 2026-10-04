/* The history panel on an entity page: what it asks for, when, and what it says when it cannot.
 *
 * This is the half of a history that `HistoryList` deliberately does not do: the asking. Inside a
 * person's page none of it could be tested (mounting that screen takes fifteen stand-ins), so the
 * panel is its own file, and the only stand-in here is the module it fetches through.
 *
 * What is asserted is the behaviour, not the wiring: one request per showing, the events on screen,
 * a sentence rather than a blank when the read fails, the same question asked again the next time
 * somebody comes back to it, and an Undo that goes through the one module that knows which door a
 * kind of event opens.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
import type { Snippet } from 'svelte';

import { reactiveProps, words } from '$lib/design/testing.svelte';
import type { HistoryEvent } from '$lib/components/common/history';

/* The only stand-in in the file. Everything else this draws (the list, the rows, the empty line,
   the problem line) is the real component, because what those SAY is what is being asserted. */
const asked = vi.hoisted(() => ({
	history: vi.fn(),
	site: vi.fn(),
	tag: vi.fn(),
	collection: vi.fn(),
	photoSet: vi.fn(),
	song: vi.fn(),
	undo: vi.fn()
}));

vi.mock('$lib/api/history', () => ({
	historyOfPerson: asked.history,
	historyOfSite: asked.site,
	historyOfTag: asked.tag,
	historyOfCollection: asked.collection,
	historyOfPhotoSet: asked.photoSet,
	historyOfSong: asked.song,
	undoHistoryEvent: asked.undo
}));

import { answered } from '$lib/organize/organize.svelte';
import { libraryChanges, recorded } from '$lib/library/changes.svelte';
import EntityHistory from './EntityHistory.svelte';

function event(overrides: Partial<HistoryEvent> = {}): HistoryEvent {
	return {
		at: 1757000000,
		actor: 'sift',
		actor_name: 'Sift',
		kind: 'added',
		what: 'Added to the library.',
		undo: null,
		reversed: false,
		pieces: [],
		means: '',
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

let host: HTMLElement;
let instance: Record<string, unknown> | null = null;

beforeEach(() => {
	asked.history.mockResolvedValue([]);
	for (const one of [asked.site, asked.tag, asked.collection, asked.photoSet]) {
		one.mockResolvedValue([]);
	}
	asked.undo.mockResolvedValue(undefined);
});

/* Taken down after every test, not merely emptied. An effect left running outlives the test that
   started it, and the next test's answer then wakes this one's panel as well: one press, two
   requests, and a count that is wrong in a file where counts are the assertion. */
afterEach(() => {
	takeItAway();
	vi.clearAllMocks();
});

function takeItAway() {
	if (instance) void unmount(instance, { outro: false });
	instance = null;
	host?.remove();
}

type Subject = 'person' | 'site' | 'tag' | 'collection' | 'photo_set';

function draw(props: { subject: Subject; id: string; name?: string; waiting?: Snippet }) {
	host = document.createElement('div');
	document.body.append(host);
	instance = mount(EntityHistory, { target: host, props });
	flushSync();
	return host;
}

/** Let every pending answer land and the panel redraw around it. A timer rather than a count of
 *  microtasks, because an undo is a read behind a write and nothing should have to know that. */
async function settle() {
	await new Promise((done) => setTimeout(done, 0));
	flushSync();
}

function rows(): HTMLElement[] {
	return [...host.querySelectorAll('.history-row')] as HTMLElement[];
}

describe('asking', () => {
	it('asks the person address once, when it is first shown', async () => {
		draw({ subject: 'person', id: 'p1' });
		await settle();
		await settle();

		expect(asked.history).toHaveBeenCalledTimes(1);
		expect(asked.history).toHaveBeenCalledWith('p1');
	});

	it('says who it is reading about while the answer is on its way', () => {
		asked.history.mockReturnValue(new Promise(() => {}));

		draw({ subject: 'person', id: 'p1', name: 'Marisol Vance' });

		expect(words(host.querySelector('.empty'))).toBe('Reading what happened to Marisol Vance');
	});

	it('still says something true when the screen has no name for them yet', () => {
		asked.history.mockReturnValue(new Promise(() => {}));

		draw({ subject: 'person', id: 'p1' });

		expect(words(host.querySelector('.empty'))).toBe('Reading what happened');
	});
});

describe('what is waiting on the record', () => {
	/*
	 * What is waiting on a record lives on this pane, where a question about a record has room to
	 * be as tall as it needs, and in the column beside the thread rather than stacked on top of it.
	 */
	function question() {
		return createRawSnippet(() => ({
			render: () => '<p data-waiting>One field a stash-box disagrees with</p>'
		}));
	}

	it('draws it in a column of its own, beside the thread rather than inside it', async () => {
		asked.history.mockResolvedValue([event()]);

		draw({ subject: 'person', id: 'p1', waiting: question() });
		await settle();

		const waiting = host.querySelector('[data-waiting]');
		const thread = host.querySelector('.history-row');
		if (!waiting || !thread) throw new Error('the pane drew neither the question nor the thread');
		/* NEITHER IS INSIDE THE OTHER, which is the whole of what the markup has to get right: the
		   two columns are the pane's own children and the grid is what puts one to the right of the
		   other. Which side they land on is a width the browser decides and jsdom lays out nothing,
		   so asserting a position here would be asserting the stylesheet: `e2e/corners.spec.ts`
		   and a look at the running app are where that is settled. */
		expect(waiting.closest('.thread')).toBeNull();
		expect(thread.closest('.beside')).toBeNull();
		expect(waiting.closest('.beside')).not.toBeNull();
		expect(thread.closest('.thread')).not.toBeNull();
	});

	it('draws it even when the thread itself could not be read', async () => {
		// The two are unrelated: a read of the history that failed says nothing about a question
		// waiting on the record, and hiding one behind the other would take it away for a reason
		// that has nothing to do with it.
		asked.history.mockRejectedValue(new Error('no'));

		draw({ subject: 'person', id: 'p1', waiting: question() });
		await settle();
		await settle();

		expect(host.querySelector('.problem')).not.toBeNull();
		expect(host.querySelector('[data-waiting]')).not.toBeNull();
	});
});

describe('what it draws', () => {
	it('hands the events to the shared list, in the order the server sent them', async () => {
		asked.history.mockResolvedValue([
			event({ what: 'Added to the library.' }),
			event({ what: 'Agreed that is them.', kind: 'confirmed', at: 1757000100 })
		]);

		draw({ subject: 'person', id: 'p1' });
		await settle();

		expect(rows().map((row) => words(row.querySelector('.what')))).toEqual([
			'Added to the library.',
			'Agreed that is them.'
		]);
	});

	it('says nothing is recorded about THEM, rather than about "this"', async () => {
		// The list's own default is right and impersonal. The words belong to the screen, which is
		// the only thing that knows what the history is of.
		draw({ subject: 'person', id: 'p1' });
		await settle();

		expect(words(host.querySelector('.empty'))).toBe('Nothing has been recorded about them yet.');
	});
});

describe('when the read fails', () => {
	it('says so, rather than leaving the busy line spinning for ever', async () => {
		asked.history.mockRejectedValue(new Error('offline'));

		draw({ subject: 'person', id: 'p1' });
		await settle();

		expect(words(host.querySelector('.problem'))).toBe("That history couldn't be read.");
	});

	it('asks again the next time it is shown, because a failure is not an answer', async () => {
		asked.history.mockRejectedValueOnce(new Error('offline'));

		draw({ subject: 'person', id: 'p1' });
		await settle();
		expect(host.querySelector('.problem')).not.toBeNull();

		// Leaving the tab takes the panel away and coming back brings a new one. That is the whole
		// of how somebody retries, so it has to be the whole of the retry.
		takeItAway();
		asked.history.mockResolvedValue([event({ what: 'Added to the library.' })]);

		draw({ subject: 'person', id: 'p1' });
		await settle();

		expect(asked.history).toHaveBeenCalledTimes(2);
		expect(rows()).toHaveLength(1);
		expect(host.querySelector('.problem')).toBeNull();
	});
});

describe('moving between two of them', () => {
	/* The panel survives the step from one person to the next: the tab rides in the address, so
	   moving between two people on the History word keeps it mounted. Everything here is about that
	   one mounted panel being handed a second subject while the first one's read is still out. */
	type Answer = { give: (events: HistoryEvent[]) => void; refuse: (why: Error) => void };

	function drawMoving(): { props: { subject: 'person'; id: string }; answers: Answer[] } {
		const answers: Answer[] = [];
		asked.history.mockImplementation(
			() => new Promise((give, refuse) => answers.push({ give, refuse }))
		);

		const props = reactiveProps({ subject: 'person' as const, id: 'p1' });
		host = document.createElement('div');
		document.body.append(host);
		instance = mount(EntityHistory, { target: host, props });
		flushSync();
		return { props, answers };
	}

	it('takes the last one off the screen before the next one is read', async () => {
		const { props, answers } = drawMoving();
		answers[0].give([event({ what: 'Added to the library.' })]);
		await settle();
		expect(rows()).toHaveLength(1);

		props.id = 'p2';
		flushSync();

		// Not when the answer lands: the words around the rows are the same for both people, so
		// nothing else on the screen would say whose events these are.
		expect(rows()).toHaveLength(0);
		expect(asked.history).toHaveBeenCalledTimes(2);
	});

	it('does not draw a slower answer for the one moved away from', async () => {
		const { props, answers } = drawMoving();
		props.id = 'p2';
		flushSync();

		answers[0].give([event({ what: 'Added to the library.' })]);
		await settle();
		expect(rows()).toHaveLength(0);

		answers[1].give([event({ what: 'Agreed that is them.', kind: 'confirmed' })]);
		await settle();
		expect(rows().map((row) => words(row.querySelector('.what')))).toEqual([
			'Agreed that is them.'
		]);
	});

	it('does not report a failure for the one moved away from', async () => {
		const { props, answers } = drawMoving();
		props.id = 'p2';
		flushSync();

		answers[0].refuse(new Error('offline'));
		await settle();

		expect(host.querySelector('.problem')).toBeNull();
		expect(words(host.querySelector('.empty'))).toBe('Reading what happened');
	});
});

describe('taking one back', () => {
	const reversible = event({
		kind: 'decided',
		what: 'Settled at the workbench.',
		undo: { kind: 'decision', id: 'd7' }
	});

	async function drawOneUndoable() {
		asked.history.mockResolvedValue([reversible]);
		draw({ subject: 'person', id: 'p1' });
		await settle();
		return host.querySelector('.history-row button') as HTMLButtonElement;
	}

	it('hands the whole event to the module that knows which door it opens', async () => {
		const undo = await drawOneUndoable();

		undo.click();
		await settle();

		expect(asked.undo).toHaveBeenCalledTimes(1);
		expect(asked.undo).toHaveBeenCalledWith(reversible);
	});

	it('re-reads afterwards, so the thread says what it is now rather than what it was', async () => {
		const undo = await drawOneUndoable();
		asked.history.mockResolvedValue([
			{ ...reversible, reversed: true },
			event({ kind: 'undone', what: 'Taken back.', at: 1757000200 })
		]);

		undo.click();
		await settle();

		expect(asked.history).toHaveBeenCalledTimes(2);
		expect(rows()).toHaveLength(2);
	});
});

describe('the subject', () => {
	it('decides which address is asked, from the table and not from a branch', () => {
		// The point of the table: the sixth kind is a row, and because the table is typed by the
		// prop, adding a kind to the prop's type is a missing row the type checker names.
		const cases = [
			['site', asked.site],
			['tag', asked.tag],
			['collection', asked.collection],
			['photo_set', asked.photoSet]
		] as const;

		for (const [subject, reader] of cases) {
			draw({ subject, id: 'x1' });
			expect(reader).toHaveBeenCalledWith('x1');
			takeItAway();
		}

		// And none of them woke the person's read, which is what says the table is being used
		// rather than a default being reached for.
		expect(asked.history).not.toHaveBeenCalled();
	});

	it('says what is empty in that subject own words', async () => {
		asked.site.mockResolvedValue([]);

		const where = draw({ subject: 'site', id: 'pf1' });
		await settle();

		expect(words(where)).toBe('Nothing has been recorded about this Site yet.');
	});
});

describe('a decision made beside it', () => {
	/*
	 * A stash-box answer pressed beside the thread must update the thread at once: the decision is
	 * written into this very history. `answered` is the one signal every decision moves.
	 */
	it('reads the thread again when something is decided, keeping the old one up meanwhile', async () => {
		asked.history.mockResolvedValue([event({ what: 'Added to the library.' })]);
		draw({ subject: 'person', id: 'p1' });
		await settle();
		expect(asked.history).toHaveBeenCalledTimes(1);

		let land: (events: HistoryEvent[]) => void = () => {};
		asked.history.mockReturnValue(new Promise((yes) => (land = yes)));
		answered.changed();
		// Settled over a short window rather than at once. See `rereadOnHistoryChange`.
		await vi.waitFor(() => expect(asked.history).toHaveBeenCalledTimes(2));
		// Not blanked to the busy line while the new thread is on its way.
		expect(host.querySelector('.empty')).toBeNull();
		expect(rows()).toHaveLength(1);

		land([event({ what: 'Kept your answer for Jane' }), event()]);
		await settle();

		expect(rows()).toHaveLength(2);
		expect(words(rows()[0])).toContain('Kept your answer for Jane');
	});

	/*
	 * A file put in a collection is not a workbench decision and moves no `answered` stamp, so the
	 * thread also listens for `recorded`, which every write in this tab rings; a write made
	 * anywhere else reaches it as the server's `libraryChanges`.
	 */
	it('reads the thread again when this tab writes anything it records', async () => {
		draw({ subject: 'collection', id: 'c1' });
		await settle();
		expect(asked.collection).toHaveBeenCalledTimes(1);

		recorded.changed();
		await vi.waitFor(() => expect(asked.collection).toHaveBeenCalledTimes(2));
	});

	it('reads the thread again when the server says the library moved', async () => {
		draw({ subject: 'collection', id: 'c1' });
		await settle();

		libraryChanges.changed();
		await vi.waitFor(() => expect(asked.collection).toHaveBeenCalledTimes(2));
	});

	it('asks once for a burst of bells, not once per bell', async () => {
		draw({ subject: 'tag', id: 't1' });
		await settle();

		// One press can ring all three: a decision rings `answered` (and through it `recorded`),
		// and the server's announcement follows; a chunked bulk write rings once per chunk.
		answered.changed();
		recorded.changed();
		libraryChanges.changed();
		await vi.waitFor(() => expect(asked.tag).toHaveBeenCalledTimes(2));
		await new Promise((done) => setTimeout(done, 400));
		expect(asked.tag).toHaveBeenCalledTimes(2);
	});

	it('does not read twice on opening, whatever the signal already says', async () => {
		answered.changed();
		recorded.changed();
		libraryChanges.changed();
		draw({ subject: 'person', id: 'p1' });
		await settle();
		await new Promise((done) => setTimeout(done, 400));
		await settle();

		expect(asked.history).toHaveBeenCalledTimes(1);
	});
});
