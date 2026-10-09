/* Asking the stash-boxes about a selection, and the three questions that are not one question. */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ post: vi.fn(), shown: vi.fn() }));

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { post: mocks.post }
}));
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: mocks.shown } }));

import { ApiError } from '$lib/api/client';
import { enrichFiles, enrichFolder, enrichMany, lookUpSongs } from './enrich-many.svelte';
import { wordsOf } from '$lib/components/common/toast-pieces';

/** What the toast said, with the tone it said it in. */
function said(): [string, { tone: string }] {
	const call = mocks.shown.mock.calls.at(-1);
	if (!call) throw new Error('nothing was said');
	return [wordsOf(call[0]), call[1]];
}

/** A refusal the server wrote a sentence for. */
function refused(detail: string): ApiError {
	return new ApiError(409, 'That request was not valid.', detail);
}

beforeEach(() => {
	vi.clearAllMocks();
	mocks.post.mockResolvedValue({});
});

afterEach(() => {
	vi.clearAllMocks();
});

it('asks about nothing rather than posting an empty selection', async () => {
	// A wall with nothing ticked still has the verb on it.
	await enrichMany('person', []);

	expect(mocks.post).not.toHaveBeenCalled();
	expect(mocks.shown).not.toHaveBeenCalled();
});

it('sends the subject and the ids together, because the server names all three', async () => {
	mocks.post.mockResolvedValue({ asked: 2 });

	await enrichMany('tag', ['t-1', 't-2']);

	expect(mocks.post).toHaveBeenCalledWith('/stash-boxes/enrich', {
		body: { subject: 'tag', ids: ['t-1', 't-2'] }
	});
});

it('says the number the SERVER asked about, not the number that were sent', async () => {
	// Two of the fourteen had gone since the wall drew them.
	mocks.post.mockResolvedValue({ asked: 12 });

	await enrichMany(
		'person',
		Array.from({ length: 14 }, (_, at) => `p-${at}`)
	);

	expect(said()[0]).toContain('12');
	expect(said()[1]).toEqual({ tone: 'success' });
});

it('falls back to the number sent when the server does not say', async () => {
	// An older server, or one that answers without the count.
	mocks.post.mockResolvedValue({});

	await enrichMany('site', ['s-1', 's-2', 's-3']);

	expect(said()[0]).toContain('3');
});

it('says it in the singular for one, which is the ordinary case from a record page', async () => {
	mocks.post.mockResolvedValue({ asked: 1 });

	await enrichMany('person', ['p-1']);

	expect(said()[0]).toBe('Asking the stash-boxes about it. Watch it in Activity.');
});

it('repeats the refusal the server gave rather than a sentence nobody can act on', async () => {
	// The one refusal that really happens: enriching is turned off under Stash-boxes.
	mocks.post.mockRejectedValue(
		refused('Enriching with stash-boxes is turned off. Turn it on under Stash-boxes.')
	);

	await enrichMany('person', ['p-1']);

	expect(said()[0]).toBe('Enriching with stash-boxes is turned off. Turn it on under Stash-boxes.');
	expect(said()[1]).toEqual({ tone: 'error' });
});

it('has something to say when the failure carries no sentence of its own', async () => {
	mocks.post.mockRejectedValue(new Error('the network went'));

	await enrichMany('person', ['p-1']);

	expect(said()[0]).toBe("Those couldn't be asked about");
	expect(said()[1]).toEqual({ tone: 'error' });
});

it('asks about a folder through the sweep, not through the by-name route', async () => {
	// A different question: that one asks what a NAME is, this asks what each FILE is.
	await enrichFolder('f-1', 'Holiday');

	// Auto-enrich, so the press carries its yes to an exact match.
	expect(mocks.post).toHaveBeenCalledWith('/stash-boxes/scan', {
		body: { folder: 'f-1', auto: true }
	});
	expect(said()[0]).toContain('Holiday');
});

it('names the folder in the refusal too, so it is clear which one did not go', async () => {
	mocks.post.mockRejectedValue(new Error('no'));

	await enrichFolder('f-1', 'Holiday');

	expect(said()[0]).toBe("Sift couldn't ask about Holiday");
	expect(said()[1]).toEqual({ tone: 'error' });
});

it('asks about files by their fingerprints, and answers with nothing when it worked', async () => {
	const problem = await enrichFiles(['a-1', 'a-2']);

	expect(mocks.post).toHaveBeenCalledWith('/stash-boxes/scan', {
		body: { assets: ['a-1', 'a-2'] }
	});
	expect(problem).toBeNull();
	expect(said()[0]).toContain('2 files');
});

it('answers with the refusal as well as showing it, so a caller can put it somewhere better', async () => {
	mocks.post.mockRejectedValue(
		refused('Enriching with stash-boxes is turned off. Turn it on under Stash-boxes.')
	);

	const problem = await enrichFiles(['a-1']);

	expect(problem).toBe('Enriching with stash-boxes is turned off. Turn it on under Stash-boxes.');
	expect(said()[0]).toBe('Enriching with stash-boxes is turned off. Turn it on under Stash-boxes.');
});

it('stays quiet when asked to, and still answers with what went wrong', async () => {
	// The sheet on one file has a whole panel to say this in.
	mocks.post.mockRejectedValue(
		refused('Enriching with stash-boxes is turned off. Turn it on under Stash-boxes.')
	);

	const problem = await enrichFiles(['a-1'], { quiet: true });

	expect(problem).toBe('Enriching with stash-boxes is turned off. Turn it on under Stash-boxes.');
	expect(mocks.shown).not.toHaveBeenCalled();
});

it('says nothing on the way in either, when it was asked to be quiet', async () => {
	const problem = await enrichFiles(['a-1'], { quiet: true });

	expect(problem).toBeNull();
	expect(mocks.shown).not.toHaveBeenCalled();
});

it('asks about no files rather than posting an empty list', async () => {
	const problem = await enrichFiles([]);

	expect(problem).toBeNull();
	expect(mocks.post).not.toHaveBeenCalled();
});

it('carries the Auto-enrich press and the box it named, so the server applies what is certain', async () => {
	await enrichFiles(['a-1', 'a-2'], { box: 'stashdb', auto: true });

	expect(mocks.post).toHaveBeenCalledWith('/stash-boxes/scan', {
		body: { assets: ['a-1', 'a-2'], box: 'stashdb', auto: true }
	});
	expect(said()[0]).toContain('Exact matches are filled in');
});

it('says a kept-local file was held back as a decision honoured, not as a file Sift lost', async () => {
	// The server's own sentence and flag; the toast reads the FLAG for its tone, never the words.
	mocks.post.mockResolvedValue({
		asked: 1,
		skipped: 1,
		reason: 'It is kept local \u2014 nothing about it leaves this machine.',
		reason_many: 'They are kept local \u2014 nothing about them leaves this machine.',
		vault_locked: false,
		kept_local: true
	});

	const problem = await enrichFiles(['a-1', 'a-2'], { auto: true });

	expect(problem).toContain('kept local');
	expect(said()[0]).toBe(
		'Asking about 1 of 2. It is kept local \u2014 nothing about it leaves this machine.'
	);
	expect(said()[1]).toEqual({ tone: 'info', icon: 'shield' });
});

it('writes a thousand as the selection bar does, never as a bare 1000', async () => {
	// A press of Select all is 1,000 files, and the bar over the wall says "1,000 files selected".
	const thousand = Array.from({ length: 1000 }, (_, index) => `a-${index}`);
	mocks.post.mockResolvedValue({
		asked: 996,
		skipped: 4,
		reason: 'It is in your vault.',
		reason_many: 'They are in your vault.',
		vault_locked: true,
		kept_local: false
	});

	await enrichFiles(thousand);

	expect(said()[0]).toBe('Asking about 996 of 1,000. They are in your vault.');

	mocks.post.mockResolvedValue({ asked: 1000, skipped: 0 });
	await enrichFiles(thousand);

	expect(said()[0]).toBe(
		'Asking the stash-boxes about 1,000 files. What comes back waits under Organize.'
	);
});

/* The route takes a thousand names at a time; a hand-made selection over it would be refused whole. */
it('asks about a selection over a thousand in parts, and says the whole of it once', async () => {
	const many = Array.from({ length: 2500 }, (_, index) => `a-${index}`);
	mocks.post.mockImplementation(
		async (_path: string, { body }: { body: { assets: string[] } }) => ({
			asked: body.assets.length,
			skipped: 0,
			reason: null,
			reason_many: null,
			vault_locked: false,
			kept_local: false
		})
	);

	expect(await enrichFiles(many, { auto: true })).toBeNull();

	const sent = mocks.post.mock.calls.map(([, { body }]) => body.assets.length);
	expect(sent).toEqual([1000, 1000, 500]);
	expect(mocks.post.mock.calls[2][1].body.auto).toBe(true);
	expect(mocks.shown).toHaveBeenCalledOnce();
	expect(said()[0]).toContain('about 2,500 files');
});

it('counts a part refused whole as left out, and goes on with the rest', async () => {
	const many = Array.from({ length: 1500 }, (_, index) => `a-${index}`);
	mocks.post
		.mockRejectedValueOnce(new ApiError(423, 'Locked', 'They are in your vault.'))
		.mockResolvedValueOnce({ asked: 500, skipped: 0, vault_locked: false, kept_local: false });

	await enrichFiles(many);

	expect(said()[0]).toBe('Asking about 500 of 1,500. They are in your vault.');
});

it('passes the box a wall flyout row named on to the enrichment of people', async () => {
	await enrichMany('person', ['p-1'], 'fansdb');

	expect(mocks.post).toHaveBeenCalledWith('/stash-boxes/enrich', {
		body: { subject: 'person', ids: ['p-1'], box: 'fansdb' }
	});
});

it('asks AcoustID about these files and says what the server said it queued', async () => {
	mocks.post.mockResolvedValue({
		job_id: 'j1',
		queued: 2,
		left_out: 0,
		said: 'Asking AcoustID about 2 files.'
	});
	await lookUpSongs(['a1', 'a2']);
	expect(mocks.post).toHaveBeenCalledWith('/music/lookup/files', {
		body: { asset_ids: ['a1', 'a2'] }
	});
	expect(said()).toEqual(['Asking AcoustID about 2 files.', { tone: 'success' }]);
});

it('asks again about files AcoustID did not know, when the file menu says so', async () => {
	mocks.post.mockResolvedValue({ job_id: 'j1', queued: 1, left_out: 0, said: 'Asking again.' });
	await lookUpSongs(['a1'], true);
	expect(mocks.post).toHaveBeenCalledWith('/music/lookup/files', {
		body: { asset_ids: ['a1'], again: true }
	});
});

it("repeats the server's refusal while the lookup is off or has no key", async () => {
	/* The sentence names the setting to change. A generic failure over it would make a switch in
	   `Settings > Music` look like a broken press. */
	mocks.post.mockRejectedValue(refused('Song lookup is off. Turn it on under Settings > Music.'));
	await lookUpSongs(['a1']);
	expect(said()).toEqual([
		'Song lookup is off. Turn it on under Settings > Music.',
		{ tone: 'error' }
	]);
});

it("presses five hundred files at a time, the route's own ceiling", async () => {
	mocks.post.mockResolvedValue({ job_id: 'j', queued: 1, left_out: 0, said: 'Queued.' });
	const ids = Array.from({ length: 501 }, (_, at) => `a${at}`);
	await lookUpSongs(ids);
	expect(mocks.post).toHaveBeenCalledTimes(2);
	expect(mocks.post.mock.calls[1][1]).toEqual({ body: { asset_ids: ['a500'] } });
});
