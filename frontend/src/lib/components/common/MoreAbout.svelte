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
	closed, on the keyboard, in the accessibility tree and found by find-in-page. */

	/* Reference material folded away under the same words everywhere, "More about this". */
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
		/* Underlined only on hover, from transparent (`check_hover_answers.js`). */
		text-decoration-color: transparent;
		text-underline-offset: 3px;
		/* On the resting rule, so it steps both ways. */
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
