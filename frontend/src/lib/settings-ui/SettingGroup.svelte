<script lang="ts">
	/* A block of rows under one heading, and one sentence for all of them where they repeat. */
	import type { Snippet } from 'svelte';

	import SectionHeading from '$lib/components/common/SectionHeading.svelte';
	import { groupPlace } from './settings-path';

	interface Props {
		heading?: string;
		/** One sentence for the whole block. Rows inside are then drawn without their own help. */
		help?: string;
		/** The rows; optional, for a block that holds no rows. */
		children?: Snippet;
		/** An anchor to jump to, for a page index or a settings search result. */
		id?: string;
		/** Choices of one kind: they separate by space, with no line between them. */
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

	/* A heading-only block introduces what follows, so the space goes under the content. */
	.group:not(:has(.rows)) {
		margin-block-end: var(--space-4);
	}

	/* The block's heading is `SectionHeading`, the one group heading. */

	.lede {
		margin: 0 0 var(--space-2);
		max-width: var(--reading-measure);
		line-height: 1.5;
	}

	/* The rows keep their own last margin. */
	.rows {
		display: flow-root;
	}

	/* The first row's rule would double the heading's own separation from it. */
	.rows > :global(.row:first-child) {
		padding-block-start: var(--space-2);
	}

	/* A later group with no heading CONTINUES the group before it: only a page's first group
	   goes without a heading. */
	:global(.group:has(> .rows)) + .group.continues {
		margin-block-start: calc(-1 * var(--space-8));
	}

	:global(.group:has(.ruled-row)) + .group.continues > .rows > :global(.row:first-child) {
		border-block-start: 1px solid var(--sift-line);
		padding-block-start: var(--space-4);
	}
</style>
