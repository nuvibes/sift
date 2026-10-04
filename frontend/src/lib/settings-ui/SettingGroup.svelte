<script lang="ts">
	/*
	 * A block of rows under one heading, and one sentence for all of them where they repeat.
	 *
	 * The rule this exists for: where several settings differ only by which one they are, they get
	 * one heading, one sentence of help, and a row each, never a heading and a paragraph repeated
	 * per setting. The four compression targets are the plain case; the three "build this" switches
	 * on Performance and the vault's lock triggers are the same shape.
	 *
	 * The rows a caller puts inside share the primitive's grid, so their labels line up in one
	 * column and their controls in another. That alignment is the difference between a table and a
	 * list with a border round it.
	 */
	import type { Snippet } from 'svelte';

	import SectionHeading from '$lib/components/common/SectionHeading.svelte';
	import { groupPlace } from './settings-path';

	interface Props {
		heading?: string;
		/** One sentence for the whole block. Rows inside are then drawn without their own help. */
		help?: string;
		/**
		 * The rows.
		 *
		 * Optional, for the blocks whose content is not rows at all: a review queue, a list of
		 * duplicate files. They still want the heading and the sentence in the shared shape, and
		 * writing those by hand is how a screen ends up with a heading two sizes off everyone
		 * else's.
		 */
		children?: Snippet;
		/**
		 * An anchor to jump to: for a page long enough to need an index, and for a settings
		 * search result that names this block.
		 *
		 * ## It lands on the HEADING, not on the section
		 *
		 * A search result rings what it lands on: a two-second wash over the element carrying the
		 * id. On the section that is the heading AND every row under it, and the blocks here are
		 * not all small: the tunnel routing block can cover twice the viewport and the "add a
		 * guest" form half of it. A wash over twice the screen is not pointing at anything.
		 *
		 * On the heading it is one band on one line, which is the same shape a registry row's ring
		 * has, and it is still a perfectly good scroll anchor: landing on the heading is landing
		 * on the block.
		 *
		 * A block with NO heading keeps it on the section, because otherwise the id would silently
		 * go nowhere. That is the case `Maintenance` draws for its list of tidyings.
		 */
		id?: string;
		/**
		 * The rows are choices of one kind (a picker's positions: how a record is drawn, which
		 * units): they separate by space, with no line between them. The hairline stands only
		 * between groups. See `LabelledRow`'s rule for a group of choices.
		 */
		choices?: boolean;
	}

	let { heading, help, children, id, choices = false }: Props = $props();

	/* The heading is a crumb of every settings path under it. */
	groupPlace(() => heading);
</script>

<section class="group" class:continues={!heading} id={heading ? undefined : id}>
	{#if heading}<SectionHeading {id}>{heading}</SectionHeading>{/if}
	{#if help}<p class="lede">{help}</p>{/if}
	{#if children}
		<!-- DRESSED BY: .choices (LabelledRow draws no line between the rows of a group of choices) -->
		<div class="rows" class:choices>
			{@render children()}
		</div>
	{/if}
</section>

<style>
	.group {
		margin-block-end: var(--space-8);
	}

	/* A heading-only block introduces what follows it, so the space belongs under the content
	   rather than under the heading. */
	.group:not(:has(.rows)) {
		margin-block-end: var(--space-4);
	}

	/* The block's heading is `SectionHeading`: the one group heading, larger than the rows and in
	   full ink so it cannot be read as one of them, with the hairline above every group after the
	   first. Its look is decided there, once, for every group on every page. */

	.lede {
		margin: 0 0 var(--space-2);
		max-width: var(--reading-measure);
		line-height: 1.5;
	}

	/* The rows keep their own last margin. A continuing group below pulls itself up by exactly the
	   group gap, so a trailing margin that collapsed out into that gap (the shaded status box under a
	   switch) would vanish and leave the box sitting on the next row's line. */
	.rows {
		display: flow-root;
	}

	/* The first row's rule would double the heading's own separation from it. */
	.rows > :global(.row:first-child) {
		padding-block-start: var(--space-2);
	}

	/* A later group with no heading CONTINUES the group before it: only a page's first group goes
	   without a heading. It closes the gap the earlier group's rows left, and its first row draws
	   the line between two rows, so two unheaded groups never sit a band apart with nothing between
	   them. After a heading alone there is no such gap to close: that group ends at its own smaller
	   margin, and pulling the next one up by the full gap would draw its rows over the heading's
	   sentence. */
	:global(.group:has(> .rows)) + .group.continues {
		margin-block-start: calc(-1 * var(--space-8));
	}

	:global(.group:has(.ruled-row)) + .group.continues > .rows > :global(.row:first-child) {
		border-block-start: 1px solid var(--sift-line);
		padding-block-start: var(--space-4);
	}
</style>
