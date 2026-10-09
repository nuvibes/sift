<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'ShellBanner',
		category: 'surface',
		role: 'the quiet line across the top of the app with something to press at the end',
		basis: 'own',
		states: ['info', 'warn', 'with an act']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: a line of text with something to press at the end of it. There is no
	behaviour here, and what it announces comes from the status role. */

	/* The quiet line across the top of the app: one sentence and one act, for a fact somebody can
	 * act on now; not a toast, which goes away. One shape for every notice, `role="status"`. */
	import type { Snippet } from 'svelte';

	interface Props {
		/** The sentence. Markup is allowed in it: most of these carry a link. */
		children: Snippet;
		/** What sits at the line's end: a Button, or the one field a notice asks for. */
		action?: Snippet;
		/** For a test that has to find this particular banner among the others. */
		testId?: string;
		/** The act moves under the sentence when both will not fit. */
		wraps?: boolean;
	}

	let { children, action, testId, wraps = false }: Props = $props();
</script>

<div class="banner" class:wraps role="status" data-testid={testId}>
	<p>{@render children()}</p>
	{#if action}{@render action()}{/if}
</div>

<style>
	.banner {
		/* The shell's own row, taking no height when no banner renders. */
		grid-area: banner;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-3);
		padding: var(--space-2) var(--space-4);
		background: var(--sift-surface-2);
		border-bottom: 1px solid var(--sift-line);
	}

	p {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	.wraps {
		flex-wrap: wrap;
	}

	.wraps p {
		flex: 1 1 auto;
	}

	/* The caller's link; global, bounded by `.banner`. */
	.banner :global(a) {
		color: var(--sift-ink);
	}
</style>
