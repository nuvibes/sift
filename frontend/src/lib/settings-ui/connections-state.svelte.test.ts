/* A Site's saved cookies, and how the screens that show them ask the server. */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { UNREACHABLE } from '$lib/shell/unreachable';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), del: vi.fn() }));

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: mocks.get, post: mocks.post, del: mocks.del }
}));

import { ApiError } from '$lib/api/client';
import { Connections, nameOf } from './connections-state.svelte';

/* A fixed moment, because the description counts DAYS from now. */
const NOW = Date.UTC(2026, 7, 22, 12, 0, 0);
const IN_DAYS = (days: number) => (NOW + days * 86400000) / 1000;

const A_ROW = {
	id: 'c-1',
	site: 'reddit',
	state: 'saved' as const,
	expires_at: IN_DAYS(7),
	expires_last: IN_DAYS(7),
	last_used_at: null,
	status: null,
	updated_at: null
};

/** What `/supported-sites` answers, cut down to what this file reads of it. */
const A_SITE = {
	key: 'reddit',
	name: 'Reddit',
	hosts: [],
	media: [],
	walls: [],
	bulk: false,
	tested: true,
	supported: true,
	names_creators: false,
	default_naming: '',
	name_words: [],
	cookies: 'partial' as const,
	cookies_why: 'Quarantined and private communities need cookies.',
	cookies_with_a_tool: null
};

function saved(over: Record<string, unknown> = {}) {
	return {
		id: 'c-1',
		cookies: 3,
		domains: ['reddit.com'],
		expires_at: IN_DAYS(7),
		expires_last: IN_DAYS(7),
		expired: false,
		...over
	};
}

beforeEach(() => {
	vi.clearAllMocks();
	vi.useFakeTimers();
	vi.setSystemTime(NOW);
	mocks.get.mockResolvedValue([]);
	mocks.post.mockResolvedValue(saved());
	mocks.del.mockResolvedValue({});
});

afterEach(() => {
	vi.useRealTimers();
});

it('reads the list and says nothing is wrong', async () => {
	const store = new Connections();
	mocks.get.mockImplementation((path: string) =>
		Promise.resolve(path === '/supported-sites' ? [A_SITE] : [A_ROW])
	);

	await store.load();

	expect(store.items).toEqual([A_ROW]);
	expect(store.sites).toEqual([A_SITE]);
	expect(store.problem).toBeNull();
	expect(store.loaded).toBe(true);
});

it('gives every supported Site a row, whether or not anything is saved for it', async () => {
	// The Site somebody opens the sheet FOR is by definition the one with nothing saved.
	const store = new Connections();
	const other = { ...A_SITE, key: 'coomer', name: 'Coomer' };
	mocks.get.mockImplementation((path: string) =>
		Promise.resolve(path === '/supported-sites' ? [A_SITE, other] : [A_ROW])
	);

	await store.load();

	expect(store.rows.map((one) => [one.name, one.row.state])).toEqual([
		['Reddit', 'saved'],
		['Coomer', 'none']
	]);
});

/* The saved row names its Site as the library files it, with capitals; the supported list keys
   it in lower case. */
it('draws a Site whose saved name differs from its key only in case once, as the saved row', async () => {
	const store = new Connections();
	const capitals = { ...A_SITE, key: 'tidewater', name: 'TideWater' };
	mocks.get.mockImplementation((path: string) =>
		Promise.resolve(
			path === '/supported-sites' ? [capitals, A_SITE] : [{ ...A_ROW, site: 'TideWater' }]
		)
	);

	await store.load();

	expect(store.rows.map((one) => [one.name, one.row.state, one.row.site])).toEqual([
		['TideWater', 'saved', 'tidewater'],
		['Reddit', 'none', 'reddit']
	]);
	expect(nameOf('TIDEWATER', store.sites)).toBe('TideWater');
});

it('hands back what the server read, and keeps none of it', async () => {
	const store = new Connections();
	mocks.post.mockResolvedValue({
		cookies: 14,
		domains: ['reddit.com'],
		expires_at: IN_DAYS(74),
		expired: false
	});

	const read = await store.preview('reddit', 'session=abc');

	expect(mocks.post).toHaveBeenCalledWith('/site-connections/preview', {
		body: { site: 'reddit', cookie: 'session=abc' }
	});
	expect(read?.cookies).toBe(14);
	// Nothing was saved, so nothing about the store moved.
	expect(store.items).toEqual([]);
});

it('says nothing at all when the read-back could not be taken', async () => {
	// A read-back that failed is not a refusal of the file.
	const store = new Connections();
	mocks.post.mockRejectedValue(new Error('the network went'));

	expect(await store.preview('reddit', 'session=abc')).toBeNull();
});

it('repeats what the Site said to a check, and re-reads the list afterwards', async () => {
	const store = new Connections();
	mocks.post.mockResolvedValue({ accepted: true, said: 'Reddit still accepts these.' });

	const answer = await store.check('c-1');

	expect(mocks.post).toHaveBeenCalledWith('/site-connections/c-1/check');
	expect(answer.said).toBe('Reddit still accepts these.');
	expect(mocks.get).toHaveBeenCalled();
	expect(store.busy).toBe(false);
});

it('turns a check that never reached the server into a sentence rather than a throw', async () => {
	const store = new Connections();
	mocks.post.mockRejectedValue(new Error('the network went'));

	const answer = await store.check('c-1');

	expect(answer.accepted).toBe(false);
	expect(answer.said).toBe(UNREACHABLE);
});

it('shows a Site under the name the supported list gives it, and its key when there is none', () => {
	// Two places deciding what to call one Site is how one Site comes to have two names.
	expect(nameOf('reddit', [A_SITE])).toBe('Reddit');
	expect(nameOf('nowhere', [A_SITE])).toBe('nowhere');
});

it('says the server could not be reached when the failure is not one it wrote', async () => {
	const store = new Connections();
	mocks.get.mockRejectedValue(new Error('the network went'));

	await store.load();

	expect(store.problem).toBe(UNREACHABLE);
	expect(store.loaded).toBe(true);
});

it('repeats what the server said about a refusal it did write', async () => {
	const store = new Connections();
	mocks.get.mockRejectedValue(new ApiError(403, 'You are not signed in.'));

	await store.load();

	expect(store.problem).toBe('You are not signed in.');
});

it('sends the cookies to the one address, and keeps nothing of what came back', async () => {
	/* Nothing is kept, and that is the assertion rather than an omission: the answer described a
	   file somebody had already been shown the read-back of, and holding it invited a second
	   sentence saying the same thing in different words. */
	const store = new Connections();

	const problem = await store.save('reddit', 'session=abc');

	expect(problem).toBeUndefined();
	expect(mocks.post).toHaveBeenCalledWith('/site-connections', {
		body: { site: 'reddit', cookie: 'session=abc' }
	});
	expect(store.busy).toBe(false);
});

it('SETS the refusal as well as answering with it, because the field is drawn from the store', async () => {
	// The field under the box reads `saveError`, so a refusal only answered would go to a caller
	// with nowhere to put it, and the screen would do nothing at all.
	const store = new Connections();
	mocks.post.mockRejectedValue(
		new ApiError(409, 'That request was not valid.', 'Sign in with your password first.')
	);

	const problem = await store.save('reddit', 'session=abc');

	expect(problem).toBe('Sign in with your password first.');
	expect(store.saveError).toBe('Sign in with your password first.');
	expect(store.busy).toBe(false);
});

it('falls back to the flat one-liner when the refusal carries no sentence of its own', async () => {
	const store = new Connections();
	mocks.post.mockRejectedValue(new ApiError(400, 'That request was not valid.'));

	expect(await store.save('reddit', 'x')).toBe('That request was not valid.');
});

it('clears the last refusal when a save is tried again', async () => {
	const store = new Connections();
	mocks.post.mockRejectedValueOnce(new ApiError(400, 'That request was not valid.'));
	await store.save('reddit', 'x');

	await store.save('reddit', 'session=abc');

	expect(store.saveError).toBeUndefined();
});

it('reads the list back after a Site is forgotten', async () => {
	const store = new Connections();
	mocks.get.mockResolvedValue([A_ROW]);
	await store.load();
	mocks.get.mockResolvedValue([]);

	await store.remove(A_ROW);

	expect(mocks.del).toHaveBeenCalledWith('/site-connections/c-1');
	expect(store.items).toEqual([]);
	expect(store.busy).toBe(false);
});

it('never uses the vocabulary of accounts, anywhere in the module it is testing', () => {
	/* Cookies, not accounts, with something behind it: nobody hands Sift an account here, and a
	   rule that lives only in people's heads is a rule that drifts back. */
	const source = readFileSync(
		resolve(dirname(fileURLToPath(import.meta.url)), 'connections-state.svelte.ts'),
		'utf8'
	);

	expect(source).not.toMatch(/\blogins?\b/i);
});
