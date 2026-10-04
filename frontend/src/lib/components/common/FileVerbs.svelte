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
	   ask before they write, and hands them to a snippet, so there is nothing to look at here that
	   is not already on the gallery as `VerbButtons` and `VerbMenuItems`, which are what it produces. */
	/* WHY NOT BITS-UI: not a control. This is the wiring behind the verbs (the actions and the sheets that ask
	   before they write) and it renders no interface of its own. */
	/*
	 * Everything that can be done to a file, wired once, for any surface that shows files.
	 *
	 * The verb LIST lives in one place (`$lib/grid/verbs`), and that is what stops a bar and a menu
	 * offering different things. Everything behind it lives here: the actions, the four sheets that
	 * ask before they write, and the handlers tying the two together. Inside one screen, a second
	 * surface showing files could only duplicate the lot or write its own button (a rail offering
	 * Share and nothing else beside a grid offering ten verbs).
	 *
	 * So this owns the sheets and hands its children the finished verbs. A surface says what it is
	 * showing and what it can do about it; it does not get to decide which verbs exist.
	 */
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
		/**
		 * Whether files on this surface can be kept at the top of it.
		 *
		 * Off unless a surface says otherwise, and the default is the point. A pin belongs to a wall
		 * somebody curates (a person's files, a collection's, Favorites, Hidden), not the whole
		 * library or the log of what has been watched. Neither of those is curated, and a verb on a
		 * wall where it means nothing is a verb somebody has to learn to ignore.
		 */
		pinnable?: boolean;
		/**
		 * Drawn with the verbs ready to render.
		 *
		 * `bar` is the declaration a bar reads and `menu` is the one a right-click menu reads. They
		 * differ in one thing only: `menu` takes the file it was opened on, so it can word the heart
		 * for that one file, and a bar never addresses one file in particular. Both keep the groups
		 * and both carry the lists: what a bar leaves out is `barShape`'s to decide, so a wall can
		 * append its own verbs before anything is pruned or split.
		 *
		 * `named` is for a surface that draws ONE verb as a control of its own rather than as a row
		 * in a list: the sharing mark on a tile, and the file's own screen, which draws Save, Edit
		 * and Compress as named buttons in its own layout. It answers with the declared verb or with
		 * nothing, so whether the control appears at all is decided in the same place as everything
		 * else rather than by a second copy of the rule written beside the button.
		 */
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
	/* Built once, and every member reads through a function so it follows the surface rather than
	   freezing whatever the props held on the first render. */
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

	/* Whether Move can be offered at all: it needs somewhere to move to, which most libraries
	   deliberately do not have. Asked once, and only by an admin, who is the only account it is
	   offered to. */
	$effect(() => {
		if (session.isAdmin) void movable.ensure();
	});

	/*
	 * Move and Compress: two verbs that need an answer first, in a sheet of their own.
	 *
	 * The same shape each time: remember what is being acted on, then open the sheet. The five
	 * places a file can be put are not here: each is a list that opens out of its row (see
	 * `places`), on every surface that draws the row.
	 */
	let acting = $state<string[]>([]);
	let moveOpen = $state(false);
	let renameOpen = $state(false);
	let renaming = $state<string[]>([]);
	let compressOpen = $state(false);
	let editOpen = $state(false);
	/*
	 * Which way the editor opens: Trim on the clip, or straight into making a GIF from its own row.
	 * The dialog takes it as `mode`; this remembers which row was pressed.
	 */
	let editMode = $state<'trim' | 'gif'>('trim');
	/* The one file the editor is open on, fetched rather than taken off the tile.
	 *
	 * A tile carries what a grid needs to draw it, and the editor needs what the file IS: how big
	 * the picture is, how long the video runs, and whether a scrub strip has been built for it.
	 * Reading those off a tile would work on the screens whose tiles happen to carry them and fail
	 * quietly on the ones that do not. */
	let editing = $state<EditableAsset | null>(null);
	let deleting = $state<string[]>([]);
	let deleteOpen = $state(false);
	let sharing = $state<ShareTarget[]>([]);
	let shareOpen = $state(false);
	/* What the visibility report is open on. ONE file: forty have forty different answers to
	   "who can see this and how", so the verb is `singleOnly` and this is not a list. */
	let reaching = $state<ShareTarget | null>(null);
	let reachOpen = $state(false);

	/*
	 * Every list here is drawn by `pickRow`, the one rule for the picture a thing is drawn by
	 * (`$lib/entity/entity-picture`), so a row wears what its card and its page wear: a tag and a
	 * collection their covers as much as a person their face.
	 */
	const tagRow = (tag: Drawable) => pickRow('tag', tag);
	const collectionRow = (one: Drawable) => pickRow('collection', one);
	const photoSetRow = (set: Drawable) => pickRow('photo_set', set);
	const songRow = (one: Drawable) => pickRow('song', one);

	/*
	 * The five lists a file can be put on, each the row itself under Add to.
	 *
	 * Written here and handed to `$lib/grid/verbs` as its handlers, because everything a pick needs
	 * (the store, the choices, the write and the making) lives in this file, while the verb list
	 * knows ids, words and icons so a verb can be read without its wiring. Typed by the handler
	 * list, so a sixth place declared there and not written here is refused by the compiler rather
	 * than drawn as a row that opens onto nothing.
	 *
	 * ONE LIST FOR EVERY DOOR. The right-click menu, a file's own Add to and the selection bar's
	 * Add to all draw these rows, because the declaration carries them. No sheet stands beside
	 * them for a set: a flyout over a set marks what some or all of it is on, stays open for the
	 * next pick, and writes to every file, so a sheet would be a second Add to menu saying the same
	 * thing differently.
	 *
	 * The row somebody picked, whole. A remembered row is drawn from the account's own record and
	 * is often not on the current page of the list, so looking a name up by id would come back
	 * empty; the declaration takes the choice itself, which already carries its name.
	 *
	 * What the files are already on, asked once for all five lists. A menu has five pickers asking
	 * about the same files, and five requests would be five readings of a selection that can change
	 * between them. The server answers all five kinds plus the heart in one reply (`POST
	 * /assets/memberships`), and each picker reads its part out of the shared promise.
	 *
	 * Held under the ids it was asked about, so opening Person then Collection over one selection
	 * reads the first answer. Dropped the moment anything is written, because a write is what makes
	 * it wrong: the picker moves its own row (see `PickMenu.choose`), and the next open must show
	 * the new state.
	 *
	 * The reply, as the server publishes it. `favorite` is a bare string on the wire and is
	 * narrowed where it is read (`onAlready`); the five lists are the server's own `Membership`.
	 */
	type MembershipsAnswer = components['schemas']['Memberships'];
	type MembershipList = components['schemas']['Membership'];

	/** The most ids one question may name. The server's own cap, and the same 500 every bulk write
	 *  is split by, so a selection bigger than one request is split rather than refused. */
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

	/** Which files the held answer is about, and the answer itself. Both, because the answer is only
	 *  reusable for exactly the set it was asked about. */
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
			// No marks rather than wrong ones, and the flyout is fully usable without them. A guest
			// is refused this outright, which is the ordinary case here rather than a failure.
			return NOTHING_ON;
		}
	}

	/*
	 * Several chunks of one selection, read as one answer.
	 *
	 * "All of them" is the only part that cannot simply be added up: a tag on every file of the
	 * first five hundred and on none of the next is on SOME of the selection, not all, so `all`
	 * is what every chunk agreed was all, and everything else anybody saw at all is `some`.
	 */
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

	/*
	 * Taking files back off.
	 *
	 * The adds are `AssetActions`, one method each, with the sentence they announce in a table at
	 * the top of that file. These removals are written here in the same shape as those, beside
	 * `LANDED` in `$lib/grid/actions`, so moving them there is a copy rather than a rewrite; this is
	 * a second place the five kinds are worded.
	 *
	 * Each takes the whole set and one thing to come off it, which is what a picker row hands over.
	 * The route is the same one the add uses in every case, told to remove, so there is no second
	 * address to keep in step.
	 */
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

	/** Say what came off, naming the one thing it came off. The same shape `AssetActions.#landed`
	 *  uses, down to the link being handed over rather than written into the sentence. */
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

	/** One removal, with the one message every failure of one gets. Written once because five
	 *  copies of a try/catch is five chances for one of them to say nothing at all. Answers with
	 *  what landed, which is what puts the picker's tick back on a refusal (`PickLanded`). */
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

	/**
	 * Put files on from the menu's picker, and say what landed.
	 *
	 * What was held about where the files already were is forgotten on both sides of the write:
	 * before, so nothing reads the old answer while the write is out; after, so a picker told
	 * `partly` and asking again gets the server's answer rather than the one from before the press.
	 *
	 * The selection is kept (`keepSelection`): a right press keeps the flyout up for the next pick,
	 * over the same files, from whichever door it was opened.
	 */
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
			/*
			 * Bulk removal of a site filing: the file's own record can only undo per file, so `POST
			 * /assets/sites` takes `add` like the tag and person writes beside it.
			 */
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
		/* A file carries one song: picking another moves the files to it, and the tick moves with
		   them once the answer is asked again. Made by name where none carries the name typed. */
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

	/**
	 * Make a person out of the name that was typed, and hand them back, with no toast here: the
	 * flyout picks the one it made and then writes what it was for, so what happened is reported
	 * once, by the write. A person made here has a name and nothing else, and their
	 * page is one press away from wherever they are next drawn.
	 */
	async function makePerson(name: string): Promise<Choice> {
		const made = await peopleStore.create(name);
		return { id: made.id, name: made.name };
	}

	async function askToMove(ids: string[]) {
		if (ids.length === 0) return;
		acting = ids;
		await movable.ensure();
		if (!movable.possible) {
			// Said rather than shown as an empty chooser: the reason is a setting, and naming it is
			// the only thing that turns a dead end into something somebody can fix.
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
		// No preflight here, deliberately: the panel asks, because it has to ask again every time
		// the target changes and a second copy of that call would be a second answer to keep true.
		compressOpen = true;
	}

	async function askToEdit(ids: string[], mode: 'trim' | 'gif' = 'trim') {
		const id = ids[0];
		if (!id) return;
		try {
			editing = await api.get<EditableAsset>(`/assets/${encodeURIComponent(id)}`);
			editMode = mode;
			editOpen = true;
		} catch {
			toasts.show("Sift couldn't open that file to edit it", { tone: 'error' });
		}
	}

	function askToShare(ids: string[]) {
		sharing = actions.shareTargets(ids);
		if (sharing.length > 0) shareOpen = true;
	}

	/* The report, off the same target the sharing panel is built from: one call, so the two panels
	   cannot come to disagree about what the thing is called or which id it is. */
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

	/*
	 * WHERE THE FILE THE MENU IS OPEN ON STANDS with enrichment from outside this machine.
	 *
	 * Asked when the menu is built for ONE file and never for a selection, which is not thrift: it
	 * is a fact about one row, and a menu over forty has forty answers that no single row could
	 * draw. So a selection gets the verb with no line under it and the plain wording, which is the
	 * honest reading of "these forty are not all the same".
	 *
	 * Remembered by the id it was asked about so one render does not ask again. A menu is opened,
	 * read and closed; holding it past that would mean drawing a decision somebody may have just
	 * changed from the row underneath.
	 */
	/*
	 * Keyed by the file, and WRITTEN ONLY WHEN THE SERVER ANSWERS. Resetting state on every ask,
	 * synchronously, would fail: `built` is called from template expressions (a `{@const}` in the
	 * theater's cell menu), where Svelte refuses a state write
	 * outright (`state_unsafe_mutation`). A map keyed by id needs no reset: a menu over another
	 * file reads that file's own answer or none, and an answer arriving late lands under its own
	 * key and redraws nothing else.
	 */
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
		/* And where it stands with swaps, for the Don't swap row beside Don't enrich: an admin's
		   question, as the row is an admin's. Its own key, so the two answers land independently. */
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
		/* The wall of files similar to this one. The chip there names the file by asking it, so a
		   press here and a pasted link read the same. See `likeWall`. */
		similar: (ids: string[]) => void goto(likeWall(ids[0])),
		share: (ids: string[]) => askToShare(ids),
		visibility: (ids: string[]) => askAboutReach(ids),
		hide: (ids: string[]) => void actions.hide(ids, !unhides(ids)),
		unlock: () => vaultPrompt.ask(),
		save: (ids: string[]) => void actions.save(ids),
		link: (ids: string[]) => void actions.copyLink(ids[0]),
		/*
		 * Auto-enrich: ask, and accept what is certain; the press is the consent.
		 *
		 * One file or forty, the same act: each file is one queued question, an exact match lands
		 * the moment it comes back, and anything less certain waits in the pile under Organize. The
		 * box is the flyout's row, handed straight on; dropping it would ask every box whatever was
		 * pressed.
		 */
		autoEnrich: (ids: string[], box?: string) => {
			/* REFUSED BEFORE ANYTHING IS SENT where the menu already knows the answer. Over a
			   selection the menu has no one answer, so the server says which were kept local. */
			if (ids.length === 1 && enrichment[ids[0]]?.refused) {
				sayKeptLocal();
				return;
			}
			void enrichFiles(ids, { box: box ?? '', auto: true });
		},
		/* ENRICH: ask and let a person choose. One file opens the chooser; several queue the batch.
		 *
		 * The same split every verb on this menu makes between a row and a selection, and here it is
		 * the difference between a conversation and a job. A sheet showing twenty candidates for each
		 * of forty files is not a screen anybody can use, so a selection goes to the pile under
		 * Organize where a page is settled in one press: every answer waits there, exact or not,
		 * because this is the verb that decides nothing. One file is somebody asking about THAT
		 * file, and it gets an answer. */
		enrich: (ids: string[]) => {
			/*
			 * Refused before the sheet opens, never inside it: a chooser opened on a file nothing
			 * may be asked about would report the refusal and also "no stash-box recognized this
			 * file", two answers to one press, one of them untrue.
			 */
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
		/* ACOUSTID: which song each of these is, asked by the sound. The server refuses a file kept
		   local or in a shut Hidden and says so, so nothing is decided here. */
		lookUpSongs: (ids: string[]) => void lookUpSongs(ids),
		/* And again, for a file AcoustID did not know: the server says when it knew it. */
		lookUpSongsAgain: (ids: string[]) => void lookUpSongs(ids, true),
		/* KEPT LOCAL, one press for the whole selection. The state comes back from the server and
		   is put where the verb reads it, so the row reverses without the menu being re-opened:
		   the same shape the hide and pin handlers beside it take. */
		keepLocal: (ids: string[], kept: boolean) => {
			void Promise.all(ids.map((id) => setKeptLocal('asset', id, kept))).then(() => {
				for (const id of ids) enrichment[id] = { ...enrichment[id], keptLocal: kept };
			});
		},
		/* KEPT OUT OF SWAPS, the Visibility panel's switch as a row, for the whole selection. The
		   answer is put where the row reads it, as `keepLocal` does. */
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
		/* RUN NOW: the pass the flyout row named, for these files. The server says what it did or
		   why it would not, and that sentence is the toast. See `$lib/jobs/run-now`. */
		runNow: (ids: string[], run: string) => void runNow(ids, run),
		remove: (ids: string[]) => askToDelete(ids)
	};

	/* Handed over only where this surface pins, which is what decides whether the verb exists at
	   all. See `pinnable`, and `fileVerbs`, which offers it only when it is given one. Spread in
	   rather than set to `undefined`, so the handler list carries no key nobody can call. */
	const pinHandler = $derived(
		pinnable ? { pin: (ids: string[], pinned: boolean) => void actions.pin(ids, pinned) } : {}
	);

	const saving = { label: saveActionLabel, icon: saveActionIcon };

	/** Whether pressing Hide over these would UNhide them.
	 *
	 *  Off the files, not the screen: with the vault open, hidden files sit on the ordinary walls
	 *  beside everything else, so the screen cannot answer it, and a wrong answer points the
	 *  action the wrong way rather than merely mislabelling the button. */
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
		// The boxes, once, and only for an admin: the list is an admin's route, and a guest asking
		// for it would be one 403 per menu.
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
				// The same answer, asked as a different question. Compressing writes a new file into
				// a library folder, so it needs one Sift may write to, which is what having
				// somewhere to move to also proves, today.
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

<!-- Compressing is the one verb here whose sheet asks the SERVER a question before it offers a
     button, because whether a target can be met is arithmetic over a file's running time and
     picture size and the browser holds neither. -->
<CompressDialog
	bind:open={compressOpen}
	assetIds={acting}
	onqueued={() => around.selection.clear()}
/>

<!-- The editor, on the one file a menu was opened over. Mounted only once there is a file for it,
     because what it draws is that file's own picture and it has nothing to show without one. -->
{#if editing}
	<EditDialog
		bind:open={editOpen}
		asset={editing}
		mode={editMode}
		onqueued={() => around.selection.clear()}
	/>
{/if}

<!-- `canDeleteFromDisk` is the half only an admin has and nothing else, because nothing else can
     be known here (`DeleteDialog`'s head says why). Defence in depth: the Delete verb is only pushed for an admin
     (`grid/verbs.ts`, held by `verbs.test.ts`), so the reason below appears only if that stops
     being true. -->
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

<!-- What the stash-boxes make of ONE file. Mounted only once a file has been chosen, because what
     it asks about is that file and it has nothing to ask without one. -->
{#if matching}
	<FileMatches
		bind:open={matchesOpen}
		assetId={matching}
		onapplied={() => around.selection.clear()}
	/>
{/if}
