<script lang="ts">
	/* The groups of look-alike faces waiting for a name, and those put aside: one wall for two tabs,
	 * read off the address. Groups under five faces are left out behind one chip at the tab line's
	 * end; the page is kept in the address (`from`), placed by the server. */
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

	/** Where the pager and the small-groups chip go, both drawn by the route. */
	let { onpaging, ontools }: { onpaging?: OnPaging; ontools?: OnTools } = $props();

	/** The queue this is drawing, which is the one thing that differs between the two tabs. */
	const queue = $derived(address.params.queue ?? '');
	const aside = $derived(queue === 'discarded-faces');

	/* The groups under the floor, a query rather than a tab of its own. */
	const small = $derived(!aside && address.url.searchParams.get('show') === 'small');
	const showing = $derived<ToCheckShow>(aside ? 'ignored' : small ? 'small' : 'waiting');

	let groups = $state<FaceGroup[]>([]);
	let total = $state(0);
	let underFloor = $state(0);
	let loading = $state(true);
	const paging = new CardPaging(PAGE, 'faces.unnamed');

	async function load() {
		const asked = showing;
		/*
		 * Taken as the read starts: one instance draws both tabs, so the anchor lands on the right
		 * one.
		 */
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
		// A landing brings no answer, so the count on screen stands.
		if (page.answer) underFloor = page.answer.small_groups;
		/* In the same synchronous turn as the rows (`CardPaging.land`). */
		paging.land(page.offset);
		rememberAnchor(address.url, path, groups[0]?.id, page.offset);
		loading = false;
	}

	/*
	 * Arrive on the address's anchor whenever the filter is entered; an offset cannot cross
	 * filters.
	 */
	let arrivedFor: ToCheckShow | null = null;
	$effect(() => {
		const now = showing;
		if (now !== arrivedFor) {
			arrivedFor = now;
			// Untracked: its answer writes the address; arrived before the offset is read.
			untrack(() => paging.arrive(anchorIn(address.url)));
		}
		void paging.offset;
		void paging.size;
		void answered.stamp;
		/* Untracked, as on every wall, or `land` would re-run it. */
		untrack(() => void load());
	});

	reloadOnLibraryChange(() => void load());

	$effect(() => {
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

	/* The chip, reported to the tab line, and taken back as the panel goes. */
	$effect(() => {
		ontools?.(small || (showing === 'waiting' && underFloor > 0) ? floorChip : null);
	});
	onDestroy(() => ontools?.(null));

	/** The chip's words: the number and what they are. */
	const smallLine = $derived(
		underFloor === 1 ? '1 small group' : `${underFloor.toLocaleString()} small groups`
	);
</script>

<!-- One chip for both states; a link, so the filter survives a refresh and a shared link. -->
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
	<!-- Only while nothing is on screen yet. -->
	{#if loading && groups.length === 0}
		<Skeleton lines={3} />
	{:else if groups.length === 0}
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
		<!-- `tab` is this queue's name, so an opened group finds its way back here. -->
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
