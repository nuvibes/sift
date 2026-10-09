<script lang="ts">
	/* WHY NOT BITS-UI: there is no card in it, and there is nothing for one to do. This is a
	   surface with a gap and a corner; every behaviour a card carries is its caller's. */
	/*
	 * One card on an Organize wall: a group of faces, a group of look-alike files, a folder that
	 * looks like somebody's.
	 *
	 * ## Why a component for a box
	 *
	 * Because three walls drawing it three ways (a soft card, a bordered card, rows with a hairline)
	 * under one header (`OrganizeHeader`) read as three applications: the inside of one tile looks
	 * nothing like the inside of the next.
	 *
	 * So the box is written once. What goes IN it is the wall's own (a strip of faces, a row of
	 * thumbnails, a portrait and a path) and a wall says nothing about the box.
	 *
	 * ## The shape
	 *
	 * The faces wall's: a soft card on the second surface with a large corner. A card's contents stack
	 * with one gap; whatever is last is pushed to the foot by the caller giving it `margin-block-start:
	 * auto`, which is how a row of cards keeps its buttons on one line however tall the strips
	 * above them are.
	 *
	 * ## The question, in one place on every card
	 *
	 * A card that asks something hands over its `question`, the `detail` under it and its
	 * `answers` (`Answers`), and the card draws them the same way on every wall: the question and
	 * its detail at the foot under the pictures, the answers at the end of the last line. Each wall
	 * placing its own question would give four layouts and button pairs for one kind of decision.
	 *
	 * ## A card that opens something
	 *
	 * `opens` is what the card's activation does (an address, or a call). The whole card then lifts
	 * under the pointer with the hover layer on its light, and a press anywhere on it that is not a
	 * control or a link does the same, as a press on a tile does (`pressOnCard`).
	 */
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
		/**
		 * The lines kept for the detail, however few it takes. On a wall of cards at their own height
		 * the question sits at a fixed distance above the answers, so a detail of one line beside one
		 * of two puts the questions of one row at two heights. Kept at the longest a wall's detail runs.
		 */
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

<!-- The box is `Panel`'s: the ground, the edge, the corner and the inset, decided in one place
     rather than restated here. What is left in this file is the two things that are a DECISION
     card's own: how its contents flow, and what an unsettled one looks like. -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<!-- svelte-ignore a11y_click_events_have_key_events: the keyboard reaches what this opens through
     the card's own button or link. -->
<div
	class="card"
	class:unsettled
	class:opens={opens !== undefined}
	onclick={(event) => pressOnCard(event, opens)}
>
	<!-- No edge and the tighter gap: a card on a wall of cards is bounded by its ground, and the
	     Panel's default edge would make every card two pixels wider than the wall was measured for. -->
	<Panel inset="md" corner="lg" edge={false} gap="sm">
		{@render children()}
		{#if question || answers}
			<!-- The foot: the question, its detail, then the answers, pushed down together so a row
			     of cards has its questions and its buttons on one line each. -->
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
	/* The wrapper takes no look of its own. It exists so the two rules below have something of this
	   file's to hang on, which is what `BarPanel` does for the same reason. */
	.card {
		display: grid;
		min-inline-size: 0;
		/* Where a card's buttons sit: at the START of their line, under the words they answer, on
		   every Organize card, where a row's buttons sit at its end. Said once here and read by
		   every button line inside a card (`Answers`, and the few lines a wall draws itself), each
		   keeping the end for a row outside a card. */
		--card-actions-justify: flex-start;
		--card-actions-push: 0;
	}

	/*
	 * A column, not the grid `Panel` lays its contents out in.
	 *
	 * A card's last block is pushed to its foot by the caller giving it `margin-block-start: auto`
	 * (the faces card does it with its count and buttons, the folder suggestions with their
	 * answers), so a row of cards keeps those on one line however tall the strips above are. In a
	 * grid stretched by the wall, `align-content` behaves as `stretch` and the free space is shared
	 * out between every auto row rather than left at the end, so the middle of the card comes apart
	 * (three children in a 400px box sit at 8, 144 and 390 as a grid, and at 0, 26 and the foot as
	 * a column).
	 */
	.card :global(.panel) {
		display: flex;
		flex-direction: column;
		transition: outline-color var(--dur-instant) var(--ease);
	}

	/* The question block at the foot of the card. One `auto` margin for the whole block: a second
	   one on the answers inside a column would share the free space rather than push. */
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

	/*
	 * THE STATE LAYER of a card that opens something: the hover layer under the pointer, the pressed
	 * layer under a press, laid over the card's light (which a `background-color` would sit under).
	 * A picture over the light does not fade, so it lands in one frame.
	 */
	.card.opens {
		cursor: pointer;
		transition:
			transform var(--dur-fast) var(--ease),
			box-shadow var(--dur-fast) var(--ease);
		border-radius: var(--radius-lg);
	}

	/* And the lift, as an entity card's: the layer alone is a few levels of light, which a card
	   under the pointer on a wide wall does not visibly answer with. */
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

	/* Not settled here: the same box with a dashed edge, which is how `EntityCard` says "this one is
	   not settled" and how a drop target says "not a thing yet". A solid card with nothing that
	   can be pressed in it just looks unfinished. On the panel itself rather than on the wrapper, so
	   the dash follows the corner `Panel` drew instead of a second copy of that radius. */
	.card.unsettled :global(.panel) {
		outline: 1px dashed var(--sift-line-strong);
		outline-offset: -1px;
	}
</style>
