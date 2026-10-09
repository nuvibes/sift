<script lang="ts">
	/*
	 * The one bar of a screen showing several things, for whichever is selected; it knows nothing
	 * of Theater.
	 */
	import type { Snippet } from 'svelte';
	import { arrive } from '$lib/shell/motion.svelte';

	interface Props {
		open: boolean;
		label: string;
		/** Faded with the rest of the chrome, never unmounted, which would replay its arrival. */
		quiet?: boolean;
		tall?: number;
		room?: number;
		/** The picker first: "to what" before "do this". */
		lead?: Snippet;
		overLead?: Snippet;
		children: Snippet;
	}

	let {
		open,
		label,
		lead,
		overLead,
		children,
		quiet = false,
		tall = $bindable(0),
		room = $bindable(0)
	}: Props = $props();

	let bar = $state<HTMLElement | null>(null);
	let widest = $state(0);

	$effect(() => {
		if (bar === null) return;
		const style = getComputedStyle(bar);
		const edges = ['paddingLeft', 'paddingRight', 'borderLeftWidth', 'borderRightWidth'] as const;
		room = Math.max(
			0,
			widest - edges.reduce((sum, edge) => sum + (parseFloat(style[edge]) || 0), 0)
		);
	});
</script>

{#if open}
	<div class="widest" aria-hidden="true" bind:clientWidth={widest}></div>
	<!-- A region, not a dialog: nothing is trapped. -->
	<div
		bind:this={bar}
		class="stage-bar"
		class:led={lead !== undefined}
		bind:offsetHeight={tall}
		class:quiet
		role="region"
		aria-label={label}
		aria-hidden={quiet}
		inert={quiet || undefined}
		transition:arrive={{ y: 64, pace: 'slow', spring: true }}
	>
		{#if lead}
			<div class="lead">{@render lead()}</div>
		{/if}
		{#if overLead}
			<div class="over-lead">{@render overLead()}</div>
		{/if}
		<div class="controls">{@render children()}</div>
	</div>
{/if}

<style>
	/* Absolute in its container, so it centres on the screen, not the window. */
	.stage-bar {
		position: absolute;
		inset-block-end: var(--space-4);
		inset-inline-start: 50%;
		translate: -50% 0;
		z-index: var(--z-bar);
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		grid-template-rows: [scrubber] auto [transport] auto;
		column-gap: var(--space-3);
		inline-size: max-content;
		max-inline-size: calc(100% - var(--space-8));
		padding: var(--space-2) var(--space-3);
		border: 1px solid var(--sift-line);
		border-radius: var(--radius-xl);
		/* A scrim and a blur: legible over moving pictures. */
		background: var(--sift-scrim);
		backdrop-filter: blur(var(--blur-glass));
		transition:
			translate var(--dur-slow) var(--ease),
			opacity var(--dur-slow) var(--ease);
	}

	.widest {
		position: absolute;
		inset-inline: calc(var(--space-8) / 2);
		inset-block-end: 0;
		block-size: 0;
		visibility: hidden;
		pointer-events: none;
	}

	/* `visibility` too, or an invisible bar swallows clicks. */
	.stage-bar.quiet {
		translate: -50% calc(100% + var(--space-4));
		opacity: 0;
		visibility: hidden;
		transition:
			translate var(--dur-slow) var(--ease-in),
			opacity var(--dur-slow) var(--ease-in),
			visibility var(--dur-slow) var(--ease-in);
	}

	/* This bar is the surface: what is put in it brings no scrim, blur or corner. */
	.stage-bar :global(.player-bar) {
		padding: 0;
		border-radius: 0;
		background: none;
		backdrop-filter: none;
	}

	.stage-bar :global(.player-bar > .row:not(.phone)) {
		grid-template-columns: minmax(0, max-content) auto minmax(0, max-content);
		column-gap: var(--space-8);
	}

	.stage-bar :global(.player-bar > .row.follows:not(.phone)) {
		grid-template-columns: auto minmax(0, max-content) minmax(0, max-content);
	}

	.stage-bar.led {
		grid-template-columns: auto minmax(0, 1fr);
	}

	.stage-bar:has(:global(.player-bar > :nth-child(3))) {
		grid-template-rows: [scrubber] auto [transport] auto [below] auto;
	}

	.lead {
		grid-column: 1;
		grid-row: transport;
		align-self: center;
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}

	.over-lead {
		grid-column: 1;
		grid-row: scrubber;
		align-self: center;
		display: flex;
		align-items: center;
		justify-content: center;
	}

	/* Down to whatever the wall gives (`min-inline-size: 0`). */
	.controls {
		grid-column: -2;
		grid-row: 1 / -1;
		display: grid;
		grid-template-rows: subgrid;
		min-inline-size: 0;
	}

	.controls > :global(.player-bar) {
		grid-row: 1 / -1;
		grid-template-rows: subgrid;
	}
</style>
