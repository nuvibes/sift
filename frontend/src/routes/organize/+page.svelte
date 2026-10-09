<script lang="ts">
	/* Organize: what needs you. It shows only what is pending, and is finished when it is empty. */

	import { ApiError } from '$lib/api/client';
	import { Empty, Problem, SettingLink, Skeleton } from '$lib/components/common';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import { screenBar } from '$lib/components/shell/screen-bar.svelte';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import BoardCard from '$lib/components/organize/BoardCard.svelte';
	import CardWall from '$lib/components/organize/CardWall.svelte';
	import { bandsOf } from '$lib/organize/bands';
	import { heldBoard, type Board, answered } from '$lib/organize/organize.svelte';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import { session } from '$lib/shell/session.svelte';

	/* Held in a store, so coming back to this screen draws the last board immediately. */
	const found = $derived<Board | null>(heldBoard.found);
	let failed = $state(false);
	/* Refused, apart from failed: a guest's 403 is not worth trying again. */
	let refused = $state(false);

	/* Whether the answer has ARRIVED, held apart from whether it was empty. */
	const ready = $derived(found !== null || failed || refused);
	const queues = $derived(found?.queues ?? []);
	/* The cards: every pile, in the order their bands give, with the records left out
	   (`bandsOf`). */
	const cards = $derived(bandsOf(queues).flatMap((band) => band.cards));
	/* What the empty board says after its first words: what would put something here. */
	const EMPTY_SENTENCE =
		'Sift has done everything it can for you. New questions appear here as files are imported, ' +
		'like a folder named after a person or a group of faces that may be one person.';
	/* Whether anything is asking. */
	const nothingWaiting = $derived(
		queues.every((one) => one.band !== 'decision' || one.count === 0)
	);

	/* What this screen offers the bar above it: nothing, said in its own words. */
	const mine = Symbol('organize-board');

	$effect(() => {
		screenBar.publish(mine, {
			filterable: 'This screen lists things to review, not files',
			resizable: 'A face is cut to one size here',
			playable: "A face is a still, and a still doesn't play"
		});
	});

	$effect(() => () => screenBar.release(mine));

	async function load() {
		try {
			await heldBoard.refresh();
			failed = false;
			refused = false;
		} catch (error) {
			if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
				refused = true;
			} else {
				failed = true;
			}
		}
	}

	/* And again whenever anything is decided. */
	$effect(() => {
		answered.stamp;
		void load();
	});

	/* And again whenever a share or a restrict moves. */
	reloadOnLibraryChange(() => void load());
</script>

<svelte:head><title>Organize</title></svelte:head>

<!--
	No `measure`: the board is a wall, so a wide screen gets more columns rather than wider margins.
-->
<PageFrame>
	{#snippet header()}
		<!--
			No lede: every card carries its OWN sentence saying what its pile is for (`Queue.purpose`).
		-->
		<!--
			The way to what was decided here: History, showing Decisions, where each line keeps its
			Undo.
		-->
		{#snippet controls()}
			<SettingLink section="tasks" setting="activity.decisions">Decisions</SettingLink>
		{/snippet}
		<PageHeader title="Organize" icon="inbox" controls={session.isAdmin ? controls : undefined} />
	{/snippet}
	{#if !ready}
		<Skeleton lines={3} />
	{:else if refused}
		<p class="quiet">This screen is for the administrator of this library.</p>
	{:else if failed}
		<Problem message="That couldn't be loaded. Try again in a moment." />
	{:else}
		{#if nothingWaiting}
			<!--
				The goal state: this screen is meant to be empty most of the time, so this reads as
				success.
			-->
			{#if cards.length === 0}
				<Empty scope="page" icon="inbox" title="Nothing to review">{EMPTY_SENTENCE}</Empty>
			{:else}
				<Empty scope="block">Nothing needs you right now. {EMPTY_SENTENCE}</Empty>
			{/if}
		{/if}

		{#if cards.length > 0}
			<!-- ONE WALL: the bands ORDER the cards, with no headings between them. -->
			<CardWall label="Organize" board>
				{#each cards as card (card.lead.name)}
					<li><BoardCard {card} /></li>
				{/each}
			</CardWall>
		{/if}
	{/if}
</PageFrame>
