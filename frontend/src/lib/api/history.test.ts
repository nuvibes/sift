/* Asking for a history, and taking one of its events back. */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import {
	historyOfAsset,
	historyOfCollection,
	historyOfPerson,
	historyOfPhotoSet,
	historyOfSite,
	historyOfTag,
	undoHistoryEvent
} from '$lib/api/history';
import type { HistoryEvent } from '$lib/components/common/history';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn() }
}));

const mocked = vi.mocked(api);

beforeEach(() => {
	vi.clearAllMocks();
	mocked.get.mockResolvedValue([]);
	mocked.post.mockResolvedValue(undefined);
});

function event(over: Partial<HistoryEvent> = {}): HistoryEvent {
	return {
		at: 1757000000,
		actor: 'somebody',
		actor_name: null,
		kind: 'moved',
		what: 'Moved to clips.',
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
		...over
	};
}

describe('the address', () => {
	it('is the file own history, with the id escaped', () => {
		// An id is minted by Sift and has nothing in it to escape today.
		void historyOfAsset('01HX/0');

		expect(mocked.get).toHaveBeenCalledWith('/assets/01HX%2F0/history', {
			query: { limit: undefined }
		});
	});

	it('asks for a number of events only when the caller said one', () => {
		// Left out, the server answers with its own default.
		void historyOfAsset('a1', 10);

		expect(mocked.get).toHaveBeenCalledWith('/assets/a1/history', { query: { limit: 10 } });
	});
});

describe('the answer', () => {
	it('is the wire shape, unchanged', async () => {
		// Every sentence in a history is written by the server in the app own voice, so there is
		// nothing here to translate, and a mapping layer would be the second vocabulary the
		// sentences exist to avoid.
		const sent = [
			{
				at: 1757000000,
				actor: 'sift',
				actor_name: 'Sift',
				kind: 'added',
				what: 'Added to the library.',
				undo: null,
				reversed: false
			}
		];
		mocked.get.mockResolvedValue({ items: sent, total: 1 });

		expect(await historyOfAsset('a1')).toEqual({ items: sent, total: 1 });
	});
});

describe('the other subjects', () => {
	it("ask at a person's own address", () => {
		void historyOfPerson('01HX/0', 3);

		expect(mocked.get).toHaveBeenCalledWith('/people/01HX%2F0/history', { query: { limit: 3 } });
	});

	/* The escaping rule holds on every address here: the file's first case proves it. */
});

describe('taking an event back', () => {
	it('sends a move through the organizer', async () => {
		await undoHistoryEvent(event({ undo: { kind: 'move', id: 'm1' } }));

		expect(mocked.post).toHaveBeenCalledWith('/moves/m1/undo', {});
	});

	it('and a decision through the workbench', async () => {
		// The two doors are the whole reason the kind travels with the id.
		await undoHistoryEvent(event({ kind: 'decided', undo: { kind: 'decision', id: 'd1' } }));

		expect(mocked.post).toHaveBeenCalledWith('/workbench/decisions/d1/undo', {});
	});

	it('does nothing at all for an event with no door', async () => {
		// Most events have none. Refusing would make every caller ask the same question twice.
		await undoHistoryEvent(event());

		expect(mocked.post).not.toHaveBeenCalled();
	});
});

describe('the four addresses beside a person', () => {
	it('are each that entity own history, with the id escaped', () => {
		// Written here and nowhere else, which is what makes this the only place a wrong one can be
		// caught, and a wrong address reaches somebody as an empty panel with no error on it.
		void historyOfSite('pf/1');
		void historyOfTag('t/1');
		void historyOfCollection('c/1');
		void historyOfPhotoSet('s/1');

		expect(mocked.get.mock.calls.map((call) => call[0])).toEqual([
			'/sites/pf%2F1/history',
			'/tags/t%2F1/history',
			'/collections/c%2F1/history',
			'/photo-sets/s%2F1/history'
		]);
	});

	it('ask for a number of events only when the caller said one', () => {
		void historyOfTag('t1', 10);
		void historyOfCollection('c1');

		expect(mocked.get.mock.calls.map((call) => call[1])).toEqual([
			{ query: { limit: 10 } },
			{ query: { limit: undefined } }
		]);
	});
});
