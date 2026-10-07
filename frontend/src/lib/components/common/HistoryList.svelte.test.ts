/* A run of events as one thread.
 *
 * What the list owns, and therefore what is asserted here, is three things: the order it was given
 * is the order it draws, the spinner lands on the one row whose undo is in flight rather than on
 * every row together, and nothing at all is a sentence rather than an empty box.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { mount } from 'svelte';
import { words } from '$lib/design/testing.svelte';
import HistoryList from './HistoryList.svelte';
import type { HistoryEvent } from './history';

let host: HTMLElement;

afterEach(() => {
	host?.remove();
});

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

function render(props: Parameters<typeof HistoryList>[1]) {
	host = document.createElement('div');
	document.body.append(host);
	mount(HistoryList, { target: host, props });
	return {
		rows: [...host.querySelectorAll('.history-row')] as HTMLElement[],
		list: host.querySelector('.history-list') as HTMLElement | null,
		host
	};
}

describe('the order', () => {
	it('is the order it was given, because the server decided it', () => {
		// A prop to flip it would be a second answer to which way time runs, and two screens would
		// eventually disagree, the one thing a history may not be ambiguous about.
		const { rows } = render({
			events: [
				event({ what: 'Added to the library.' }),
				event({ what: 'Tagged poolside.', kind: 'tagged', at: 1757000100 }),
				event({ what: 'Renamed to clip.mp4.', kind: 'renamed', at: 1757000200 })
			]
		});

		expect(rows.map((row) => words(row.querySelector('.what')))).toEqual([
			'Added to the library.',
			'Tagged poolside.',
			'Renamed to clip.mp4.'
		]);
	});

	it('draws two events that happened at the same second as two rows', () => {
		// The key is the kind, the time and the position together. Two events of one kind at one
		// moment is an ordinary thing (a bulk press writes a row per file at one stamp) and a
		// key that was only the two would collapse them into one.
		const { rows } = render({
			events: [
				event({ kind: 'tagged', what: 'Tagged poolside.' }),
				event({ kind: 'tagged', what: 'Tagged summer.' })
			]
		});

		expect(rows).toHaveLength(2);
	});
});

describe('the thread', () => {
	it('is there whenever there is anything to join up', () => {
		expect(render({ events: [event()] }).list).not.toBeNull();
	});
});

describe('nothing yet', () => {
	it('is a sentence rather than an empty box', () => {
		const { list, host: drawn } = render({ events: [] });

		expect(list).toBeNull();
		expect(words(drawn)).toContain('Nothing has been recorded about this yet.');
	});

	it('says it in the caller words when the caller knows what this is of', () => {
		const { host: drawn } = render({ events: [], emptyText: 'Nobody has decided anything here.' });

		expect(words(drawn)).toContain('Nobody has decided anything here.');
	});
});

describe('an undo in flight', () => {
	it('is drawn on the row that asked and on no other', () => {
		// An id rather than a boolean: a history is a list, and a spinner on every row at the same
		// time would say every one of them is being taken back.
		const { rows } = render({
			events: [
				event({ kind: 'renamed', undo: { kind: 'move', id: 'm1' } }),
				event({ kind: 'moved', at: 1757000100, undo: { kind: 'move', id: 'm2' } })
			],
			onundo: vi.fn(),
			undoing: 'm2'
		});

		const busy = rows.map((row) => row.querySelector('button')?.disabled);
		expect(busy).toEqual([false, true]);
	});

	it('is drawn nowhere while nothing is in flight', () => {
		const { rows } = render({
			events: [event({ kind: 'renamed', undo: { kind: 'move', id: 'm1' } })],
			onundo: vi.fn()
		});

		expect(rows[0].querySelector('button')?.disabled).toBe(false);
	});
});
