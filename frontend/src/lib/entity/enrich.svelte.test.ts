/* Linking a person, a site or a tag to a stash-box, and reading back what was kept.
 *
 * Two claims carry the file. The kind is an ARGUMENT (one module for all three subjects, which is
 * what makes one confirm screen possible), so every address here has to carry the kind it was
 * given rather than a spelling of its own. And reading what has already been agreed must never
 * fail: the stash-boxes are optional, every screen they touch works without them, and a page that
 * threw because an optional table could not be read would be a person's page that would not open.
 */

import { beforeEach, expect, it, vi } from 'vitest';

import { api, ApiError } from '$lib/api/client';
import {
	forgetLink,
	link,
	linksOf,
	madeByGlyph,
	madeBySaid,
	makerOf,
	problemFrom,
	chooserPicture,
	refresh,
	search,
	siteIconAddress
} from '$lib/entity/enrich.svelte';

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

beforeEach(() => {
	vi.resetAllMocks();
});

it('reads what has already been agreed, for each kind of subject', async () => {
	mocked.get.mockResolvedValue({ links: [{ box_id: 'box-1' }] });

	await linksOf('person', 'p-1');
	await linksOf('site', 's-1');
	await linksOf('tag', 't-1');

	expect(mocked.get.mock.calls.map((one) => one[0])).toEqual([
		'/stash-boxes/links/person/p-1',
		'/stash-boxes/links/site/s-1',
		'/stash-boxes/links/tag/t-1'
	]);
});

it('answers with nothing rather than throwing when that cannot be read', async () => {
	// The one refusal that is swallowed on purpose. A guest may read this and an install may have no
	// stash-boxes at all; either way the person's page still has to open.
	mocked.get.mockRejectedValue(new ApiError(403, 'forbidden'));

	expect(await linksOf('person', 'p-1')).toEqual([]);
});

it('hands the typed name over as a parameter rather than building the address', async () => {
	// A name with an ampersand in it is ordinary, and pasted into an address it ends the parameter.
	// Escaping is the client's job; what matters here is that this gives it the chance.
	mocked.get.mockResolvedValue({ answers: [] });

	await search('person', 'Salt & Pepper');

	expect(mocked.get).toHaveBeenCalledWith('/stash-boxes/search/person', {
		query: { term: 'Salt & Pepper' }
	});
});

it('says which row the chooser was opened on, so a record kept local can be refused', async () => {
	// The parameter the server route takes: a search with no `about` is a search about nothing, and
	// nothing is what the refusal would have to go on.
	mocked.get.mockResolvedValue({ answers: [] });

	await search('person', 'Salt & Pepper', 'p-1');

	expect(mocked.get).toHaveBeenCalledWith('/stash-boxes/search/person', {
		query: { term: 'Salt & Pepper', about: 'p-1' }
	});
});

it('leaves the row out when the chooser was opened on nothing', async () => {
	// A term somebody typed is not a fact about any row here, and claiming one would refuse a
	// search that is about nothing at all.
	mocked.get.mockResolvedValue({ answers: [] });

	await search('person', 'Salt & Pepper');

	expect(mocked.get).toHaveBeenCalledWith('/stash-boxes/search/person', {
		query: { term: 'Salt & Pepper' }
	});
});

it('agrees a link with a PUT, so saying it twice says the same thing', async () => {
	// One box knows one subject once. Repeating the agreement has to land on the same row rather
	// than making a second one.
	mocked.put.mockResolvedValue({ box_id: 'box-1' });

	await link('person', 'p-1', 'box-1', 'remote-9');

	expect(mocked.put).toHaveBeenCalledWith('/stash-boxes/links/person/p-1/box-1', {
		body: { remote_id: 'remote-9' }
	});
});

it('asks again by hand, and nothing here runs on a timer', async () => {
	mocked.post.mockResolvedValue({ box_id: 'box-1' });

	await refresh('tag', 't-1', 'box-2');

	expect(mocked.post).toHaveBeenCalledWith('/stash-boxes/links/tag/t-1/box-2/refresh', {});
});

it('forgets a link without touching the subject', async () => {
	// One request, to the links route. What was agreed to was written through the subject's own edit
	// route and stays there, so forgetting must not reach for it.
	mocked.del.mockResolvedValue(undefined);

	await forgetLink('site', 's-1', 'box-1');

	expect(mocked.del).toHaveBeenCalledWith('/stash-boxes/links/site/s-1/box-1');
	expect(mocked.put).not.toHaveBeenCalled();
	expect(mocked.post).not.toHaveBeenCalled();
});

it('shows what the server said, and one flat sentence for everything else', () => {
	expect(problemFrom(new ApiError(409, 'no', 'That box is switched off.'))).toBe(
		'That box is switched off.'
	);
	expect(problemFrom(new ApiError(500, 'no'))).toBe("That didn't work.");
	expect(problemFrom(null)).toBe("That didn't work.");
});

it('asks each kind of row who made it at the address that can answer', async () => {
	// The three a box can name ride along with their links; the two it cannot are asked at their
	// own entity's route. What this holds is that neither half borrows the other's address: a
	// shelf asked on the links route would be told it might have stash-box records.
	mocked.get.mockImplementation(async (path: string) =>
		path.startsWith('/stash-boxes/')
			? { links: [], made_by: { kind: 'sift', via: 'folder' } }
			: { kind: 'you', via: null }
	);

	expect(await makerOf('person', 'p-1')).toEqual({ kind: 'sift', via: 'folder' });
	expect(await makerOf('collection', 'c-1')).toEqual({ kind: 'you', via: null });
	expect(await makerOf('photo_set', 'ps-1')).toEqual({ kind: 'you', via: null });

	expect(mocked.get.mock.calls.map((one) => one[0])).toEqual([
		'/stash-boxes/links/person/p-1',
		'/collections/c-1/made-by',
		'/photo-sets/ps-1/made-by'
	]);
});

it('draws no maker line rather than breaking the page when that cannot be read', async () => {
	// The same swallow the links read makes, and for a stronger reason: this one is a line under a
	// name, and a header that throws takes the whole entity page with it.
	mocked.get.mockRejectedValue(new ApiError(500, 'no'));

	expect(await makerOf('collection', 'c-1')).toBeNull();
	expect(await makerOf('photo_set', 'ps-1')).toBeNull();
	expect(await makerOf('tag', 't-1')).toBeNull();
});

it('says the maker in the words the header draws, for every kind of maker', () => {
	// One sentence built in one place. The pass's phrase is the `enriched:` filter's own row with
	// its colon turned into a comma, so the line and the filter cannot come to mean different things.
	const made = (kind: string, via: string | null = null, box_name: string | null = null) => ({
		kind,
		via,
		act: null,
		box_id: null,
		box_name,
		box_slug: null
	});

	expect(madeBySaid(made('you'))).toBe('Created by you');
	expect(madeBySaid(made('another_user'))).toBe('Created by another user');
	expect(madeBySaid(made('somebody'))).toBe('Created by somebody');
	expect(madeBySaid(made('sift'))).toBe('Created by Sift');
	expect(madeBySaid(made('sift', 'folder'))).toBe('Created by Sift, from a folder name');
	expect(madeBySaid(made('stash_box', null, 'FansDB'))).toBe('Created by FansDB');
	// A row that is a noun phrase loses its capital after the words in front of it.
	expect(madeBySaid(made('sift', 'stash'))).toBe('Created by a stash-box');
	// A Stash library imported is not a stash-box's answer, and never reads as one.
	expect(madeBySaid(made('sift', 'stash_library'))).toBe('Created by Sift, from a Stash library');
});

it('says and draws the act that made a file Sift produced, and the general line where none is stored', () => {
	// A tag Sift put on a copy it made names the verb that made the copy, in History's word for it,
	// and wears the glyph that verb wears on a file's menu: Compress, and the editor's Modify.
	const produced = (act: string | null) => ({
		kind: 'sift',
		via: 'produced',
		act,
		box_id: null,
		box_name: null,
		box_slug: null
	});

	expect(madeBySaid(produced('compress'))).toBe('Created by Sift, from a file it compressed');
	expect(madeByGlyph(produced('compress'))).toBe('compress');
	expect(madeBySaid(produced('edit'))).toBe('Created by Sift, from a file it edited');
	expect(madeByGlyph(produced('edit'))).toBe('design_services');
	// A tag made before the act was written down, or an act this build has no word for.
	for (const act of [null, 'later']) {
		expect(madeBySaid(produced(act))).toBe('Created by Sift, from a file it made');
		expect(madeByGlyph(produced(act))).toBe('auto_fix_high');
	}
	// An act only ever qualifies the `produced` pass: any other pass keeps its own mark and words.
	expect(madeBySaid({ ...produced('compress'), via: 'folder' })).toBe(
		'Created by Sift, from a folder name'
	);
	expect(madeByGlyph({ ...produced('compress'), via: 'folder' })).toBe('folder_supervised');
});

it('draws a found site from the pack Sift ships, not from the stash-box', async () => {
	// The icon route. A box's picture costs one trip off this machine per row of the list, for a
	// mark that is already on this disk.
	expect(
		chooserPicture({ icon_slug: 'quillhouse', image_url: '/api/stash-boxes/b-1/picture?url=x' })
	).toEqual({
		src: siteIconAddress('quillhouse'),
		instead: '/api/stash-boxes/b-1/picture?url=x'
	});
});

it('keeps the box picture as the second address, so a missing logo changes nothing', async () => {
	expect(chooserPicture({ icon_slug: 'quillhouse', image_url: null })).toEqual({
		src: '/api/sites/icons/quillhouse',
		instead: null
	});
});

it('leaves an entry the pack has never heard of exactly as it was', async () => {
	// Every person and every tag in the chooser, and every site with no logo: the server sends no
	// slug for them at all.
	expect(chooserPicture({ image_url: '/api/stash-boxes/b-1/picture?url=x' })).toEqual({
		src: '/api/stash-boxes/b-1/picture?url=x',
		instead: null
	});
});
