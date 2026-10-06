/* The actions a bar and a menu share: what they run over, and what they leave alone.
 *
 * The API is stubbed. What is under test is the decision about WHICH files an answer applies to,
 * which is the part these own: a rating that reaches the first of a selection and stops is the
 * failure this file exists to catch, and it looks exactly like a working press.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { wordsOf } from '$lib/components/common/toast-pieces';

import { api } from '$lib/api/client';
import { collections } from '$lib/library/collections.svelte';
import { Selection } from '$lib/components/common';
import { people } from '$lib/people/people.svelte';
import { photoSets } from '$lib/library/photo-sets.svelte';
import { tags } from '$lib/entity/tags.svelte';
import { toasts } from '$lib/shell/toasts.svelte';
import { AssetActions, type Actionable } from './actions.svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
	ApiError: class extends Error {
		detail: string | null = null;
	}
}));

vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: vi.fn() } }));

/* The four stores that own the writes these verbs make.
 *
 * Stubbed at the STORE rather than at `api`, because the question this file asks about them is the
 * store's boundary: was the whole selection handed over in one call, or was the store called once
 * per file? How a store then turns one call into one request (and splits a selection of four
 * thousand into chunks of five hundred) is `bulk.test.ts`'s question and is answered there.
 */
const done = { changed: 0, skipped: 0, reason: null, reason_many: null, vault_locked: false };
vi.mock('$lib/entity/tags.svelte', () => ({ tags: { assign: vi.fn() } }));
vi.mock('$lib/people/people.svelte', () => ({ people: { assign: vi.fn(), filedUnder: vi.fn() } }));
vi.mock('$lib/library/collections.svelte', () => ({ collections: { add: vi.fn() } }));
vi.mock('$lib/library/photo-sets.svelte', () => ({ photoSets: { add: vi.fn() } }));

const mocked = vi.mocked(api);
const said = vi.mocked(toasts.show);
const store = {
	tags: vi.mocked(tags),
	people: vi.mocked(people),
	collections: vi.mocked(collections),
	photoSets: vi.mocked(photoSets)
};

/** What the toast said, as the caller handed it over: the opening words and the thing they
    name, as its piece. */
function lastToast(): { message: string; href?: string | null; label?: string } {
	const words = said.mock.calls.at(-1)?.[0] ?? '';
	if (typeof words === 'string') return { message: words, href: undefined, label: undefined };
	const named = words.find((one) => typeof one !== 'string');
	const lead = words.filter((one) => typeof one === 'string').join('');
	return { message: lead.trimEnd(), href: named?.href, label: named?.text };
}

function file(id: string, overrides: Partial<Actionable> = {}): Actionable {
	return {
		id,
		media_type: 'video',
		favorite: false,
		rating: null,
		concealed: false,
		original_filename: `${id}.mp4`,
		...overrides
	};
}

/** Three files and an `AssetActions` over them, plus what the screen recorded. */
function around(items: Actionable[]) {
	const held = new Map(items.map((one) => [one.id, { ...one }]));
	const written: { id: string; rating?: number | null }[] = [];
	const actions = new AssetActions<Actionable>({
		lookup: (id) => held.get(id),
		selection: new Selection(),
		setState: (id, state) => {
			const one = held.get(id);
			if (one) held.set(id, { ...one, ...state });
			written.push({ id, rating: state.rating });
		}
	});
	return { actions, held, written };
}

beforeEach(() => {
	vi.clearAllMocks();
	store.tags.assign.mockResolvedValue({ ...done, changed: 1 });
	store.people.assign.mockResolvedValue({ ...done, changed: 1 });
	store.people.filedUnder.mockResolvedValue({ ...done, changed: 1 });
	store.collections.add.mockResolvedValue({ ...done, changed: 1 });
	store.photoSets.add.mockResolvedValue({ ...done, changed: 1 });
});

/*
 * ONE CALL FOR THE WHOLE SELECTION, per destination, never one per file.
 *
 * The failure this guards is invisible on a small selection and ruinous on a real one: a loop
 * awaiting a request per file turns "add these four hundred to a collection" into four hundred
 * round trips, each one announcing that the library changed. It is also the shape a well-meaning
 * edit reaches for, because a per-file call is the easier thing to write.
 */
describe('every verb that puts files on something', () => {
	it('hands the whole selection over in one call, per thing picked', async () => {
		const { actions } = around([file('a'), file('b'), file('c')]);
		const ids = ['a', 'b', 'c'];

		await actions.tag(ids, [{ id: 't1', name: 'Beach' }]);
		await actions.assign(ids, [{ id: 'p1', name: 'Ada' }]);
		await actions.site(ids, [{ id: 's1', name: 'Discord' }]);
		await actions.collect(ids, [{ id: 'c1', name: 'Best of' }]);
		await actions.photoSet(ids, [{ id: 'ps1', name: 'beach days' }]);

		expect(store.tags.assign.mock.calls).toEqual([[ids, ['t1']]]);
		expect(store.people.assign.mock.calls).toEqual([[ids, ['p1']]]);
		expect(store.people.filedUnder.mock.calls).toEqual([[ids, ['s1']]]);
		expect(store.collections.add.mock.calls).toEqual([['c1', ids]]);
		expect(store.photoSets.add.mock.calls).toEqual([['ps1', ids]]);
	});

	it('sends one call per destination when several were ticked, still with every id', async () => {
		const { actions } = around([file('a'), file('b')]);

		await actions.collect(
			['a', 'b'],
			[
				{ id: 'c1', name: 'Best of' },
				{ id: 'c2', name: 'Keep' }
			]
		);

		// Two calls because the endpoint takes ONE collection and the items going into it. What
		// matters is that neither of them is per file.
		expect(store.collections.add.mock.calls).toEqual([
			['c1', ['a', 'b']],
			['c2', ['a', 'b']]
		]);
	});
});

/*
 * The sentence afterwards names what it touched, and the name is somewhere to go.
 *
 * "3 files went on 1 tag" would be one wording doing five jobs: it counts the things instead of
 * naming them, and the one part worth a click is not there to click.
 */
describe('what a verb says when it lands', () => {
	it('names a photo set, and links to it', async () => {
		const { actions } = around([file('a')]);
		await actions.photoSet(['a'], [{ id: 'ps1', name: 'beach days' }]);
		expect(lastToast()).toEqual({
			message: 'The file was added to the Photo Set',
			href: '/photo-sets/ps1',
			label: 'beach days'
		});
	});

	it('counts the files and names the Site', async () => {
		const { actions } = around([file('a'), file('b')]);
		await actions.site(['a', 'b'], [{ id: 's1', name: 'Discord' }]);
		expect(lastToast()).toEqual({
			message: '2 files were added to the Site',
			href: '/sites/s1',
			label: 'Discord'
		});
	});

	it('names a person bare, with no word in front of the name', async () => {
		const { actions } = around([file('a'), file('b'), file('c'), file('d')]);
		await actions.assign(['a', 'b', 'c', 'd'], [{ id: 'p1', name: 'Ada' }]);
		expect(lastToast()).toEqual({
			message: '4 files were added to',
			href: '/people/p1',
			label: 'Ada'
		});
	});

	it('says a file was tagged, not that it went on a tag', async () => {
		const { actions } = around([file('a')]);
		await actions.tag(['a'], [{ id: 't1', name: 'Beach' }]);
		expect(lastToast()).toEqual({
			message: 'The file was tagged',
			href: '/tags/t1',
			label: 'Beach'
		});
	});

	it('says files went INTO a collection, which is a place rather than a label', async () => {
		const { actions } = around([file('a'), file('b'), file('c')]);
		await actions.collect(['a', 'b', 'c'], [{ id: 'c1', name: 'Best of' }]);
		expect(lastToast()).toEqual({
			message: '3 files were added to the collection',
			href: '/collections/c1',
			label: 'Best of'
		});
	});

	it('counts the destinations and offers no link when several were picked', async () => {
		const { actions } = around([file('a'), file('b'), file('c')]);

		await actions.photoSet(
			['a', 'b', 'c'],
			[
				{ id: 'ps1', name: 'beach days' },
				{ id: 'ps2', name: 'Summer' }
			]
		);

		// No link, because there is no page for "2 photo sets": a link has to go somewhere.
		expect(lastToast()).toEqual({
			message: '3 files were added to 2 Photo Sets',
			href: undefined,
			label: undefined
		});
	});

	it('counts what actually landed, not what was asked for', async () => {
		store.tags.assign.mockResolvedValue({ ...done, changed: 2, skipped: 1 });
		const { actions } = around([file('a'), file('b'), file('c')]);

		await actions.tag(['a', 'b', 'c'], [{ id: 't1', name: 'Beach' }]);

		// The success line and the one about what was left out have to agree, or the app is
		// contradicting itself in two lines and somebody is left counting.
		expect(wordsOf(said.mock.calls[0][0])).toBe('2 files were tagged Beach');
	});
});

/*
 * A WRITE FROM THE MENU'S PICKER: it answers with what the server said, and it can be told to leave
 * the selection alone.
 *
 * The flyout stays up after a right press, and the next pick is over the SAME files. Clearing the
 * selection when the write finishes is right for the bar's sheets, which are done, and wrong here:
 * the flyout would be left standing over a selection that was gone. And a write that answered
 * nothing would leave the picker's tick moved after a refusal.
 */
describe('a write made from the picker', () => {
	const writes = [
		[
			'tag',
			(a: AssetActions<Actionable>, keep: boolean) =>
				a.tag(['a', 'b'], [{ id: 't1', name: 'Beach' }], { keepSelection: keep })
		],
		[
			'assign',
			(a: AssetActions<Actionable>, keep: boolean) =>
				a.assign(['a', 'b'], [{ id: 'p1', name: 'Ada' }], { keepSelection: keep })
		],
		[
			'site',
			(a: AssetActions<Actionable>, keep: boolean) =>
				a.site(['a', 'b'], [{ id: 's1', name: 'Discord' }], { keepSelection: keep })
		],
		[
			'collect',
			(a: AssetActions<Actionable>, keep: boolean) =>
				a.collect(['a', 'b'], [{ id: 'c1', name: 'Best of' }], { keepSelection: keep })
		],
		[
			'photoSet',
			(a: AssetActions<Actionable>, keep: boolean) =>
				a.photoSet(['a', 'b'], [{ id: 'ps1', name: 'beach days' }], { keepSelection: keep })
		]
	] as const;

	function held(): { actions: AssetActions<Actionable>; selection: Selection } {
		const selection = new Selection();
		selection.pick('a', { shiftKey: false }, ['a', 'b']);
		selection.pick('b', { shiftKey: false }, ['a', 'b']);
		const items = new Map([file('a'), file('b')].map((one) => [one.id, one]));
		const actions = new AssetActions<Actionable>({ lookup: (id) => items.get(id), selection });
		return { actions, selection };
	}

	it.each(writes)('%s keeps the selection when the picker asks', async (_, write) => {
		const { actions, selection } = held();
		await write(actions, true);
		expect(selection.count).toBe(2);
	});

	it.each(writes)('%s still clears it for everybody else', async (_, write) => {
		const { actions, selection } = held();
		await write(actions, false);
		expect(selection.count).toBe(0);
	});

	it("answers with the server's own count of what it left out", async () => {
		store.tags.assign.mockResolvedValue({ ...done, changed: 0, skipped: 2 });
		const { actions } = held();

		const answer = await actions.tag(['a', 'b'], [{ id: 't1', name: 'Beach' }], {
			keepSelection: true
		});

		expect(answer?.skipped).toBe(2);
	});

	it('answers nothing for a write that failed outright', async () => {
		store.collections.add.mockRejectedValue(new Error('refused'));
		const { actions } = held();

		expect(await actions.collect(['a', 'b'], [{ id: 'c1', name: 'Best of' }])).toBeNull();
	});
});

/*
 * THE FOUR OPINIONS, EACH IN ONE REQUEST.
 *
 * The heart, the stars, the pin and the vault go as one request for the selection, not one PER FILE
 * awaited one after another: a selection of a hundred and thirty-four would be a hundred and
 * thirty-four round trips, and a failure part-way would leave it half written with nothing on
 * screen saying which half. They answer `BulkWriteDone`, like every other write over a selection,
 * and what is asserted here is the address, the body and what a partial answer does to the rows,
 * never a count of requests for its own sake.
 */
describe('a rating set from the bar', () => {
	it('reaches every file picked in ONE request, and only those', async () => {
		mocked.post.mockResolvedValue({ ...done, changed: 2 });
		const { actions } = around([file('a'), file('b'), file('c')]);

		await actions.rate(['a', 'b'], 4);

		expect(mocked.post.mock.calls).toEqual([
			['/assets/rating', { body: { asset_ids: ['a', 'b'], rating: 4 } }]
		]);
		// The third file was never named. A bulk action that reaches something outside the selection
		// is worse than one that misses part of it: nothing on screen said it was included.
		expect(mocked.put).not.toHaveBeenCalled();
	});

	it('sends the value that was chosen, not a nudge from what each file had', async () => {
		mocked.post.mockResolvedValue({ ...done, changed: 2 });
		const { actions } = around([file('a', { rating: 5 }), file('b', { rating: 1 })]);

		await actions.rate(['a', 'b'], 2);

		expect(mocked.post.mock.calls[0][1]).toEqual({
			body: { asset_ids: ['a', 'b'], rating: 2 }
		});
	});

	it('puts every row back when the server could not do all of it', async () => {
		/* A partial answer says HOW MANY were left out and never WHICH, by design, so the rows
		 * this can be sure of are the ones it started with, and the wall re-reads. A half-rated
		 * selection with nothing on screen saying so is the failure this file exists to catch. */
		mocked.post.mockResolvedValue({ ...done, changed: 1, skipped: 1, reason: 'nope' });
		const { actions, held } = around([file('a', { rating: 1 }), file('b', { rating: 1 })]);

		await actions.rate(['a', 'b'], 3);

		expect(held.get('a')?.rating).toBe(1);
		expect(held.get('b')?.rating).toBe(1);
		expect(said.mock.calls.at(-1)?.[0]).toContain("couldn't be included");
	});
});

describe('the heart from the bar', () => {
	it('hearts the whole selection in ONE request, to one target state', async () => {
		/* One state for the set: if any of them is not yet a favorite, the press makes them all
		 * favorites. Toggling each into whatever it was not does nothing useful on the common
		 * press. */
		mocked.post.mockResolvedValue({ ...done, changed: 2 });
		const { actions, held } = around([file('a', { favorite: true }), file('b')]);

		await actions.favorite(['a', 'b']);

		expect(mocked.post.mock.calls).toEqual([
			['/assets/favorite', { body: { asset_ids: ['a', 'b'], favorite: true } }]
		]);
		expect(held.get('b')?.favorite).toBe(true);
	});

	it('puts every heart back when the server could not do all of it', async () => {
		mocked.post.mockResolvedValue({ ...done, changed: 1, skipped: 1, reason: 'nope' });
		const { actions, held } = around([file('a'), file('b')]);

		await actions.favorite(['a', 'b']);

		expect(held.get('a')?.favorite).toBe(false);
		expect(held.get('b')?.favorite).toBe(false);
		expect(said.mock.calls.at(-1)?.[0]).toContain("couldn't be included");
	});
});

describe('the pin from the bar', () => {
	it('pins the whole selection in ONE request', async () => {
		mocked.post.mockResolvedValue({ ...done, changed: 2 });
		const { actions } = around([file('a'), file('b')]);

		await actions.pin(['a', 'b'], true);

		expect(mocked.post.mock.calls).toEqual([
			['/assets/pin', { body: { asset_ids: ['a', 'b'], pinned: true } }]
		]);
	});

	it('puts every row back when the server could not do all of it', async () => {
		// All or nothing on the screen, exactly as the entity walls are: a wall showing three of
		// five pinned after one press is a wall nobody can reason about.
		mocked.post.mockResolvedValue({ ...done, changed: 1, skipped: 1, reason: 'nope' });
		const { actions, held } = around([file('a', { pinned: true }), file('b')]);

		await actions.pin(['a', 'b'], false);

		expect(held.get('a')?.pinned).toBe(true);
		expect(held.get('b')?.pinned).toBe(false);
	});
});

describe('hiding from the bar', () => {
	it('hides the whole selection in ONE request', async () => {
		mocked.post.mockResolvedValue({ ...done, changed: 2 });
		const { actions } = around([file('a'), file('b')]);

		await actions.hide(['a', 'b'], true);

		expect(mocked.post.mock.calls).toEqual([
			['/assets/vault', { body: { asset_ids: ['a', 'b'], vault: true } }]
		]);
		expect(mocked.put).not.toHaveBeenCalled();
	});
});

describe('a move from the bar', () => {
	it('sends every file to the one destination that was chosen, in one request', async () => {
		mocked.post.mockResolvedValue({ changed: 2, skipped: 0 });
		const { actions } = around([file('a'), file('b')]);

		await actions.move(['a', 'b'], 'folder-1');

		expect(mocked.post.mock.calls).toEqual([
			['/assets/move', { body: { asset_ids: ['a', 'b'], folder_id: 'folder-1' } }]
		]);
	});

	it('does nothing at all without a destination', async () => {
		const { actions } = around([file('a')]);
		await actions.move(['a'], '');
		expect(mocked.post).not.toHaveBeenCalled();
	});
});

describe('removing files', () => {
	function wall(items: Actionable[]) {
		const forget = vi.fn();
		const refresh = vi.fn();
		const actions = new AssetActions<Actionable>({
			lookup: (id) => items.find((one) => one.id === id),
			selection: new Selection(),
			forget,
			refresh
		});
		return { actions, forget, refresh };
	}

	it('drops the rows on the press, before the server answers', async () => {
		let answer: (value: unknown) => void = () => {};
		mocked.post.mockReturnValueOnce(new Promise((resolve) => (answer = resolve)) as never);
		const { actions, forget, refresh } = wall([file('a'), file('b')]);
		const removing = actions.remove(['a', 'b'], 'sift');
		expect(forget.mock.calls.map((call) => call[0])).toEqual(['a', 'b']);
		answer({ ...done, changed: 2 });
		await removing;
		expect(refresh).not.toHaveBeenCalled();
	});

	it('reads the rows back when the server refuses the whole set, or part of it', async () => {
		mocked.post.mockRejectedValueOnce(new Error('down'));
		const whole = wall([file('a')]);
		await whole.actions.remove(['a'], 'sift');
		expect(whole.refresh).toHaveBeenCalledTimes(1);

		mocked.post.mockResolvedValueOnce({ ...done, changed: 1, skipped: 1 } as never);
		const part = wall([file('a'), file('b')]);
		await part.actions.remove(['a', 'b'], 'sift');
		expect(part.refresh).toHaveBeenCalledTimes(1);
	});
});
