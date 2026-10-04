/* Merging: the count that comes first, and the write that cannot be taken back.
 *
 * What is worth testing here is that they are two calls and stay two calls. Weighing is the whole
 * guard on an irreversible act (it writes nothing and its numbers are what somebody presses
 * against), so a weigh that reached the writing route, or a merge that answered from the weighing
 * one, would leave the screen showing a confirmation of something that had already happened.
 *
 * And that the two subjects reach two different addresses. One pair of functions serves people and
 * sites, so the thing that can silently go wrong is the word: a request that sends site ids to the
 * people route would be answered with four 404s, and a request that sent them under the wrong field
 * name would be a 422 nothing on screen can explain.
 */

import { beforeEach, expect, it, vi } from 'vitest';

import { api, ApiError } from '$lib/api/client';
import { mergeSeveral, problemFrom, weighSeveral, type Weighed } from '$lib/entity/merge.svelte';

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

function weighed(over: Partial<Weighed> = {}): Weighed {
	return {
		from_name: 'Jane',
		into_name: 'Jane Doe',
		files: 12,
		usernames: 2,
		aliases: 1,
		links: 0,
		faces: 40,
		children: 0,
		facts: 0,
		usernames_named: [],
		aliases_named: [],
		links_named: [],
		faces_from: [],
		children_named: [],
		filled: [],
		...over
	};
}

beforeEach(() => {
	vi.resetAllMocks();
});

it('counts what would move at an address of its own, apart from the one that writes', async () => {
	// Two addresses, and that is the assertion. The numbers on the confirm screen are the whole
	// guard on an act with no undo, and a "count" that went to the writing route would be the act.
	mocked.post.mockResolvedValue(weighed());

	const answer = await weighSeveral('person', ['p-1'], 'p-2');

	expect(mocked.post).toHaveBeenCalledWith('/people/weigh-merge', {
		body: { people: ['p-1'], into: 'p-2' }
	});
	expect(answer.files).toBe(12);
});

it('names both in the answer, so the confirm can say who goes', async () => {
	mocked.post.mockResolvedValue(weighed({ from_name: 'Neve', into_name: 'Neve Arb' }));

	const answer = await weighSeveral('person', ['p-1'], 'p-2');

	expect([answer.from_name, answer.into_name]).toEqual(['Neve', 'Neve Arb']);
});

it('sends the survivor apart from the ones going, so the direction cannot be inferred', async () => {
	// The ids are not interchangeable and the direction is unrecoverable, so which is which is
	// worth an assertion rather than an eye.
	mocked.post.mockResolvedValue(weighed());

	await mergeSeveral('person', ['p-goes', 'p-also-goes'], 'p-stays');

	expect(mocked.post).toHaveBeenCalledWith('/people/merge', {
		body: { people: ['p-goes', 'p-also-goes'], into: 'p-stays' }
	});
});

it('sends sites to the sites route, under the word the server reads', async () => {
	// The one thing a shared pair of functions can get wrong. Site ids under `people` is a 422, and
	// site ids sent to `/people/merge` is a 404 about a site that is plainly there.
	mocked.post.mockResolvedValue(weighed({ from_name: 'Goner', into_name: 'Keeper' }));

	await weighSeveral('site', ['s-1'], 's-2');
	await mergeSeveral('site', ['s-1'], 's-2');

	expect(mocked.post).toHaveBeenNthCalledWith(1, '/sites/weigh-merge', {
		body: { sites: ['s-1'], into: 's-2' }
	});
	expect(mocked.post).toHaveBeenNthCalledWith(2, '/sites/merge', {
		body: { sites: ['s-1'], into: 's-2' }
	});
});

it('shows what the server said when the server wrote a reason', async () => {
	const refused = new ApiError(
		409,
		'That request was not valid',
		'Somebody cannot merge into themselves.'
	);

	expect(problemFrom(refused)).toBe('Somebody cannot merge into themselves.');
});

it('carries the two-logins refusal through, because it is the one somebody can act on', async () => {
	// Every other refusal is "one of these is not something I can show you", which has no
	// instruction in it. This one does, and a flat sentence would throw the instruction away.
	const refused = new ApiError(
		409,
		'That request was not valid',
		'Both of these sites have cookies saved, and only one can be kept.'
	);

	expect(problemFrom(refused)).toContain('only one can be kept');
});

it('falls back to one flat sentence for anything else', async () => {
	// A refusal that explains itself is the exception. Everything else (a dropped connection, a
	// 404 that must not confirm the thing exists) gets the same line.
	expect(problemFrom(new ApiError(404, 'not found'))).toBe("That didn't work.");
	expect(problemFrom(new Error('socket hang up'))).toBe("That didn't work.");
	expect(problemFrom('a string nobody threw on purpose')).toBe("That didn't work.");
});
