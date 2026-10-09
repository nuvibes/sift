<script lang="ts">
	/* Where a Recognition feature stands, in one shaded box under its switch. */
	import type { Snippet } from 'svelte';
	import { Note } from '$lib/components/common';

	interface Props {
		/** The one line, every number in it the server's. */
		status: string;
		/** Ready to run: the good-news mark. */
		ready?: boolean;
		/** Switched on and unable to run (no models, no device): the caution mark. */
		caution?: boolean;
		/** What follows the line inside the same box. */
		children?: Snippet;
	}

	let { status, ready = false, caution = false, children }: Props = $props();
</script>

<div class="recognition-note">
	<Note tone={caution ? 'caution' : 'info'} icon={ready ? 'check_circle' : undefined}>
		<span class="status">{status}</span>
	</Note>
	{#if children}
		<div class="more">{@render children()}</div>
	{/if}
</div>

<style>
	/* The shaded ground is what makes the line read as the switch's answer rather than as one
	   more row. */
	.recognition-note {
		max-width: var(--reading-measure);
		margin: 0 0 var(--space-3);
		padding: var(--space-3);
		border-radius: var(--radius-md);
		background: var(--sift-surface-2);
	}

	/* The line itself, in the box's own measure; the mark sits beside it at the note's rhythm. */
	.status {
		font: var(--text-body-sm);
	}

	.more {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		margin-block-start: var(--space-2);
		padding-inline-start: calc(16px + var(--space-2));
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	/* A snippet that drew nothing this time leaves no band under the line. */
	.more:not(:has(*)) {
		display: none;
	}

	.more :global(p) {
		margin: 0;
	}
</style>
