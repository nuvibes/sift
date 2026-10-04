<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'MoreAbout',
		category: 'composition',
		role: 'reference material folded away under a line, found by find-in-page',
		basis: 'site:<details>',
		states: ['shut', 'open']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: `<details>` is the site's disclosure and it is already right: open or
	   closed, on the keyboard, in the accessibility tree, and found by find-in-page. bits-ui's
	   Collapsible would replace all of that with script. */

	/*
	 * Reference material, folded away: the second half of an explanation, a licence term, a
	 * measured figure, the consequence nobody needs before deciding.
	 *
	 * A component so every disclosure opens the same way (the marker removed in both spellings, the
	 * hover ground, the focus ring), wherever it is used.
	 *
	 * "More about this", everywhere, rather than a summary written per use: a label that never
	 * changes becomes furniture, where one that changes has to be read first.
	 */
	import type { Snippet } from 'svelte';

	interface Props {
		/** A plain sentence, for the common case. */
		text?: string;
		/** Or markup, where the folded part has a link or a code span in it. */
		children?: Snippet;
	}

	let { text, children }: Props = $props();
</script>

<details class="more">
	<summary>More about this</summary>
	{#if children}{@render children()}{:else}<p>{text}</p>{/if}
</details>

<style>
	.more {
		margin-block-start: var(--space-2);
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	summary {
		cursor: pointer;
		color: var(--sift-ink-2);
		border-radius: var(--radius-sm);
		text-decoration: underline;
		/* Underlined only on hover, and the resting rule declares it transparent rather than absent
		   so the change is a step rather than a line appearing between frames. There is no surface
		   under this to step, which is the case `check_hover_answers.js` names an underline for. */
		text-decoration-color: transparent;
		text-underline-offset: 3px;
		/* The Light register, declared on the resting rule so the step is animated in both
		   directions rather than only on the way in. */
		transition:
			color var(--dur-instant) var(--ease),
			text-decoration-color var(--dur-instant) var(--ease);
	}

	summary:hover {
		color: var(--hover-ink);
		text-decoration-color: currentColor;
	}

	.more :global(p) {
		margin: var(--space-2) 0 0;
		max-width: var(--reading-measure);
		line-height: 1.5;
	}
</style>
