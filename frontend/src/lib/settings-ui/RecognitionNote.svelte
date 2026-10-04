<script lang="ts">
	/*
	 * Where a Recognition feature stands, in one shaded box under its switch.
	 *
	 * One box holds everything the feature has to say about itself: the status line, and under it
	 * whatever is in flight or worth a warning (a download's bar, a run that stopped, files measured
	 * by a previous model). Two boxes, or a status line with a warning drawn somewhere else, read as
	 * two things having gone wrong. The box sits under the switch because the status is about the
	 * switch: whether saying yes has done what somebody expected.
	 *
	 * Drawn by `RecognitionPane` on each feature's own section and by `RecognitionSection` beside the
	 * three switches on Importing, so the two doors say the same thing the same way.
	 */
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
	/* The shaded ground is what makes the line read as the switch's answer rather than as one more
	   row. It stays in the rows' measure, so it never reaches across the control column. */
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
