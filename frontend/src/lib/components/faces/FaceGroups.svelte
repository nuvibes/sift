<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	import { pileHref } from '$lib/organize/addresses';
	import {
		fingerprintOffers,
		fingerprintQuestion,
		makePersonFromFingerprints,
		type FingerprintOffer
	} from '$lib/people/fingerprint-offers';
	import { goto } from '$app/navigation';
	import { pageOf } from '$lib/entity/related.svelte';
	import { thing } from '$lib/components/common/toast-pieces';
	import { untrack } from 'svelte';
	import type { Attachment } from 'svelte/attachments';
	/*
	 * The piles of faces nobody named, and the discarded ones: one list under two statuses. Naming
	 * a pile is one question for many sightings; Discard is not a delete. Every number is the
	 * server's.
	 */
	import type { OnPaging } from '$lib/components/common/Pager.svelte';
	import { onDestroy } from 'svelte';
	import { page as address } from '$app/state';
	import { anchorIn, rememberAnchor } from '$lib/grid/anchor';
	import { CardPaging } from '$lib/grid/cards.svelte';
	import CardWall from '$lib/components/organize/CardWall.svelte';
	import DecisionCard from '$lib/components/organize/DecisionCard.svelte';
	import Answers, { type Answer } from '$lib/components/organize/Answers.svelte';
	import FaceCovers from '$lib/components/faces/FaceCovers.svelte';
	import ReferenceCount from '$lib/components/faces/ReferenceCount.svelte';
	import {
		ActionBar,
		ConfirmDialog,
		ContextMenu,
		Empty,
		PickMenu,
		Problem,
		Selection,
		Skeleton,
		TileGesture,
		VerbButtons,
		VerbMenuItems,
		VerbMore
	} from '$lib/components/common';
	import { pileVerbs } from '$lib/components/faces/verbs';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import { barShape, type PickChoice, type PickPage } from '$lib/components/common/verbs';
	import { people } from '$lib/people/people.svelte';
	import { personRow } from '$lib/people/person-row';
	import { announceSkipped } from '$lib/library/bulk';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { decided } from '$lib/organize/organize.svelte';
	import {
		CROPS_ON_A_CARD,
		ignoreGroup,
		faceGroups,
		nameFaces,
		PILES_PER_PAGE,
		referenceStrengths,
		removeFaces,
		restoreGroup,
		type FaceGroup,
		type PileStatus,
		type ReferenceStrengths
	} from '$lib/people/faces.svelte';

	interface Props {
		status: PileStatus;
		onpaging?: OnPaging;
		/**
		 * Groups read by a host (the review list); this draws them and asks the host to re-read.
		 */
		supplied?: FaceGroup[] | null;
		onreload?: () => Promise<void> | void;
		/** The host draws one empty state for its whole list. */
		quiet?: boolean;
		/** The tab this wall is on, carried into the group screen (`pileHref`). */
		tab?: string;
		measure?: Attachment<HTMLElement>;
	}

	let {
		status,
		onpaging,
		supplied = null,
		onreload,
		quiet = false,
		tab,
		measure
	}: Props = $props();

	const handed = $derived(supplied !== null);

	$effect(() => {
		// A handed list's pager is the host's.
		if (!handed) onpaging?.(paging.asPager(groups.length, total, 'groups'));
	});
	onDestroy(() => onpaging?.(null));

	let fetched = $state<FaceGroup[]>([]);
	const groups = $derived(supplied ?? fetched);
	let loading = $state(true);
	let failed = $state(false);

	/* Paged by whole rows of the window, like every wall. */
	const paging = new CardPaging(PILES_PER_PAGE, 'faces.groups');
	let total = $state(0);
	let busy = $state(false);
	let confirmingAway = $state<FaceGroup | null>(null);
	let confirmOpen = $state(false);
	/*
	 * Removing a group has its own dialog: a reversible sentence must not confirm a permanent act.
	 */
	let confirmingRemoval = $state<FaceGroup | null>(null);
	let removeOpen = $state(false);
	let removeManyOpen = $state(false);
	let aimed = $state<string | null>(null);
	/* The ids the chosen verb was handed: a right-click aims without picking. */
	let acting = $state<string[]>([]);
	/* Read once for the screen, not per keystroke. */
	let strengths = $state<ReferenceStrengths | null>(null);

	/*
	 * A page of the library's people from the server, with `total`, so a match past the wall is
	 * found.
	 */
	async function askPeople(typed: string): Promise<PickPage> {
		const asked = await people.choices(typed);
		return {
			choices: asked.items.map(personRow),
			more: Math.max(0, asked.total - asked.items.length)
		};
	}

	/* Where this wall was left, in the address (`$lib/grid/anchor`); `path` is captured once. */
	const path = address.url.pathname;
	let arriving = true;

	/** Through `land`, in the same step as the rows (`CardPaging.land`). */
	function settle(at: number, first: string | null | undefined) {
		paging.land(at);
		rememberAnchor(address.url, path, first, at);
	}

	/* Ours, or the host's re-read, which brings the counts back into step. */
	async function load() {
		if (handed) {
			await onreload?.();
			return;
		}
		failed = false;
		let overtaken = false;
		try {
			const asked = status;
			const page = await paging.fill(
				asked,
				() => fetched,
				(query) => {
					loading = true;
					return faceGroups(asked, query);
				},
				(answer) => ({ rows: answer.groups, total: answer.total, offset: answer.offset })
			);
			if (page === null) {
				overtaken = true;
				return;
			}
			fetched = page.rows;
			total = page.total;
			settle(page.offset, fetched[0]?.id);
		} catch {
			failed = true;
		} finally {
			if (!overtaken) loading = false;
		}
	}

	/* A new status starts at the top. */
	$effect(() => {
		void status;
		untrack(() => {
			paging.offset = 0;
		});
	});

	/* `paging.size` too: a taller window holds more rows. */
	$effect(() => {
		void status;
		void paging.offset;
		void paging.size;
		// A handed list is read by its host.
		if (handed) return;
		if (arriving) {
			arriving = false;
			// UNTRACKED: this effect's own answer writes the address.
			paging.arrive(untrack(() => anchorIn(address.url)));
		}
		/* UNTRACKED: tracked, `land` clearing the anchor would ask again. */
		untrack(() => void load());
	});

	/*
	 * Watching `loaded` alone: watching `loading` too loops as fast as the machine can go on a
	 * refusal.
	 */
	$effect(() => {
		const stale = !people.loaded;
		untrack(() => {
			if (stale && !people.loading) void people.load();
		});
	});

	$effect(() => {
		if (!strengths) {
			void referenceStrengths()
				.then((found) => (strengths = found))
				.catch(() => (strengths = null));
		}
	});

	/*
	 * Groups that look like somebody in a fingerprints file, asked about while that is off; open
	 * tab only.
	 */
	let offers = $state<Map<string, FingerprintOffer>>(new Map());
	let madeHere = $state<Map<string, { id: string; name: string }>>(new Map());

	async function readOffers() {
		offers = status === 'open' ? await fingerprintOffers() : new Map();
	}

	$effect(() => {
		void status;
		untrack(() => void readOffers());
	});

	/* A share moving changes every pile's size; a handed list is reloaded by its host. */
	reloadOnLibraryChange(() => {
		void readOffers();
		if (!handed) void load();
	});

	async function makeFromFingerprints(group: FaceGroup, offer: FingerprintOffer) {
		busy = true;
		try {
			const id = await makePersonFromFingerprints(offer.entry_id);
			madeHere = new Map(madeHere).set(group.id, { id, name: offer.name });
			toasts.show([thing('person', id, offer.name), ' is a person now'], { tone: 'success' });
		} catch {
			toasts.show("That person couldn't be made", { tone: 'error' });
		} finally {
			busy = false;
		}
	}

	/**
	 * Naming a pile confirms every face in it as that person, in one write (`/faces/name`,
	 * `whole_group`); a vaulted face is skipped and counted.
	 */
	async function name(group: FaceGroup, personId: string) {
		busy = true;
		try {
			const named = await nameFaces(
				group.faces.map((face) => face.track_id),
				{ personId },
				true
			);
			await load();
			announceSkipped(named, 'face');
		} catch {
			toasts.show("Those faces couldn't be named", { tone: 'error' });
		} finally {
			busy = false;
		}
	}

	/**
	 * Create and pick, as every picker does; the picker offers create only when no row has the
	 * name.
	 */
	async function makePerson(named: string): Promise<PickChoice> {
		const made = await people.create(named);
		return { id: made.id, name: made.name };
	}

	/*
	 * The shared selection gesture: a long press starts it, and clicks are caught on the way DOWN.
	 */
	const selection = new Selection();
	const selecting = $derived(selection.count > 0);
	const gesture = new TileGesture(selection, () => groups.map((group) => group.id));
	const picked = $derived(selection.ordered(groups.map((group) => group.id)));

	/*
	 * Both answers always built, one dimmed, so the bar keeps its shape; naming points at the card.
	 */
	const verbs = $derived(
		pileVerbs({
			nameElsewhere: 'Name a group from its own card',
			setAside: (ids) => void actOn(ids),
			restore: (ids) => void actOn(ids),
			remove: (ids) => ((acting = ids), (removeManyOpen = true))
		}).map((verb) => {
			if (verb.id === 'set-aside') {
				return {
					...verb,
					label: 'Discard them',
					disabled: busy || status !== 'open',
					why: 'These are already discarded'
				};
			}
			if (verb.id === 'restore') {
				return { ...verb, disabled: busy || status !== 'ignored', why: "These aren't discarded" };
			}
			return { ...verb, disabled: verb.disabled || busy };
		})
	);
	const shape = $derived(barShape(verbs));

	/* Right-clicking a group that is not picked picks it. */
	function aimAt(groupId: string) {
		aimed = selection.has(groupId) ? null : groupId;
	}

	const menuIds = $derived(aimed ? [aimed] : picked);

	/* A page turn or a new tab lets go of the selection. */
	$effect(() => {
		void status;
		void paging.offset;
		selection.clear();
	});

	function pressed(group: FaceGroup, event: MouseEvent) {
		gesture.clicked(group.id, event);
	}

	function onEscape(event: KeyboardEvent) {
		// Picks only (`TileGesture.undoKeys`); no data.
		if (gesture.undoKeys(event)) {
			event.preventDefault();
			event.stopPropagation();
			return;
		}
		if (event.key !== 'Escape') return;
		if (gesture.escaped(event)) event.stopPropagation();
	}

	async function actOn(ids: string[]) {
		if (ids.length === 0 || busy) return;
		acting = ids;
		busy = true;
		const putting = status === 'open';
		try {
			for (const id of ids) {
				await (putting ? ignoreGroup(id) : restoreGroup(id));
			}
			selection.clear();
			await load();
		} catch {
			toasts.show(
				putting ? "Those groups couldn't be discarded" : "Those groups couldn't be restored",
				{ tone: 'error' }
			);
		} finally {
			busy = false;
		}
	}

	async function setAside(group: FaceGroup) {
		try {
			const aside = await ignoreGroup(group.id);
			// Undoable; permanent means no scan raises it again.
			decided('That group is discarded', aside.decision_id, { after: load });
			await load();
		} catch {
			toasts.show("That group couldn't be discarded", { tone: 'error' });
		}
	}

	async function bringBack(group: FaceGroup) {
		try {
			await restoreGroup(group.id);
			await load();
		} catch {
			toasts.show("That group couldn't be restored", { tone: 'error' });
		}
	}

	function askToDiscard(group: FaceGroup) {
		confirmingAway = group;
		confirmOpen = true;
	}

	function askToRemove(group: FaceGroup) {
		confirmingRemoval = group;
		removeOpen = true;
	}

	/** Forget every face in every picked group; the files are untouched. */
	async function removePicked() {
		busy = true;
		try {
			// What the verb was handed (`acting`).
			const tracks = groups
				.filter((group) => acting.includes(group.id))
				.flatMap((group) => group.faces.map((face) => face.track_id));
			const done = await removeFaces(tracks);
			// No Undo: the server records no decision for a removal.
			decided('Those faces are deleted', null, { after: load });
			announceSkipped(done, 'face');
			selection.clear();
			await load();
		} catch {
			toasts.show("Those faces couldn't be deleted", { tone: 'error' });
		} finally {
			busy = false;
		}
	}

	async function removeGroup(group: FaceGroup) {
		try {
			const done = await removeFaces(group.faces.map((face) => face.track_id));
			decided('Those faces are deleted', null, { after: load });
			announceSkipped(done, 'face');
			await load();
		} catch {
			toasts.show("Those faces couldn't be deleted", { tone: 'error' });
		}
	}

	function faceCount(group: FaceGroup): string {
		return group.size === 1 ? '1 face' : `${counted(group.size)} faces`;
	}

	function groupSays(group: FaceGroup): string {
		if (status !== 'open') return faceCount(group);
		return group.size === 1
			? 'One face, waiting for a name'
			: `${counted(group.size)} faces look like one person`;
	}

	/* The declared pile verbs behind the chevron, each asking its own confirmation. */
	function rarer(group: FaceGroup): Answer[] {
		return pileVerbs({
			setAside: () => askToDiscard(group),
			remove: () => askToRemove(group)
		}).map((verb) => ({
			label: verb.label,
			icon: verb.icon,
			filled: verb.filled,
			destructive: verb.destructive,
			run: () => verb.run?.([group.id])
		}));
	}
</script>

<svelte:window onkeydown={onEscape} />

<!-- The shared bar every wall of tiles uses; the count is of piles. -->
<ActionBar count={picked.length} noun="group" onclear={() => selection.clear()}>
	{#snippet actions()}
		<!-- The same bar on both tabs; naming dimmed and pointing at the card. -->
		<VerbButtons verbs={shape.named} ids={picked} />
	{/snippet}
	{#snippet overflow()}
		<VerbMore verbs={shape.rest} ids={picked} noun="group" />
	{/snippet}
</ActionBar>

<!-- Only while nothing is on screen: a reload keeps what is drawn. -->
{#if !handed && loading && groups.length === 0}
	<Skeleton lines={3} />
{:else if failed}
	<Problem message="Those groups couldn't be loaded." />
{:else if groups.length === 0 && quiet}
	<!-- Nothing: the host draws one empty state for the whole list. -->
{:else if groups.length === 0}
	<Empty scope="block">
		{status === 'open'
			? "Nothing is waiting. Faces that Sift can't place appear here, grouped, so naming somebody is one question rather than hundreds."
			: 'Nothing has been discarded. A group you put here stays listed and can be restored.'}
	</Empty>
{:else}
	<CardWall cards={handed ? measure : paging.cards}>
		{#each groups as group (group.id)}
			{@const offer = status === 'open' ? offers.get(group.id) : undefined}
			{@const made = madeHere.get(group.id)}
			<li>
				<!-- `DecisionCard`'s shape: the crops, "Who is this?", the answers. -->
				{#snippet asked()}
					{#if made}
						<a href={pageOf('person', made.id)}>{made.name}</a> is a person now
					{:else if offer}
						{fingerprintQuestion(offer)}
					{:else}
						Who is this?
					{/if}
				{/snippet}
				{#snippet said()}{groupSays(group)}{/snippet}
				{#snippet answered()}
					{#if made}
						<!-- Answered: the link in the question is the way on. -->
					{:else if offer}
						<Answers
							yes={{
								label: 'Create a person',
								icon: 'add',
								run: () => void makeFromFingerprints(group, offer)
							}}
							rest={[
								{
									label: 'Open this group',
									icon: 'arrow_forward',
									run: () => void goto(pileHref(group, tab))
								},
								...rarer(group)
							]}
							about="this group"
							disabled={busy}
						/>
					{:else if status === 'open'}
						<Answers
							yes={{
								label: 'Add as person',
								icon: 'person_add',
								menu: picker,
								menuLabel: 'Name this group',
								scrolls: false
							}}
							rest={rarer(group)}
							about="this group"
							disabled={busy}
						/>
					{:else}
						<Answers
							yes={{ label: 'Restore', icon: 'history', run: () => void bringBack(group) }}
							about="this group"
							disabled={busy}
						/>
					{/if}
				{/snippet}
				<!--
				`PickMenu` behind the button, portalled, so a right-click there reaches the
				browser's own menu.
				-->
				{#snippet picker()}
					<PickMenu
						label="Add as person"
						icon="person_add"
						kind="person"
						plural="people"
						inline
						ask={askPeople}
						onpick={(choice) => void name(group, choice.id)}
						oncreate={makePerson}
					>
						{#snippet hint(choice)}
							<ReferenceCount personId={choice.id} {strengths} />
						{/snippet}
					</PickMenu>
				{/snippet}
				<!--
				The thumbnails link into the whole pile, Discarded too; the menu answers anywhere on
				the card.
				-->
				<ContextMenu
					label="Actions for this group"
					triggerClass="group-trigger"
					onOpenChange={(isOpen) => {
						if (!isOpen && aimed === group.id) aimed = null;
					}}
				>
					<DecisionCard
						question={status === 'open' ? asked : undefined}
						detail={said}
						answers={answered}
						opens={pileHref(group, tab)}
					>
						<FaceCovers
							faces={group.faces}
							most={CROPS_ON_A_CARD}
							href={pileHref(group, tab)}
							label="Open this group"
							picked={selection.has(group.id) || aimed === group.id}
							oncontextmenu={() => aimAt(group.id)}
							onpointerdown={(event) => gesture.pressStart(group.id, event)}
							onpointerup={() => gesture.pressEnd()}
							onclickcapture={(event) => pressed(group, event)}
							sweepId={group.id}
						/>
					</DecisionCard>

					{#snippet items()}
						<VerbMenuItems ids={menuIds} {verbs} />
					{/snippet}
				</ContextMenu>
			</li>
		{/each}
	</CardWall>
{/if}

<ConfirmDialog
	bind:open={confirmOpen}
	title="Discard this group?"
	consequence={'It moves to Discarded, where it stays listed and can be restored with its ' +
		'faces. Nothing is deleted and no file is touched.'}
	confirmLabel="Discard"
	onconfirm={() => {
		const group = confirmingAway;
		confirmingAway = null;
		if (group) void setAside(group);
	}}
/>

<ConfirmDialog
	bind:open={removeManyOpen}
	title="Delete the faces in these groups?"
	consequence={'The files themselves are untouched, and Sift remembers the decision \u2014 a later ' +
		"scan of the same file won't raise them again, whatever your settings are then."}
	confirmLabel="Delete"
	onconfirm={() => void removePicked()}
/>

<ConfirmDialog
	bind:open={removeOpen}
	title="Delete these faces?"
	consequence={'The files themselves are untouched, and Sift remembers the decision \u2014 a later ' +
		"scan of the same file won't raise these again, whatever your settings are then."}
	confirmLabel="Delete"
	onconfirm={() => {
		const group = confirmingRemoval;
		confirmingRemoval = null;
		if (group) void removeGroup(group);
	}}
/>

<style>
	/* No box of its own, as `DataRow`'s `.row-trigger`. */
	:global(.group-trigger) {
		display: contents;
	}
</style>
