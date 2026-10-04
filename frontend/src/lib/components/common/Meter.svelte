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
	/*
	 * A reading, not a task.
	 *
	 * `ProgressBar` says how far through something is and only ever grows; this says where a value
	 * stands between two ends and may go either way: how well somebody can be recognized, a level,
	 * a share of a capacity. They look alike, and that is why they are two components rather than
	 * one with a flag: a screen reader is told which kind of thing it is (`role="meter"` against
	 * `role="progressbar"`), and a bar that can fall must never wear the sweep a bar that cannot
	 * know its end wears.
	 *
	 * The fill is the caller's when the colour IS the reading: recognition strength draws a red-to-
	 * green run and clips it, because a solid fill would hide the three thresholds behind the number.
	 * Given no fill, it draws the accent, like the progress bar.
	 */
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

<!-- The width is set through `style:`, which compiles to a property set on the element rather than
     a style attribute in the markup. The policy the app is served under refuses the attribute form. -->
<Meter.Root {value} min={0} {max} aria-label={label} class="meter-track">
	{#if fill}
		{@render fill(fraction)}
	{:else}
		<div class="fill" style:inline-size={`${fraction * 100}%`}></div>
	{/if}
</Meter.Root>

<style>
	/* The element carrying this class is rendered by the primitive, which puts the class on its own
	   div, so this rule is global. Nothing else in the app is inside this component. The same
	   track the progress bar draws, so the two read as one family. */
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
