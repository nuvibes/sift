<script lang="ts">
	/* WHY NOT BITS-UI: there is no card in it, and there is nothing for one to do. This is a
	surface with a gap and a corner; every behaviour a card carries is its caller's. */
	/* One card on an Organize wall, the box written once. A card asking something hands over its
	 * question, detail and answers, drawn alike at the foot; `opens` makes the whole card a press.
	 * */
	import type { Snippet } from 'svelte';
	import { pressOnCard, type CardOpens } from '$lib/components/common/card-press';
	import Panel from '$lib/components/common/Panel.svelte';
	import SectionHeading from '$lib/components/common/SectionHeading.svelte';

	interface Props {
		children: Snippet;
		/** Drawn as one that is not settled: a chain nothing can be done with here. Dashed. */
		unsettled?: boolean;
		/** What the card asks, as one sentence. Drawn as the card's heading, at its foot. */
		question?: Snippet;
		/** The line under the question: where, how many, how sure. */
		detail?: Snippet;
		/** The answers, drawn at the end of the card's last line. Usually `Answers`. */
		answers?: Snippet;
		/** Lines kept for the detail, so a row's questions sit at one height. */
		detailLines?: number;
		/** What a press on the card does, where it opens something. See the header. */
		opens?: CardOpens;
	}

	let {
		children,
		unsettled = false,
		question,
		detail,
		answers,
		detailLines = 0,
		opens
	}: Props = $props();
</script>

<!-- The box is Panel's; this file owns the flow and the unsettled look. -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<!-- svelte-ignore a11y_click_events_have_key_events: the keyboard reaches what this opens through
the card's own button or link. -->
<div
	class="card"
	class:unsettled
	class:opens={opens !== undefined}
	onclick={(event) => pressOnCard(event, opens)}
>
	<!-- No edge: a card on a wall is bounded by its ground. -->
	<Panel inset="md" corner="lg" edge={false} gap="sm">
		{@render children()}
		{#if question || answers}
			<!-- The foot, pushed down so a row's questions and buttons line up. -->
			<div class="foot">
				{#if question}
					<SectionHeading band>{@render question()}</SectionHeading>
				{/if}
				{#if detail}
					<p class="detail" style:--detail-lines={detailLines || null}>{@render detail()}</p>
				{/if}
				{@render answers?.()}
			</div>
		{/if}
	</Panel>
</div>

<style>
	.card {
		display: grid;
		min-inline-size: 0;
		/* Inside a card, buttons sit at the start of their line, under the words they answer. */
		--card-actions-justify: flex-start;
		--card-actions-push: 0;
	}

	/* A column, not Panel's grid: a stretched grid shares the free space between rows. */
	.card :global(.panel) {
		display: flex;
		flex-direction: column;
		transition: outline-color var(--dur-instant) var(--ease);
	}

	/* One auto margin for the whole block. */
	.foot {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		margin-block-start: auto;
		min-inline-size: 0;
	}

	.detail {
		margin: 0;
		min-block-size: calc(var(--detail-lines, 0) * 1lh);
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
		overflow-wrap: anywhere;
	}

	/* The state layer for a card that opens something, over its light. */
	.card.opens {
		cursor: pointer;
		transition:
			transform var(--dur-fast) var(--ease),
			box-shadow var(--dur-fast) var(--ease);
		border-radius: var(--radius-lg);
	}

	/* And the lift, as an entity card's. */
	.card.opens:hover {
		transform: translateY(var(--lift-y));
		box-shadow: var(--elev-tile-lift);
	}

	.card.opens:hover :global(.panel) {
		background: var(--sift-card-hover-layer), var(--sift-card-fill);
		--sift-line: var(--sift-card-hover-line);
	}

	/* A press settles it back onto the wall, the pressed layer on its light. */
	.card.opens:active:not(:has(a:active, button:active)) {
		transform: none;
	}

	.card.opens:active:not(:has(a:active, button:active)) :global(.panel) {
		background: var(--sift-card-press-layer), var(--sift-card-fill);
	}

	/* Unsettled: a dashed edge on the panel, following its corner. */
	.card.unsettled :global(.panel) {
		outline: 1px dashed var(--sift-line-strong);
		outline-offset: -1px;
	}
</style>
