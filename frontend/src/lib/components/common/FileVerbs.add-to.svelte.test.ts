/*
 * Every door onto Add to opens one menu, and every list in it acts on everything the door is over.
 *
 * The selection bar's Add to, a file's own and the right-click menu's are one menu only while the
 * host hands every door the same rows with the same lists. A bar handed the rows without the lists
 * draws five rows that open nothing out, beside two doors whose rows open into the list.
 *
 * Mounted for real, because the fault would live in what the host hands each door, which no reading of
 * the declaration alone can see. Asserted, per door: the same six rows in the same order with the
 * same words and glyphs, the same list object behind each of the five places, and a pick through
 * the bar's list writes to every file picked through the same store call a pick on one file makes,
 * so the announce and the History line are the one act's. Nothing here names anything real.
 */
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { collections } from '$lib/library/collections.svelte';
import { people } from '$lib/people/people.svelte';
import { photoSets } from '$lib/library/photo-sets.svelte';
import { songs } from '$lib/entity/songs.svelte';
import { session, type Viewer } from '$lib/shell/session.svelte';
import { tags } from '$lib/entity/tags.svelte';
import { toasts } from '$lib/shell/toasts.svelte';
import type { Actionable } from '$lib/grid/actions.svelte';
import Probe from './FileVerbsDoorsProbe.test.svelte';
import { Selection } from './selection.svelte';
import { barShape, type Verb } from './verbs';

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async () => ({})),
		post: vi.fn(async () => ({})),
		put: vi.fn(async () => ({})),
		del: vi.fn(async () => ({}))
	},
	ApiError: class extends Error {
		status = 0;
		detail: string | null = null;
	},
	setCsrfToken: vi.fn()
}));

const WROTE = { changed: 3, skipped: 0, reason: null, reason_many: null, vault_locked: false };

function file(id: string): Actionable {
	return {
		id,
		media_type: 'video',
		favorite: false,
		rating: null,
		concealed: false,
		original_filename: `${id}.mp4`
	};
}

const FILES = [file('f-otter'), file('f-heron'), file('f-lark')];
const PICKED = FILES.map((one) => one.id);
const ONE = 'f-otter';

let instance: ReturnType<typeof mount> | null = null;

interface Doors {
	bar: (ids: string[]) => Verb[];
	menu: (ids: string[], subjectId?: string) => Verb[];
}

function doors(): Doors {
	let handed: Doors | null = null;
	const selection = new Selection();
	instance = mount(Probe, {
		target: document.body,
		props: { items: FILES, selection, ondoors: (given: Doors) => (handed = given) }
	});
	flushSync();
	if (handed === null) throw new Error('the host drew nothing');
	return handed;
}

/** The Add to the selection bar names, as `barShape` splits it for the bar to draw. */
function barAddTo(all: Doors): Verb | undefined {
	return barShape(all.bar(PICKED)).named.find((verb) => verb.id === 'add');
}

/** The Add to a file's own screen and the right-click menu draw, off `menu`. */
function menuAddTo(all: Doors, ids: string[], subject?: string): Verb | undefined {
	return all.menu(ids, subject).find((verb) => verb.id === 'add');
}

/** A row as a person reads it: its words, its glyph, and whether it opens out into a list. */
function readAs(verb: Verb) {
	return { id: verb.id, label: verb.label, icon: verb.icon, list: verb.pick !== undefined };
}

beforeEach(() => {
	session.viewer = { role: 'admin', can_save_to_device: true } as Viewer;
	vi.spyOn(toasts, 'show').mockImplementation(() => 0 as never);
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
	session.viewer = undefined;
	vi.restoreAllMocks();
});

describe('the three doors onto Add to open one menu', () => {
	it('draws the same rows, in the same order, with the same lists, from every door', () => {
		const all = doors();
		const bar = barAddTo(all);
		const popout = menuAddTo(all, [ONE], ONE);
		const rightClick = menuAddTo(all, PICKED, ONE);
		expect(bar, 'the selection bar names Add to').toBeDefined();
		const rows = bar?.children?.map(readAs);
		expect(rows?.map((row) => row.label)).toEqual([
			'Person',
			'Site',
			'Collection',
			'Photo Set',
			'Tag',
			'Song',
			'Favorites'
		]);
		// Every place opens out into its list on the bar, never as a row opening a sheet.
		expect(rows?.filter((row) => row.list).map((row) => row.id)).toEqual([
			'assign',
			'site',
			'collect',
			'photo_set',
			'tag',
			'song'
		]);
		expect(popout?.children?.map(readAs)).toEqual(rows);
		expect(rightClick?.children?.map(readAs)).toEqual(rows);
	});

	it('hands every door the same list behind each place, and no second press beside it', () => {
		const all = doors();
		const bar = barAddTo(all)?.children ?? [];
		const menu = menuAddTo(all, [ONE], ONE)?.children ?? [];
		for (const row of bar.filter((one) => one.pick)) {
			const twin = menu.find((one) => one.id === row.id);
			expect(twin?.pick, `${row.label} is one list on both doors`).toBe(row.pick);
			// A press beside the list is the road back to a sheet of the bar's own.
			expect(row.run, `${row.label} carries no second way in`).toBeUndefined();
		}
	});
});

/*
 * One pick through the BAR's list, over three files, per place: every file handed to the one store
 * call the same pick on ONE file makes (through a file's own Add to), so the write, its announce
 * and its History line are the single-file act's, over the set.
 */
describe('a pick from the selection bar acts on the whole selection', () => {
	const CHOICE = { id: 'thing-1', name: 'Marram' };

	const PLACES: { id: string; label: string; wrote: () => ReturnType<typeof vi.fn> }[] = [
		{
			id: 'collect',
			label: 'Collection',
			wrote: () => vi.spyOn(collections, 'add').mockResolvedValue(WROTE) as never
		},
		{
			id: 'photo_set',
			label: 'Photo Set',
			wrote: () => vi.spyOn(photoSets, 'add').mockResolvedValue(WROTE) as never
		},
		{
			id: 'assign',
			label: 'Person',
			wrote: () => vi.spyOn(people, 'assign').mockResolvedValue(WROTE) as never
		},
		{
			id: 'tag',
			label: 'Tag',
			wrote: () => vi.spyOn(tags, 'assign').mockResolvedValue(WROTE) as never
		},
		{
			id: 'site',
			label: 'Site',
			wrote: () => vi.spyOn(people, 'filedUnder').mockResolvedValue(WROTE) as never
		},
		{
			id: 'song',
			label: 'Song',
			wrote: () => vi.spyOn(songs, 'add').mockResolvedValue(WROTE) as never
		}
	];

	/** The file ids a store call was handed, wherever the store takes them. */
	const idsIn = (call: unknown[]): unknown =>
		call.find((arg) => Array.isArray(arg) && arg.includes(ONE));

	for (const place of PLACES) {
		it(`writes ${place.label} onto every picked file, as the one-file pick does`, async () => {
			const all = doors();
			const store = place.wrote();
			const fromBar = barAddTo(all)?.children?.find((one) => one.id === place.id);
			const fromPopout = menuAddTo(all, [ONE], ONE)?.children?.find((one) => one.id === place.id);
			expect(fromBar?.pick, `${place.label} opens a list on the bar`).toBeDefined();

			expect(await fromPopout?.pick?.pick([ONE], CHOICE)).toBe('landed');
			expect(await fromBar?.pick?.pick(PICKED, CHOICE)).toBe('landed');

			expect(store).toHaveBeenCalledTimes(2);
			expect(idsIn(store.mock.calls[0])).toEqual([ONE]);
			expect(idsIn(store.mock.calls[1])).toEqual(PICKED);
			// Said both times, by the one sentence table (`AssetActions.#landed`).
			expect(vi.mocked(toasts.show)).toHaveBeenCalledTimes(2);
		});
	}
});
