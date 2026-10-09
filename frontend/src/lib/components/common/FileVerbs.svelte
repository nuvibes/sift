<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'FileVerbs',
		category: 'composition',
		role: 'the host behind every surface that shows files: the verbs and the sheets that ask before they write',
		basis: 'own'
	} satisfies DesignEntry;
</script>

<script lang="ts">
	import { counted as grouped } from '$lib/entity/entity-counts';
	import type { components } from '$lib/api/schema';
	/* NOT ON THE GALLERY: this draws no element of its own. It owns the actions and the sheets that
	ask before they write; VerbButtons and VerbMenuItems, which it feeds, are there. */
	/* WHY NOT BITS-UI: not a control. This is the wiring behind the verbs (the actions and the sheets that ask
	before they write) and it renders no interface of its own. */
	/* Everything that can be done to a file, wired once: the list is `$lib/grid/verbs`'s, the actions,
	 * sheets and handlers are here, and children get the finished verbs. */
	import { goto } from '$app/navigation';
	import { likeWall } from '$lib/search/like';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { vaultPrompt } from '$lib/shell/vault.svelte';
	import { enrichFiles, lookUpSongs } from '$lib/entity/enrich-many.svelte';
	import { loadRunNowGroups, runNow, runNowGroups } from '$lib/jobs/run-now.svelte';
	import {
		enrichBoxes,
		enrichmentOf,
		lastEnriched,
		loadEnrichBoxes,
		loadSongLookup,
		refusedOutside,
		songLookup,
		sayKeptLocal,
		setKeptLocal
	} from '$lib/entity/enrichment.svelte';
	import FileMatches from '$lib/components/organize/FileMatches.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { keptFromSwaps, setKeptFromSwaps } from '$lib/components/swap/swap';
	import { movable } from '$lib/library/movable.svelte';
	import { tags as tagStore } from '$lib/entity/tags.svelte';
	import { collections as collectionStore } from '$lib/library/collections.svelte';
	import { photoSets as photoSetStore } from '$lib/library/photo-sets.svelte';
	import { songs as songStore } from '$lib/entity/songs.svelte';
	import { people as peopleStore, sites as siteStore } from '$lib/people/people.svelte';
	import {
		AssetActions,
		type Actionable,
		type DeleteMode,
		type PutOnHow,
		type Surroundings
	} from '$lib/grid/actions.svelte';
	import {
		fileVerbs,
		menuVerbs,
		type FileVerbHandlers,
		type Verb,
		type VerbId
	} from '$lib/grid/verbs';
	import {
		landedOf,
		type OnAlready,
		type PickChoice,
		type PickLanded,
		type PickPage
	} from '$lib/components/common/verbs';
	import { announceSkipped, type BulkWriteDone } from '$lib/library/bulk';
	import { libraryChanges } from '$lib/library/changes.svelte';
	import { pickRow, type Drawable } from '$lib/entity/entity-picture';
	import { personRow } from '$lib/people/person-row';
	import { siteRow } from '$lib/entity/site-row';
	import { saveActionIcon, saveActionLabel } from '$lib/capture/copy-out';
	import type { Choice } from '$lib/components/common';
	import MoveDialog from '$lib/components/MoveDialog.svelte';
	import BatchRename from '$lib/organize/BatchRename.svelte';
	import CompressDialog from '$lib/components/CompressDialog.svelte';
	import EditDialog from '$lib/components/EditDialog.svelte';
	import { api } from '$lib/api/client';
	import type { EditableAsset } from '$lib/edit/edit.svelte';
	import DeleteDialog from '$lib/components/DeleteDialog.svelte';
	import ShareDialog from '$lib/components/ShareDialog.svelte';
	import VisibilityDialog from '$lib/components/VisibilityDialog.svelte';
	import type { ShareTarget } from '$lib/library/sharing';
	import type { Snippet } from 'svelte';

	interface Props<Item extends Actionable> {
		/** Everything this surface is showing, so a verb can look one up without asking the server. */
		items: readonly Item[];
		/** How this surface holds a file, and what it can do about one. See `Surroundings`. */
		around: Omit<Surroundings<Item>, 'lookup'> & { lookup?: (id: string) => Item | undefined };
		/** Whether this surface is the one that shows hidden files. Flips what Hide means. */
		showingHidden?: boolean;
		/** Whether files can be pinned here: only on a curated wall. */
		pinnable?: boolean;
		/** Drawn with the verbs: `bar` and `menu` (which words the heart for the file it was opened on),
		 * and `named` for a surface drawing one verb as its own control. */
		children: Snippet<
			[
				{
					bar: (ids: string[]) => Verb[];
					menu: (ids: string[], subjectId?: string) => Verb[];
					named: (verb: VerbId, ids: string[], subjectId?: string) => Verb | undefined;
					actions: AssetActions<Item>;
				}
			]
		>;
	}

	let {
		items,
		around,
		showingHidden = false,
		pinnable = false,
		children
	}: Props<Actionable> = $props();

	const lookup = (id: string) => around.lookup?.(id) ?? items.find((one) => one.id === id);
	/* Built once, every member read through a function so it follows the surface. */
	const actions = new AssetActions<Actionable>({
		lookup,
		get selection() {
			return around.selection;
		},
		setState: (id, state, keep) => around.setState?.(id, state, keep),
		forget: (id) => around.forget?.(id),
		refresh: () => around.refresh?.(),
		stillBelongs: (item) => around.stillBelongs?.(item) ?? true,
		showingHidden: () => around.showingHidden?.() ?? false
	});

	/* Whether Move can be offered (somewhere to move to), asked once, by an admin. */
	$effect(() => {
		if (session.isAdmin) void movable.ensure();
	});

	/* Move and Compress, each answered in a sheet first. */
	let acting = $state<string[]>([]);
	let moveOpen = $state(false);
	let renameOpen = $state(false);
	let renaming = $state<string[]>([]);
	let compressOpen = $state(false);
	let editOpen = $state(false);
	/* Which way the editor opens: Trim or a GIF. */
	let editMode = $state<'trim' | 'gif'>('trim');
	/* The file the editor is open on, fetched, since a tile lacks what the editor needs. */
	let editing = $state<EditableAsset | null>(null);
	let deleting = $state<string[]>([]);
	let deleteOpen = $state(false);
	let sharing = $state<ShareTarget[]>([]);
	let shareOpen = $state(false);
	/* The visibility report, on one file only. */
	let reaching = $state<ShareTarget | null>(null);
	let reachOpen = $state(false);

	/* Every list's rows by `pickRow`, so a row wears its card's picture. */
	const tagRow = (tag: Drawable) => pickRow('tag', tag);
	const collectionRow = (one: Drawable) => pickRow('collection', one);
	const photoSetRow = (set: Drawable) => pickRow('photo_set', set);
	const songRow = (one: Drawable) => pickRow('song', one);

	/* The five lists a file can be put on, each a row under Add to on every door, typed by the
	 * handler list. What the files are already on is asked once for all five
	 * (`/assets/memberships`),
	 * held per id set and dropped on any write. */
	type MembershipsAnswer = components['schemas']['Memberships'];
	type MembershipList = components['schemas']['Membership'];

	/** The most ids one question may name: the server's cap, so a big selection is split. */
	const MOST_PER_ASK = 500;

	const NOTHING_ON: MembershipsAnswer = {
		people: { all: [], some: [] },
		sites: { all: [], some: [] },
		collections: { all: [], some: [] },
		photo_sets: { all: [], some: [] },
		tags: { all: [], some: [] },
		songs: { all: [], some: [] },
		favorite: 'none'
	};

	/** The ids the held answer is about, and the answer. */
	let askedAbout = '';
	let asked: Promise<MembershipsAnswer> | null = null;

	function membershipsOf(ids: string[]): Promise<MembershipsAnswer> {
		const key = ids.join(',');
		if (asked && askedAbout === key) return asked;
		askedAbout = key;
		asked = fetchMemberships(ids);
		return asked;
	}

	/** Forget it, because something was just written that would make it wrong. */
	function membershipsMoved(): void {
		asked = null;
		askedAbout = '';
	}

	async function fetchMemberships(ids: string[]): Promise<MembershipsAnswer> {
		if (ids.length === 0) return NOTHING_ON;
		try {
			const pages: MembershipsAnswer[] = [];
			for (let start = 0; start < ids.length; start += MOST_PER_ASK) {
				pages.push(
					await api.post<MembershipsAnswer>('/assets/memberships', {
						body: { asset_ids: ids.slice(start, start + MOST_PER_ASK) }
					})
				);
			}
			return pages.length === 1 ? pages[0] : joinAnswers(pages);
		} catch {
			// No marks rather than wrong ones; a guest is refused this outright.
			return NOTHING_ON;
		}
	}

	/* Chunks of one selection joined: `all` only where every chunk said all, else `some`. */
	function joinAnswers(pages: MembershipsAnswer[]): MembershipsAnswer {
		const join = (of: (page: MembershipsAnswer) => MembershipList): MembershipList => {
			const all = pages
				.map(of)
				.reduce<string[]>(
					(kept, part) => kept.filter((id) => part.all.includes(id)),
					[...of(pages[0]).all]
				);
			const seen = new Set(pages.flatMap((page) => [...of(page).all, ...of(page).some]));
			for (const id of all) seen.delete(id);
			return { all, some: [...seen] };
		};
		const hearts = new Set(pages.map((page) => page.favorite));
		return {
			people: join((page) => page.people),
			sites: join((page) => page.sites),
			collections: join((page) => page.collections),
			photo_sets: join((page) => page.photo_sets),
			tags: join((page) => page.tags),
			songs: join((page) => page.songs),
			// One word for the whole selection: every chunk has to agree, or it is some of them.
			favorite: hearts.size === 1 ? [...hearts][0] : 'some'
		};
	}

	/** One kind's two lists, as a picker reads them: id to how much of the set carries it. */
	function onAlready(list: MembershipList): Record<string, OnAlready> {
		const marks: Record<string, OnAlready> = {};
		for (const id of list.all) marks[id] = 'all';
		for (const id of list.some) marks[id] = 'some';
		return marks;
	}

	/* Taking files back off, in the same shape as `AssetActions`' adds, through the same routes. */
	const TAKEN_OFF: Record<
		string,
		{ wall: string; lead: (files: string, single: boolean) => string }
	> = {
		tag: {
			wall: '/tags',
			lead: (files, single) => `${files} ${single ? 'is' : 'are'} no longer tagged`
		},
		collection: {
			wall: '/collections',
			lead: (files, single) => `${files} ${single ? 'was' : 'were'} removed from the collection`
		},
		person: {
			wall: '/people',
			lead: (files, single) => `${files} ${single ? 'was' : 'were'} removed from`
		},
		site: {
			wall: '/sites',
			lead: (files, single) => `${files} ${single ? 'was' : 'were'} removed from the Site`
		},
		photo_set: {
			wall: '/photo-sets',
			lead: (files, single) => `${files} ${single ? 'was' : 'were'} removed from the Photo Set`
		},
		song: {
			wall: '/songs',
			lead: (files, single) => `${files} ${single ? 'was' : 'were'} taken off the song`
		}
	};

	/** Say what came off, as `AssetActions.#landed` does. */
	function cameOff(ids: string[], choice: PickChoice, kind: string, done: BulkWriteDone): void {
		membershipsMoved();
		libraryChanges.changed();
		const off = ids.length - done.skipped;
		if (done.changed > 0 && off > 0) {
			const one = TAKEN_OFF[kind];
			const lead = one.lead(off === 1 ? 'The file' : `${grouped(off)} files`, off === 1);
			const href = `${one.wall}/${encodeURIComponent(choice.id)}`;
			toasts.show([`${lead} `, { text: choice.name, kind, id: choice.id, href }], {
				tone: 'success'
			});
		}
		announceSkipped(done, kind === 'photo_set' ? 'picture' : 'file');
	}

	/** One removal with its one failure message, answering what landed. */
	async function takeOff(
		ids: string[],
		choice: PickChoice,
		kind: string,
		write: () => Promise<BulkWriteDone>
	): Promise<PickLanded> {
		if (ids.length === 0) return 'refused';
		try {
			const done = await write();
			cameOff(ids, choice, kind, done);
			return landedOf(done, ids.length);
		} catch {
			toasts.show("That couldn't be removed", { tone: 'error' });
			return 'refused';
		}
	}

	/** Put files on from the picker and say what landed; the held answer is forgotten either side of
	 * the write, and the selection kept for the next pick. */
	async function wentOn(
		ids: string[],
		run: (how: PutOnHow) => Promise<BulkWriteDone | null>
	): Promise<PickLanded> {
		membershipsMoved();
		const done = await run({ keepSelection: true });
		membershipsMoved();
		return landedOf(done, ids.length);
	}

	/** One page of a kind, as the picker draws it: the rows, and how many did not fit. */
	function pageOf<T>(asked: { items: T[]; total: number }, row: (one: T) => PickChoice): PickPage {
		return { choices: asked.items.map(row), more: Math.max(0, asked.total - asked.items.length) };
	}

	const places: Pick<
		FileVerbHandlers,
		'tag' | 'assign' | 'site' | 'collect' | 'photoSet' | 'song'
	> = {
		tag: {
			kind: 'tag',
			plural: 'tags',
			ask: async (typed) => pageOf(await tagStore.choices(typed), tagRow),
			already: async (ids) => onAlready((await membershipsOf(ids)).tags),
			pick: (ids, choice) => wentOn(ids, (how) => actions.tag(ids, [choice], how)),
			unpick: (ids, choice) =>
				takeOff(ids, choice, 'tag', () => tagStore.assign(ids, [choice.id], false)),
			create: async (name: string) => {
				const made = await tagStore.create(name);
				return { id: made.id, name: made.name };
			}
		},
		assign: {
			kind: 'person',
			plural: 'people',
			ask: async (typed) => pageOf(await peopleStore.choices(typed), personRow),
			already: async (ids) => onAlready((await membershipsOf(ids)).people),
			pick: (ids, choice) => wentOn(ids, (how) => actions.assign(ids, [choice], how)),
			unpick: (ids, choice) =>
				takeOff(ids, choice, 'person', () => peopleStore.assign(ids, [choice.id], false)),
			create: makePerson
		},
		site: {
			kind: 'site',
			plural: 'Sites',
			ask: async (typed) => pageOf(await siteStore.choices(typed), siteRow),
			already: async (ids) => onAlready((await membershipsOf(ids)).sites),
			pick: (ids, choice) => wentOn(ids, (how) => actions.site(ids, [choice], how)),
			/* Bulk removal of a Site filing, by `add`, as the tag and person writes. */
			unpick: (ids, choice) =>
				takeOff(ids, choice, 'site', () => peopleStore.filedUnder(ids, [choice.id], false)),
			create: (name: string) => siteStore.create(name)
		},
		collect: {
			kind: 'collection',
			plural: 'collections',
			ask: async (typed) => pageOf(await collectionStore.choices(typed), collectionRow),
			already: async (ids) => onAlready((await membershipsOf(ids)).collections),
			pick: (ids, choice) => wentOn(ids, (how) => actions.collect(ids, [choice], how)),
			unpick: (ids, choice) =>
				takeOff(ids, choice, 'collection', () => collectionStore.removeItems(choice.id, ids)),
			create: (name: string) => collectionStore.create(name)
		},
		photoSet: {
			kind: 'photo_set',
			plural: 'Photo Sets',
			ask: async (typed) => pageOf(await photoSetStore.choices(typed), photoSetRow),
			already: async (ids) => onAlready((await membershipsOf(ids)).photo_sets),
			pick: (ids, choice) => wentOn(ids, (how) => actions.photoSet(ids, [choice], how)),
			unpick: (ids, choice) =>
				takeOff(ids, choice, 'photo_set', () => photoSetStore.remove(choice.id, ids)),
			create: (name: string) => photoSetStore.create(name)
		},
		/* A file carries one song: picking another moves it. */
		song: {
			kind: 'song',
			plural: 'songs',
			ask: async (typed) => pageOf(await songStore.choices(typed), songRow),
			already: async (ids) => onAlready((await membershipsOf(ids)).songs),
			pick: (ids, choice) => wentOn(ids, (how) => actions.song(ids, [choice], how)),
			unpick: (ids, choice) => takeOff(ids, choice, 'song', () => songStore.remove(choice.id, ids)),
			create: async (name: string) => {
				const made = await songStore.create(name);
				return { id: made.id, name: made.name };
			}
		}
	};

	/** Create a person from the typed name, without a toast: the write reports once. */
	async function makePerson(name: string): Promise<Choice> {
		const made = await peopleStore.create(name);
		return { id: made.id, name: made.name };
	}

	async function askToMove(ids: string[]) {
		if (ids.length === 0) return;
		acting = ids;
		await movable.ensure();
		if (movable.foldersRead === 'failed') {
			toasts.show("Sift couldn't read your folders", { tone: 'error' });
			return;
		}
		if (!movable.possible) {
			// Said, since the reason is a setting somebody can change.
			toasts.show(
				"Sift can't move files: every library folder is read-only to it. Give Sift write access to one first.",
				{
					tone: 'error'
				}
			);
			return;
		}
		moveOpen = true;
	}

	function askToCompress(ids: string[]) {
		if (ids.length === 0) return;
		acting = ids;
		// No preflight here: the panel asks each time the target changes.
		compressOpen = true;
	}

	/* The record a file's own sheet already holds, which carries everything the dialog draws. */
	function heldForEditing(id: string): EditableAsset | null {
		const held: object | undefined = lookup(id);
		const needs = ['media_type', 'width', 'height', 'duration_ms', 'filename', 'art', 'sprite'];
		return held && needs.every((field) => field in held) ? (held as EditableAsset) : null;
	}

	/* Opened on the press from the held record where there is one, then read fresh in place. */
	async function askToEdit(ids: string[], mode: 'trim' | 'gif' = 'trim') {
		const id = ids[0];
		if (!id) return;
		const held = heldForEditing(id);
		if (held) {
			editing = held;
			editMode = mode;
			editOpen = true;
		}
		try {
			const fresh = await api.get<EditableAsset>(`/assets/${encodeURIComponent(id)}`);
			if (held && (!editOpen || editing?.id !== id)) return;
			editing = fresh;
			editMode = mode;
			editOpen = true;
		} catch {
			if (!held) toasts.show("Sift couldn't open that file to edit it", { tone: 'error' });
		}
	}

	function askToShare(ids: string[]) {
		sharing = actions.shareTargets(ids);
		if (sharing.length > 0) shareOpen = true;
	}

	/* The report off the sharing panel's own target. */
	function askAboutReach(ids: string[]) {
		reaching = actions.shareTargets(ids.slice(0, 1))[0] ?? null;
		if (reaching) reachOpen = true;
	}

	function askToDelete(ids: string[]) {
		if (ids.length === 0) return;
		deleting = ids;
		deleteOpen = true;
	}

	/* Which file the chooser is about, and whether it is up. */
	let matching = $state('');
	let matchesOpen = $state(false);

	/* Where the menu's one file stands with enrichment, asked per file, never for a selection; keyed
	 * by file and written only on the answer, as a template may call this
	 * (`state_unsafe_mutation`). */
	let enrichment = $state<
		Record<
			string,
			{ keptLocal: boolean; refused: boolean; why?: string; note?: string; keptOut?: boolean }
		>
	>({});
	const enrichmentAsked = new Set<string>();

	function askAboutEnrichment(id: string | undefined): void {
		if (id === undefined || enrichmentAsked.has(id)) return;
		enrichmentAsked.add(id);
		void enrichmentOf('asset', id).then((state) => {
			const { refused, why } = refusedOutside(state);
			enrichment[id] = {
				...enrichment[id],
				keptLocal: state?.kept_local ?? false,
				refused,
				...(why ? { why } : {}),
				note: lastEnriched(state) ?? undefined
			};
		});
		/* And with swaps, for the admin's Don't swap row, on its own key. */
		if (session.isAdmin)
			void keptFromSwaps('asset', id)
				.then((state) => (enrichment[id] = { ...enrichment[id], keptOut: state.kept_out_here }))
				.catch(() => {
					// Unknown reads as not kept out: the row offers Don't swap, which is the safe word.
				});
	}

	const handlers = {
		...places,
		favorite: (ids: string[]) => void actions.favorite(ids),
		rate: (ids: string[], rating: number | null) => void actions.rate(ids, rating),
		move: (ids: string[]) => void askToMove(ids),
		rename: (ids: string[]) => {
			renaming = ids;
			renameOpen = true;
		},
		compress: (ids: string[]) => askToCompress(ids),
		edit: (ids: string[]) => void askToEdit(ids),
		gif: (ids: string[]) => void askToEdit(ids, 'gif'),
		/* The similar-files wall (`likeWall`). */
		similar: (ids: string[]) => void goto(likeWall(ids[0])),
		share: (ids: string[]) => askToShare(ids),
		visibility: (ids: string[]) => askAboutReach(ids),
		hide: (ids: string[]) => void actions.hide(ids, !unhides(ids)),
		unlock: () => vaultPrompt.ask(),
		save: (ids: string[]) => void actions.save(ids),
		link: (ids: string[]) => void actions.copyLink(ids[0]),
		/*
		 * Auto-enrich: the press is the consent; exact matches land, the rest wait under Organize.
		 */
		autoEnrich: (ids: string[], box?: string) => {
			/*
			 * Refused before sending where the menu knows the answer; over a selection the server
			 * says.
			 */
			if (ids.length === 1 && enrichment[ids[0]]?.refused) {
				sayKeptLocal();
				return;
			}
			void enrichFiles(ids, { box: box ?? '', auto: true });
		},
		/* Enrich: one file opens the chooser, several queue to the pile under Organize. */
		enrich: (ids: string[]) => {
			/* Refused before the sheet opens, so it never gives two answers. */
			if (ids.length === 1 && enrichment[ids[0]]?.refused) {
				sayKeptLocal();
				return;
			}
			if (ids.length === 1) {
				matching = ids[0];
				matchesOpen = true;
				return;
			}
			void enrichFiles(ids);
		},
		/* AcoustID, by the sound; the server refuses kept-local or shut Hidden files. */
		lookUpSongs: (ids: string[]) => void lookUpSongs(ids),
		/* And again, for a file AcoustID did not know: the server says when it knew it. */
		lookUpSongsAgain: (ids: string[]) => void lookUpSongs(ids, true),
		/* Kept local, for the whole selection; the answer reverses the row in place. */
		keepLocal: (ids: string[], kept: boolean) => {
			void Promise.all(ids.map((id) => setKeptLocal('asset', id, kept))).then(() => {
				for (const id of ids) enrichment[id] = { ...enrichment[id], keptLocal: kept };
			});
		},
		/* Kept out of swaps, as `keepLocal`. */
		keepFromSwaps: (ids: string[], kept: boolean) => {
			void Promise.all(ids.map((id) => setKeptFromSwaps('asset', id, kept))).then(
				(answers) => {
					for (const one of answers)
						enrichment[one.id] = { ...enrichment[one.id], keptOut: one.kept_out_here };
					toasts.show(kept ? 'Kept out of swaps' : 'Can be swapped again');
				},
				() => toasts.show("That couldn't be changed", { tone: 'error' })
			);
		},
		/* Run the named pass on these files; the server's sentence is the toast. */
		runNow: (ids: string[], run: string) => void runNow(ids, run),
		remove: (ids: string[]) => askToDelete(ids)
	};

	/* Spread in only where this surface pins, so no uncallable key exists. */
	const pinHandler = $derived(
		pinnable ? { pin: (ids: string[], pinned: boolean) => void actions.pin(ids, pinned) } : {}
	);

	const saving = { label: saveActionLabel, icon: saveActionIcon };

	/** Whether Hide would unhide these, read off the files, not the screen. */
	function unhides(ids: string[]): boolean {
		return showingHidden || (ids.length > 0 && ids.every((id) => lookup(id)?.hidden));
	}

	/** Whether every one of these is a locked tile: hidden, with the vault shut. See `allLocked`. */
	function allLocked(ids: string[]): boolean {
		return ids.length > 0 && ids.every((id) => lookup(id)?.concealed === true);
	}

	/** Whether every file in a set is already a favorite, which is what lets the bar say so. */
	function allFavorited(ids: string[]): boolean {
		return ids.length > 0 && ids.every((id) => lookup(id)?.favorite);
	}

	/** The same question for the pin. Mixed reads as NOT pinned. See `allPinned` in the verbs. */
	function allArePinned(ids: string[]): boolean {
		return ids.length > 0 && ids.every((id) => lookup(id)?.pinned === true);
	}

	/** The rating every file in a set shares, or null when they do not share one. */
	function sharedRating(ids: string[]): number | null {
		const first = lookup(ids[0])?.rating ?? null;
		return ids.every((id) => (lookup(id)?.rating ?? null) === first) ? first : null;
	}

	function built(ids: string[], subjectId?: string): Verb[] {
		const subject = subjectId ? lookup(subjectId) : null;
		askAboutEnrichment(ids.length === 1 ? ids[0] : undefined);
		const known = ids.length === 1 ? enrichment[ids[0]] : undefined;
		// The boxes, once, for an admin only.
		if (session.isAdmin) loadEnrichBoxes();
		// And the song lookup's name and state, for its row beside the boxes: an admin's route too.
		if (session.isAdmin) loadSongLookup();
		// The same for Run task's passes: an admin's list, loaded by the first menu that wants it.
		if (session.isAdmin) loadRunNowGroups();
		return fileVerbs(
			{
				isAdmin: session.isAdmin,
				canSave: session.canSave,
				showingHidden,
				keptLocal: known?.keptLocal ?? false,
				keptFromSwaps: known?.keptOut ?? false,
				enrichRefused: known?.refused ?? false,
				...(known?.why ? { enrichWhy: known.why } : {}),
				enrichBoxes: enrichBoxes(),
				songLookup: songLookup(),
				runGroups: runNowGroups(),
				...(known?.note ? { lastEnriched: known.note } : {}),
				subject: subject
					? {
							media_type: subject.media_type,
							favorite: subject.favorite,
							pinned: subject.pinned ?? false,
							concealed: subject.concealed
						}
					: null,
				count: ids.length,
				rating: sharedRating(ids),
				allHidden: unhides(ids),
				allLocked: allLocked(ids),
				allFavorite: allFavorited(ids),
				allPinned: allArePinned(ids),
				canMove: movable.possible,
				// Compressing needs a folder Sift may write to, which Move proves too.
				canCompress: movable.possible,
				handlers: { ...handlers, ...pinHandler }
			},
			saving
		);
	}
</script>

{@render children({
	/*
	 * The bar reads the declaration in its declared shape: the same list the menu reads, with
	 * the same five lists under Add to.
	 *
	 * "Add to" is one door in the bar, opening onto the same rows the right-click menu and a
	 * file's own Add to show under it, flyouts included, so a child's words and its list are
	 * written once. Flattening and pruning are the caller's, through `barShape`, because a wall
	 * appends its own verbs to this list first.
	 */
	bar: (ids: string[]) => built(ids),
	menu: (ids: string[], subjectId?: string) => menuVerbs(built(ids, subjectId)),
	named: (verb: VerbId, ids: string[], subjectId?: string) =>
		built(ids, subjectId).find((one) => one.id === verb),
	actions
})}

<!-- The one that moves files, which asks where to before it writes. -->
<MoveDialog
	bind:open={moveOpen}
	count={acting.length}
	folders={movable.folders}
	onconfirm={(folderId: string) => void actions.move(acting, folderId)}
/>

<BatchRename bind:open={renameOpen} assetIds={renaming} />

<!-- Compress asks the server first: the browser cannot do that arithmetic. -->
<CompressDialog
	bind:open={compressOpen}
	assetIds={acting}
	onqueued={() => around.selection.clear()}
/>

<!-- The editor, mounted once there is a file. -->
{#if editing}
	<EditDialog
		bind:open={editOpen}
		asset={editing}
		mode={editMode}
		onqueued={() => around.selection.clear()}
	/>
{/if}

<!-- `canDeleteFromDisk` is the admin's half; the verb is admin only (verbs.test.ts). -->
<DeleteDialog
	bind:open={deleteOpen}
	count={deleting.length}
	ids={deleting}
	canDeleteFromDisk={session.isAdmin}
	unavailableReason="Only an admin can delete files from disk."
	onconfirm={(mode: DeleteMode) => void actions.remove(deleting, mode)}
/>

<ShareDialog bind:open={shareOpen} targets={sharing} onapplied={() => around.selection.clear()} />
<VisibilityDialog bind:open={reachOpen} target={reaching} />

<!-- What the stash-boxes make of one file, mounted once chosen. -->
{#if matching}
	<FileMatches
		bind:open={matchesOpen}
		assetId={matching}
		onapplied={() => around.selection.clear()}
	/>
{/if}
