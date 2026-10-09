<script lang="ts">
	/*
	 * The groups of look-alike faces: the ones waiting for a name, and the ones put aside.
	 *
	 * One component for two tabs, because they are one wall of group cards filtered two ways. It
	 * reads which it is drawing off the address, so there is one copy of the card, one empty state
	 * per tab and one place to change either.
	 *
	 * Ignored is a tab rather than a control on the work: a control must be opened before it can be
	 * read, and somebody who thinks they hid something by mistake should find it without going
	 * looking. A tab says its own name.
	 *
	 * The cards are `FaceGroups`, handed this page's rows rather than reading its own, so the
	 * naming picker, the verbs, the selection gesture and the right-click menu are the existing
	 * ones.
	 *
	 * The floor, and the chip on the tab line. Most of a swept library is groups of one or two
	 * people who walked past a camera once, which would bury the real questions, so a group of
	 * fewer than five faces is not listed or counted. How many there are is one chip that opens
	 * them as the same cards; nothing is deleted or unreachable. The chip stays in one spot for
	 * both states, the far end of the tab line (the slot `OnTools` gives every panel), reading "N
	 * small groups" on the way in and "Back to the groups" on the way out.
	 *
	 * Where the page was is carried in the address, so opening a group from a later page and coming
	 * back lands on that page. It pages as every other wall does (`CardPaging`): the first group on
	 * screen is written as `from`, and the server turns it into a place in the list
	 * (`FaceService.position_in_to_check`), because only the server holds the scoped, floored,
	 * ordered list the group is a position in.
	 *
	 * The page is whole rows of the wall: this panel hands its rows to `FaceGroups` to draw, and
	 * hands it this paging's measure for the wall it draws them on.
	 */
	import { page as address } from '$app/state';
	import { onDestroy, untrack } from 'svelte';

	import FaceGroups from '$lib/components/faces/FaceGroups.svelte';
	import { Empty, Skeleton } from '$lib/components/common';
	import type { OnPaging } from '$lib/components/common/Pager.svelte';
	import Chip from '$lib/components/common/Chip.svelte';
	import type { OnTools } from '$lib/components/organize/OrganizeHeader.svelte';
	import { answered } from '$lib/organize/organize.svelte';
	import { anchorIn, rememberAnchor } from '$lib/grid/anchor';
	import { CardPaging } from '$lib/grid/cards.svelte';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import {
		TO_CHECK_PER_PAGE as PAGE,
		toCheck,
		type FaceGroup,
		type ToCheckShow
	} from '$lib/people/faces.svelte';

	/** Where the pager goes: the frame's foot, drawn by the route. See `PagerProps`. And where the
	 *  small-groups chip goes: the far end of the tab line, drawn by the route. See `OnTools`. */
	let { onpaging, ontools }: { onpaging?: OnPaging; ontools?: OnTools } = $props();

	/** The queue this is drawing, which is the one thing that differs between the two tabs. */
	const queue = $derived(address.params.queue ?? '');
	const aside = $derived(queue === 'discarded-faces');

	/* The groups under the floor, opened from the line at the foot of the waiting tab. A query
	   rather than a tab of its own: it is the same cards, and a sixth tab counting strangers would
	   be back to a wall that cannot be worked down. */
	const small = $derived(!aside && address.url.searchParams.get('show') === 'small');
	const showing = $derived<ToCheckShow>(aside ? 'ignored' : small ? 'small' : 'waiting');

	let groups = $state<FaceGroup[]>([]);
	let total = $state(0);
	let underFloor = $state(0);
	let loading = $state(true);
	const paging = new CardPaging(PAGE, 'faces.unnamed');

	async function load() {
		const asked = showing;
		/* The address this read's anchor may be written onto, taken as the read starts. One instance
		   of this panel draws both of its tabs, and the router does not remount it between them, so
		   a fixed path would refuse the second tab's anchor, and a path read after the answer could
		   write the first tab's group onto the second tab's address. See `rememberAnchor`. */
		const path = address.url.pathname;
		const page = await paging.fill(
			asked,
			() => groups,
			(query) => {
				loading = true;
				return toCheck(query, asked, aside ? undefined : 'group');
			},
			(answer) => ({
				rows: answer.items.map((one): FaceGroup => ({
					id: one.id,
					status: one.status ?? (aside ? 'ignored' : 'open'),
					size: one.size,
					faces: one.faces
				})),
				total: answer.total,
				offset: answer.offset
			})
		);
		// Overtaken by a newer read, which finishes this one's work.
		if (page === null) return;
		groups = page.rows;
		total = page.total;
		// A landing answers from the rows held and brings no answer: the count under the floor is
		// then the one already on screen.
		if (page.answer) underFloor = page.answer.small_groups;
		/* In the SAME synchronous turn as the rows. See `CardPaging.land`. Moved after an await,
		   the effect watching the offset re-runs with the old rows in hand and asks again for the
		   page it was just handed. */
		paging.land(page.offset);
		rememberAnchor(address.url, path, groups[0]?.id, page.offset);
		loading = false;
	}

	/* Arrive on the address's anchor whenever the filtering is (re)entered (the first visit, the
	   chip into the small groups, and Back out of them) and page from there. A page of the groups
	   above the floor is not a page of the ones under it, so an offset carried across a filter
	   lands past the end; the anchor in the address, when there is one, is a group of THIS
	   filter, because the address it was written onto is this filter's. */
	let arrivedFor: ToCheckShow | null = null;
	$effect(() => {
		const now = showing;
		if (now !== arrivedFor) {
			arrivedFor = now;
			// UNTRACKED: this effect's own answer writes the address, and reading it plainly would
			// make the effect depend on what it causes. Arrived BEFORE the offset is read below, so
			// the move to the top is part of this run rather than a reason for another.
			untrack(() => paging.arrive(anchorIn(address.url)));
		}
		void paging.offset;
		void paging.size;
		void answered.stamp;
		/* The load UNTRACKED, as on every wall: `fill` reads the anchor before its first await, and
		   tracked, `land` clearing it would re-run this and ask again for the page just landed. */
		untrack(() => void load());
	});

	reloadOnLibraryChange(() => void load());

	$effect(() => {
		/* The small groups say so in the foot too: the chip on the tab line says the way back, and
		   this says where you are. */
		onpaging?.(
			paging.asPager(
				groups.length,
				total,
				/* Nouns, as `PagerProps.noun` asks: without one an empty list reads "No to name". */
				aside ? 'discarded groups' : small ? 'small groups' : 'unnamed groups'
			)
		);
	});
	onDestroy(() => onpaging?.(null));

	/* The chip, reported to the tab line, or nothing, on Discarded and while there are no small
	   groups to offer. Reported again whenever what it would say changes, and taken back as this
	   panel goes so the next queue on the screen does not wear it. */
	$effect(() => {
		ontools?.(small || (showing === 'waiting' && underFloor > 0) ? floorChip : null);
	});
	onDestroy(() => ontools?.(null));

	/**
	 * The chip's words on the way in: the number and what they are, and nothing about who they
	 * might be.
	 */
	const smallLine = $derived(
		underFloor === 1 ? '1 small group' : `${underFloor.toLocaleString()} small groups`
	);
</script>

<!-- ONE chip for both states, at the far end of the tab line (see the note at the top). A link
     rather than a button: it GOES somewhere: the filtering is an address, so it survives a
     refresh and a shared link, and the chip carries the middle-click and open-in-a-new-tab that
     come with an anchor. Square, because it is a filter over the list rather than a label on it. -->
{#snippet floorChip()}
	{#if small}
		<Chip shape="square" tone="quiet" icon="arrow_back" href="/organize/faces-to-name"
			>Back to the groups</Chip
		>
	{:else}
		<Chip shape="square" tone="quiet" href="/organize/faces-to-name?show=small">{smallLine}</Chip>
	{/if}
{/snippet}

<section class="panel">
	<!-- Only while there is nothing on screen yet. See `SuggestionsPanel` for why a reload keeps
	     what is drawn. -->
	{#if loading && groups.length === 0}
		<Skeleton lines={3} />
	{:else if groups.length === 0}
		<!-- The whole tab, so the page's empty state: a line saying what is absent, then what
		     would put something here. -->
		<Empty
			scope="page"
			icon="group"
			title={aside ? 'Nothing discarded' : small ? 'No small groups' : 'No unnamed faces'}
		>
			{#if aside}
				A group you discard stays listed here, and you can restore it.
			{:else if small}
				A group of fewer than five faces is kept here, so the list of unnamed groups stays the
				questions worth answering.
			{:else}
				Faces Sift doesn't recognize appear here in groups, so you name a person once rather than
				hundreds of times.
			{/if}
		</Empty>
	{:else}
		<!-- `tab` is this queue's own name, so a group opened from here can find its way back to
		     here rather than to the tab the group screen is registered under. See `pileHref`. -->
		<FaceGroups
			status={aside ? 'ignored' : 'open'}
			supplied={groups}
			onreload={load}
			tab={queue}
			measure={paging.cards}
			quiet
		/>
	{/if}
</section>

<style>
	.panel {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}
</style>
