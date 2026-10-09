/* The pile of fields two answers differ about, and what taking one of them does. */

import { beforeEach, expect, it, vi } from 'vitest';

import { api, ApiError } from '$lib/api/client';
import {
	boxesOf,
	disagreements,
	pageOf,
	problemFrom,
	settle,
	waitingText,
	type Disagreement
} from '$lib/entity/reconcile.svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
	ApiError: class extends Error {
		status: number;
		detail?: string;
		constructor(status: number, message: string, detail?: string) {
			super(message);
			this.status = status;
			this.detail = detail;
		}
	}
}));

const mocked = vi.mocked(api);

function row(over: Partial<Disagreement> = {}): Disagreement {
	return {
		subject: 'person',
		local_id: 'p-1',
		name: 'Jane',
		box_id: 'box-1',
		box_name: 'StashDB',
		key: 'birth_date',
		mine: '1990-01-01',
		theirs: '1991-02-02',
		mine_said: null,
		theirs_said: null,
		...over
	};
}

beforeEach(() => {
	vi.resetAllMocks();
});

it('reads the pile out of the answer rather than handing the answer back', async () => {
	// Every other screen here takes a list. A caller that had to reach through a wrapper is a
	// caller that has to know the wrapper is there, and the next one will forget.
	mocked.get.mockResolvedValue({ disagreements: [row()] });

	expect(await disagreements()).toEqual([row()]);
	// The signal travels even when there is none: the store's one call site is the panel's, which
	// hands its abort signal down, and a second spelling for the signal-less case is a second call.
	expect(mocked.get).toHaveBeenCalledWith('/stash-boxes/disagreements', { signal: undefined });
});

it('sends the whole of what identifies one row, and no more', async () => {
	// Four parts, because a disagreement is a FIELD on a subject as one box sees it.
	mocked.post.mockResolvedValue({ files: 0, fields: 1, created: 0, decision_id: 'd-1' });

	await settle(row(), true);

	expect(mocked.post).toHaveBeenCalledWith('/stash-boxes/disagreements/settle', {
		body: {
			subject: 'person',
			local_id: 'p-1',
			box_id: 'box-1',
			key: 'birth_date',
			take_theirs: true
		}
	});
});

it('still sends the decision when the answer is to keep what is already there', async () => {
	// The one that looks like it could be skipped. Nothing is written either way, but the row only
	// leaves the pile because the server was told: the list is worked out on every read.
	mocked.post.mockResolvedValue({ files: 0, fields: 0, created: 0, decision_id: 'd-2' });

	await settle(row(), false);

	expect(mocked.post).toHaveBeenCalledTimes(1);
	expect(mocked.post.mock.calls[0][1]).toMatchObject({ body: { take_theirs: false } });
});

it('opens the right page for each kind of subject', () => {
	expect(pageOf(row({ subject: 'person', local_id: 'p-9' }))).toBe('/people/p-9');
	expect(pageOf(row({ subject: 'site', local_id: 's-9' }))).toBe('/sites/s-9');
	expect(pageOf(row({ subject: 'tag', local_id: 't-9' }))).toBe('/tags/t-9');
	expect(pageOf(row({ subject: 'asset', local_id: 'a-9' }))).toBe('/asset/a-9');
});

it('shows what the server said, and one flat sentence for everything else', () => {
	expect(
		problemFrom(new ApiError(409, 'no', 'That link was forgotten while you were reading.'))
	).toBe('That link was forgotten while you were reading.');
	expect(problemFrom(new ApiError(500, 'no'))).toBe("That didn't work.");
	expect(problemFrom(new Error('offline'))).toBe("That didn't work.");
});

it('names the boxes that disagree, and says a stash-box only where no row names one', () => {
	expect(waitingText(1, ['FansDB'])).toBe('One field FansDB disagrees with');
	expect(waitingText(3, ['FansDB', 'StashDB'])).toBe('3 fields FansDB and StashDB disagree with');
	expect(waitingText(2)).toBe('2 fields a stash-box disagrees with');
	expect(
		boxesOf([{ box_name: 'StashDB' }, { box_name: 'FansDB' }, { box_name: 'StashDB' }])
	).toEqual(['StashDB', 'FansDB']);
});
