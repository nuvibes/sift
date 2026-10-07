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
	/*
	 * The piles of faces nobody has named, and the piles somebody discarded.
	 *
	 * One component under two headings, because they are one list under two statuses, and two
	 * components would be two chances to scope, count or draw them differently.
	 *
	 * **The point of the screen is that naming somebody is one question instead of hundreds.** Forty
	 * sightings of the same stranger are one pile and one prompt. So the unit here is the pile, not
	 * the face: the faces inside one are a handful of views of the same person, shown so somebody
	 * can tell who it is, and the decision applies to all of them.
	 *
	 * **Discarding a pile is not a delete.** It moves to Discarded, stays listed, and comes back
	 * with its faces: a trapdoor is a control nobody dares use.
	 *
	 * Every number here is the server's. A pile's size is how much of it this account may see, and
	 * a pile they may see none of never arrives. Recomputing any of that in the browser would be a
	 * second opinion about concealment living in the one place it can be read.
	 */
	import type { OnPaging } from '$lib/components/common/Pager.svelte';
	import { onDestroy } from 'svelte';
	import { page as address } from '$app/state';
	import { anchorIn, rememberAnchor } from '$lib/grid/anchor';
	import { CardPaging } from '$lib/grid/cards.svelte';
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
		/** Where the pager goes: the frame's foot, drawn by the route. See `PagerProps`. */
		onpaging?: OnPaging;
		/**
		 * The groups to draw, when somebody else has already read them.
		 *
		 * A second mode rather than a second component. The review list is one list of two kinds of
		 * item (a person's proposals, then the groups), paged by one offset over one read, so the
		 * groups on a page arrive with the people above them. What is needed here is this wall's
		 * cards: the naming picker, set-aside and restore, the selection gesture, the right-click
		 * menu, the reference hints. A second wall drawing the same card would be a second place
		 * for all of that.
		 *
		 * So the fetching half is what becomes optional, never the drawing half. Left undefined
		 * this reads and pages as normal (the pile screens use that); handed a list it draws what
		 * it was given, reports no pager of its own, and asks its host to read again whenever a
		 * decision here changes what belongs on screen.
		 */
		supplied?: FaceGroup[] | null;
		/** Read again, for the host of a supplied list. Ignored when this reads for itself. */
		onreload?: () => Promise<void> | void;
		/** Say nothing when there is nothing, because the host draws the empty state for the whole
		 *  list rather than for the groups half of it. */
		quiet?: boolean;
		/**
		 * Which tab this wall is being drawn on, so a group carries it into the screen it opens.
		 *
		 * One screen draws a group however it was reached, so it cannot know which tab somebody
		 * left. See `pileHref`. Absent where this wall is not a tab of anything (a file's own
		 * strip of faces), and the group's own queue answers then.
		 */
		tab?: string;
	}

	let { status, onpaging, supplied = null, onreload, quiet = false, tab }: Props = $props();

	/** Whether the list is somebody else's. One reading, so no branch can disagree with another. */
	const handed = $derived(supplied !== null);

	$effect(() => {
		// A handed list is a page of somebody else's list, so the pager over it is theirs too:
		// reporting one here would put two pagers in the frame's foot, counting different things.
		if (!handed) onpaging?.(paging.asPager(groups.length, total, 'groups'));
	});
	onDestroy(() => onpaging?.(null));

	let fetched = $state<FaceGroup[]>([]);
	const groups = $derived(supplied ?? fetched);
	let loading = $state(true);
	let failed = $state(false);

	/*
	 * Paged, because a swept library has a pile for every face that joined nothing, and every pile
	 * costs the server a query for its faces and a question to the resolver about who may see them.
	 *
	 * By whole rows of the window rather than a fixed count, and by position rather than a page
	 * number, so this wall behaves the way every other paged view in Sift does. A fixed count
	 * overflows a small laptop and leaves a large monitor half empty.
	 */
	const paging = new CardPaging(PILES_PER_PAGE, 'faces.groups');
	let total = $state(0);
	let busy = $state(false);
	let confirmingAway = $state<FaceGroup | null>(null);
	let confirmOpen = $state(false);
	/* Removing a group, which is the other thing somebody wants from this wall and the one that
	   does not come back. Its own pair of variables rather than a mode on the ignore dialog: the
	   two say different things and share no wording, and one dialog that means two things is how
	   a permanent action gets confirmed with a sentence describing a reversible one. */
	let confirmingRemoval = $state<FaceGroup | null>(null);
	let removeOpen = $state(false);
	/** The bar's Delete, which acts on everything picked rather than on one card. */
	let removeManyOpen = $state(false);
	/** The card a right-click was aimed at, while its menu is open. Not a selection. See `aimAt`. */
	let aimed = $state<string | null>(null);
	/*
	 * What the verb somebody chose is ABOUT, recorded when they choose it.
	 *
	 * These verbs open a question and the answer arrives later; reading the selection at that point
	 * reads it at the wrong moment, because a right-click aims without picking. The component that
	 * drew the verb hands it the ids, and that is the one thing that certainly knows.
	 */
	let acting = $state<string[]>([]);
	/* Read once for the screen rather than per candidate. A picker asking as you type would be one
	   request per name per keystroke, for a hint. */
	let strengths = $state<ReferenceStrengths | null>(null);

	/*
	 * WHO THIS COULD BE, as one page of the library's people.
	 *
	 * From the server and not from `people.items`: that is one page of the WALL, in the wall's own
	 * order, so somebody past it could not be found by typing their name at all. `total` comes back
	 * with the page so the picker can say how many it is not showing: a list that asked for six
	 * and drew six would leave a seventh match not existing as far as this card was concerned.
	 */
	async function askPeople(typed: string): Promise<PickPage> {
		const asked = await people.choices(typed);
		return {
			/*
			 * Drawn by their face, as the "Add to" flyout and the People wall draw a person: one
			 * rule, in `$lib/people/person-row`, rather than a name here and a picture there.
			 */
			choices: asked.items.map(personRow),
			more: Math.max(0, asked.total - asked.items.length)
		};
	}

	/*
	 * Where this wall was left, carried in the address; see `$lib/grid/anchor`.
	 *
	 * `path` is captured once so a background refresh cannot rewrite the address after somebody has
	 * navigated away, and `arriving` is true exactly once: after the first settle the anchor in the
	 * address is one this screen wrote, and honouring it again would start a new question at the
	 * old one's position.
	 */
	const path = address.url.pathname;
	let arriving = true;

	/**
	 * Read the address once, then keep it in step with where the page actually landed.
	 *
	 * Through `land`, never a plain `paging.offset = at`, and in the same synchronous step as the
	 * rows are written (every caller writes them just before calling this). An anchored page is
	 * found by a row and only its answer says what offset that row is at, so the offset moves after
	 * the rows land; moved plainly, the effect watching it would ask again for the page it was just
	 * handed. Landed, the next `fill` answers from the rows held. See `CardPaging.land`.
	 */
	function settle(at: number, first: string | null | undefined) {
		paging.land(at);
		rememberAnchor(address.url, path, first, at);
	}

	/* Read again: ours, or whoever handed the list over.
	 *
	 * One function because every verb on this wall calls it after it writes, and those verbs do not
	 * know or care where the rows came from. The host's re-read is what brings the counts, the
	 * order and the line at the foot of the list back into step, which a re-read of the groups
	 * alone could not do. */
	async function load() {
		if (handed) {
			await onreload?.();
			return;
		}
		failed = false;
		let overtaken = false;
		try {
			// Through `fill`, so a re-size trims the groups held or asks for the rest only.
			const asked = status;
			const page = await paging.fill(
				asked,
				() => fetched,
				(query) => {
					// "Looking..." only when a request goes out: a landing or a trim asks nothing.
					loading = true;
					return faceGroups(asked, query);
				},
				(answer) => ({ rows: answer.groups, total: answer.total, offset: answer.offset })
			);
			// Overtaken by a newer read, which finishes this one's work: the loading line too.
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

	/* Back to the beginning when the status changes. Landing deep inside Discarded because that is
	   where you were in Unidentified is a screen that looks empty for no reason anybody can see. */
	$effect(() => {
		void status;
		untrack(() => {
			paging.offset = 0;
		});
	});

	/* `paging.size` is read as well, and that is what makes a resize work: a taller window holds
	   more rows, so the page has to be re-fetched at the new size rather than merely re-flowed. */
	$effect(() => {
		void status;
		void paging.offset;
		void paging.size;
		// A handed list is read by its host, and reading it here as well would be two requests for
		// one screen, and this one would page by an offset that means nothing in their list.
		if (handed) return;
		if (arriving) {
			arriving = false;
			// UNTRACKED: this effect's own answer writes the address, and reading it here plainly
			// would make the effect depend on what it causes: the anchor written and deleted twice,
			// settling with nothing.
			paging.arrive(untrack(() => anchorIn(address.url)));
		}
		/* The load UNTRACKED: its dependencies are the ones named above. `fill` reads the paging's
		   anchor before its first await, and tracked, `land` clearing that anchor would re-run this
		   effect and ask again for the page just landed whenever the row resolved to the page
		   already open. */
		untrack(() => void load());
	});

	/* The two things read once for the screen, each in an effect of its own.
	 *
	 * Both are guarded by a condition that READS the state the work then WRITES (`!strengths`,
	 * `!people.loaded`) so the answer arriving is a change the effect is watching, and it runs
	 * again. On its own that is harmless; sharing an effect with the load above would fetch the
	 * whole pile list a second time a beat after arriving, for nothing.
	 *
	 * The catch is not decoration either. A hint that fails is a hint nobody gets, not an unhandled
	 * rejection in the console of a screen that is otherwise fine. */
	/* `loaded` is the dependency; `loading` is checked inside `untrack` and is not one.
	 *
	 * Both tracked, this loops the moment the server refuses: the request fails, `loading` goes
	 * back to false, `loaded` never goes true, and the effect, which was watching `loading`,
	 * runs again and asks again, as fast as the machine can go. Watching `loaded` alone cannot:
	 * on success it goes true, this runs once more and returns; on failure nothing it depends on
	 * moved. `loading` still guards, so two screens mounted together do not both fetch. */
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
	 * The groups that look like somebody a facial fingerprints file holds, while making people from
	 * fingerprints is off: such a group asks whether to make them a person instead of "Who is this?".
	 *
	 * Keyed by the group and read beside the groups rather than inside them, so a card asks only
	 * about a group it already draws, and an account the server answers nothing for asks as before.
	 * Read on the open tab only: a discarded group is asked nothing.
	 */
	let offers = $state<Map<string, FingerprintOffer>>(new Map());
	/* The person each Yes made, by group, so the card says who and links to them until the
	   matching that follows names the group's faces and the group leaves the wall. */
	let madeHere = $state<Map<string, { id: string; name: string }>>(new Map());

	async function readOffers() {
		offers = status === 'open' ? await fingerprintOffers() : new Map();
	}

	$effect(() => {
		void status;
		untrack(() => void readOffers());
	});

	/* A share or a restrict moving changes which files this account may see, which changes how big
	 * every pile is and whether some of them belong on screen at all, and nothing is imported to
	 * announce it. See the helper. */
	/* A handed list is reloaded by its host on the same announcement, so subscribing here too would
	   read the whole list twice for one share moving. The offers are this wall's own either way. */
	reloadOnLibraryChange(() => {
		void readOffers();
		if (!handed) void load();
	});

	/* The Yes to a group's fingerprints question. The person, their History line and its Undo are
	   the server's; this says who was made and links to them. */
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

	/*
	 * Naming a pile: every face in it is confirmed as that person, and the rest of the pile
	 * offered.
	 *
	 * All of them rather than the clearest one, which is the point of a pile: each face is a
	 * separate sighting, and every one becomes a reference, which is how matching improves from
	 * this library's own pictures.
	 *
	 * One write, with the whole group: `/faces/name` names many faces in one request (the route
	 * runs `confirm_many`), rather than one request per face queueing on the same person's gallery.
	 * A face this account's own locked vault conceals is skipped and counted rather than failing
	 * part-way through, and `whole_group` finishes the pile on the server, because a card draws
	 * only a page of a pile. `nameFaces` says a whole group's own card should ask for this.
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

	/*
	 * Somebody the library has never heard of, made so the pick above can name the pile as them.
	 *
	 * The picker's contract is "create one and pick it", so this creates and the naming is the
	 * ordinary pick path afterwards, which is what makes creating somebody here the same act it is
	 * in every other picker in the application, recorded in what this account reaches for and named
	 * with the same whole-group write as anybody who already existed.
	 *
	 * What it gives up is the server's own name resolution: `/faces/name` resolves a typed name
	 * against existing people before creating one, and `POST /people` deliberately does not, because
	 * two people may share a name. The guard that replaces it is structural rather than a second
	 * copy of that lookup written here: the picker offers its create row only when no row on the
	 * page is called that, and the page is the server's own answer for what was typed.
	 */
	async function makePerson(named: string): Promise<PickChoice> {
		const made = await people.create(named);
		return { id: made.id, name: made.name };
	}

	/* Picking several piles together, by the same gesture every other wall of tiles uses.
	 *
	 * Without it, holding a card would open it, so the gesture people have learned everywhere else
	 * in the app (and in every photo app on a phone) would do the one thing it does not do
	 * anywhere else, and ignoring twenty piles would be twenty presses of twenty separate buttons.
	 *
	 * The shared gesture, not a second copy of it: a long press starts a selection, and from then on
	 * a plain click adds and removes, because something being selected IS the mode. The card is a
	 * link, so the click has to be caught on the way DOWN and stopped: otherwise every press of a
	 * selection navigates away from the selection.
	 */
	const selection = new Selection();
	const selecting = $derived(selection.count > 0);
	const gesture = new TileGesture(selection, () => groups.map((group) => group.id));
	const picked = $derived(selection.ordered(groups.map((group) => group.id)));

	/*
	 * What can be done to the groups picked, declared once for the bar and for the menu on a card.
	 *
	 * Both answers about a grouping are always built and one of them is dim, this screen's own
	 * decision: the bar must not change under somebody switching tabs, and a missing control reads
	 * as a feature that does not exist where a greyed one reads as one that does not apply here.
	 *
	 * Naming is the fourth row, with no handler, because it needs a name typed against one group,
	 * and that field lives on the card. Offered, dim, and saying where to do it through the verb's
	 * `why`.
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
	/* The bar's two halves, split by the shared rule rather than listed here. The right-click menu
	   draws `verbs` whole, which is what keeps the two surfaces the same list. */
	const shape = $derived(barShape(verbs));

	/* Right-clicking a group that is not picked picks it, the same rule the
	   walls of faces inside these groups use. */
	function aimAt(groupId: string) {
		aimed = selection.has(groupId) ? null : groupId;
	}

	/** What the MENU acts on: the aimed card, or the whole selection when opened inside it. */
	const menuIds = $derived(aimed ? [aimed] : picked);

	/* Let go of a selection whenever the wall underneath it changes. A page turn or a switch between
	   Unidentified and Discarded leaves ids picked that are no longer on screen, and the bar then
	   counts things nobody can see. */
	$effect(() => {
		void status;
		void paging.offset;
		selection.clear();
	});

	function pressed(group: FaceGroup, event: MouseEvent) {
		gesture.clicked(group.id, event);
	}

	function onEscape(event: KeyboardEvent) {
		// Ctrl+Z takes back the last thing PICKED, Ctrl+Shift+Z picks it again. It touches no
		// data and never reaches the server. See `TileGesture.undoKeys`.
		if (gesture.undoKeys(event)) {
			event.preventDefault();
			event.stopPropagation();
			return;
		}
		if (event.key !== 'Escape') return;
		if (gesture.escaped(event)) event.stopPropagation();
	}

	/** Discard or bring back what the verb was handed, one call each, then let go and re-read. */
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
			// Undoable, from the toast and from the record on the board. Permanent means no scan ever
			// raises it again; it never meant a mis-click cannot be corrected, and those are two
			// different promises.
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

	/* Forget every face in the group. The files are untouched: what goes is Sift's record of
	   having seen a face there, which is why this is offered beside Discard rather than instead of
	   it: discarding keeps the group and stops being asked about it, deleting gets rid of the
	   detections themselves. */
	/** Forget every face in every picked group, then let go and re-read. */
	async function removePicked() {
		busy = true;
		try {
			// What the verb was handed, not what is picked. A right-click aims at a card without
			// picking it, so re-reading the selection here would act on nothing, or on something
			// else. See `acting`.
			const tracks = groups
				.filter((group) => acting.includes(group.id))
				.flatMap((group) => group.faces.map((face) => face.track_id));
			const done = await removeFaces(tracks);
			// No Undo, and that is the server's ruling rather than an omission: removing a face is a
			// change to the library, so /faces/remove records no decision to take back.
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

	/* The line under the question, in the words the board asks it with, so a group reads the same
	   on the board and on its card. A discarded group is asked nothing, so it says its size. */
	function groupSays(group: FaceGroup): string {
		if (status !== 'open') return faceCount(group);
		return group.size === 1
			? 'One face, waiting for a name'
			: `${counted(group.size)} faces look like one person`;
	}

	/*
	 * A card's rarer answers, behind its chevron: the declared pile verbs, so a word reworded on the
	 * bar is reworded here too (`faces/verbs.ts`, held by `verbs.test.ts`). Each still asks its own
	 * confirmation: `pileVerbs` says what the rows are, and this says what pressing one does to this
	 * card's group.
	 */
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

<!-- What says a selection is on, and the way out of it. The count is of piles rather than faces:
     the pile is what the actions here run over. -->
<!-- The same bar every other wall of tiles uses, not one of its own: a strip of its own above the
     grid, while the rest of the app floats a pill up from the bottom, would make the one gesture
     people have learned produce something unlike the thing they learned it for. -->
<ActionBar count={picked.length} noun="group" onclear={() => selection.clear()}>
	{#snippet actions()}
		<!--
			One bar, the same on both tabs, with what does not apply here disabled rather than
			absent: a missing control reads as a feature that does not exist, a greyed one as one
			that does not apply to what is picked.

			Naming a group needs a name typed against one group, so it lives on the card; it is
			here, disabled, saying where to do it.

			"Add as person" and Delete keep their words, since one is what this wall is for and the
			other destroys something; Discard and Restore, which only ever half apply, are one press
			away at the end. The shape is the same on both tabs either way.
		-->
		<VerbButtons verbs={shape.named} ids={picked} />
	{/snippet}
	{#snippet overflow()}
		<VerbMore verbs={shape.rest} ids={picked} noun="group" />
	{/snippet}
</ActionBar>

<!-- Only while there is nothing on screen yet. A reload after a decision keeps what is
     already drawn and swaps it when the answer arrives; showing the skeleton again makes the
     page blink out and back for every single answer, which is the one thing somebody working
     through a queue does over and over. `EntityGrid` has always done it this way. -->
{#if !handed && loading && groups.length === 0}
	<Skeleton lines={3} />
{:else if failed}
	<Problem message="Those groups couldn't be loaded." />
{:else if groups.length === 0 && quiet}
	<!-- Nothing at all: the host of a handed list draws one empty state for the whole of it, and a
	     second one under it would say the screen is empty while there are cards above. -->
{:else if groups.length === 0}
	<Empty scope="block">
		{status === 'open'
			? "Nothing is waiting. Faces that Sift can't place appear here, grouped, so naming somebody is one question rather than hundreds."
			: 'Nothing has been discarded. A group you put here stays listed and can be restored.'}
	</Empty>
{:else}
	<ul class="groups" {@attach paging.cards}>
		{#each groups as group (group.id)}
			{@const offer = status === 'open' ? offers.get(group.id) : undefined}
			{@const made = madeHere.get(group.id)}
			<li>
				<!-- The card asks in the one shape every Organize question wears (`DecisionCard`):
				     the crops, then "Who is this?" with the line the board says under it, then the
				     answers. Naming is the button and opens the picker; Discard and Delete are behind
				     its chevron, each asking its own confirmation. A discarded group has no question
				     left, only the way back. -->
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
						<!-- Answered: the link in the question is the way on, and nothing is left to press. -->
					{:else if offer}
						<!-- One press makes them. Naming the group as somebody else is on its own page,
						     opened from the chevron, where the whole group can be looked at first. -->
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
					The same selector the "Add to" flyouts are, behind the button: `PickMenu` pages the
					whole library, says how many it is not showing, remembers who this account reaches
					for, and creates from a row worded like every other picker's create row.

					The box is portalled to the end of the document by the door that opens it (see
					`MenuButton`), so it is outside the card's context-menu subtree and a right-click on
					it still reaches the browser's own menu, the one that can paste.
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
							<!-- How many reference photos this person already has, which is what decides
							     between two names that read identically. See `ReferenceCount`. -->
							<ReferenceCount personId={choice.id} {strengths} />
						{/snippet}
					</PickMenu>
				{/snippet}
				<!-- The thumbnails link into the whole pile: a card shows a handful, and a decision
				     often needs all of them, since the grouping is tuned to split rather than merge,
				     so a pile is usually one person and occasionally one person plus a stranger.

				     Discarded links too: this is the wall you come to in order to reconsider
				     something, so what was set aside must be looked at, not only counted. Same screen
				     either way; it reads the pile's own status and offers bringing it back instead of
				     setting it aside.

				     The menu answers anywhere on the card, the question and the answers included, so
				     a right-click there does not hand back the browser's menu ("Save image as" over a
				     card). The trigger takes no box of its own, so the card's layout is unchanged. -->
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
	</ul>
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
	.groups {
		display: grid;
		/*
		 * Wide enough for four crops (4 x 3.25rem + 3 gaps of 0.25rem, plus the card's 0.75rem
		 * inset either side = 15.25rem). The crops grow to fill whatever width the column ends up
		 * with (`FaceCovers`), so a wider column means wider crops or a fifth one, never a strip of
		 * ground.
		 */
		grid-template-columns: repeat(auto-fill, minmax(15.25rem, 1fr));
		gap: var(--space-3);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	/* The trigger takes no box of its own: the card underneath is what somebody sees and what the
	   wall lays out, and a wrapper with a size would put a second block between the two. The same
	   rule, and the same reason, as `DataRow`'s `.row-trigger`. Named for this file alone, as every
	   caller of `ContextMenu` names its own trigger, so a rule here dresses no other file's. */
	:global(.group-trigger) {
		display: contents;
	}

	/* The card fills its row, so the question and answers at its foot share one line across the
	   row however many crops each card holds. */
	.groups > li {
		display: grid;
	}
</style>
