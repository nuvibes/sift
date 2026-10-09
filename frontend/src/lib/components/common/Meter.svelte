<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Meter',
		category: 'composition',
		role: 'a measurement within a known range, which can go up or down',
		basis: 'bits-ui:Meter',
		states: ['default', 'own fill']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* A reading that may go either way (`role="meter"`), unlike ProgressBar; the caller's fill is
	 * used where the colour is the reading. */
	import type { Snippet } from 'svelte';
	import { Meter } from 'bits-ui';

	interface Props {
		/** Where the value stands, 0..max. Clamped: a reading past its own range escapes the track. */
		value: number;
		max?: number;
		/** Name what is measured. A bar with no name is a rectangle to a screen reader. */
		label: string;
		/** How to draw the filled part, given the fraction 0..1. Absent draws the accent. */
		fill?: Snippet<[number]>;
	}

	let { value, max = 100, label, fill }: Props = $props();

	const fraction = $derived(max > 0 ? Math.min(1, Math.max(0, value / max)) : 0);
</script>

<!-- `style:` sets a property; the served policy refuses the style attribute. -->
<Meter.Root {value} min={0} {max} aria-label={label} class="meter-track">
	{#if fill}
		{@render fill(fraction)}
	{:else}
		<div class="fill" style:inline-size={`${fraction * 100}%`}></div>
	{/if}
</Meter.Root>

<style>
	/* Global: the primitive renders the element; the progress bar's track. */
	:global(.meter-track) {
		position: relative;
		block-size: 4px;
		inline-size: 100%;
		border-radius: var(--radius-full);
		background: var(--sift-surface-4);
		overflow: hidden;
	}

	.fill {
		block-size: 100%;
		border-radius: var(--radius-full);
		background: var(--sift-accent);
		transition: inline-size var(--dur-base) var(--ease);
	}
</style>
