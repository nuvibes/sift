<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'ProgressBar',
		category: 'composition',
		role: 'how far through, as a bar, known or not',
		basis: 'bits-ui:Progress',
		states: ['determinate', 'indeterminate', 'paused']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	import { Progress } from 'bits-ui';

	interface Props {
		/**
		 * How far along, 0..max. `null` means the work is running but its size is not known yet:
		 * a download before the server has said how big the file is. That is a different thing from
		 * zero, and it looks different: zero is a bar that has not moved, unknown is a sweep.
		 */
		value?: number | null;
		max?: number;
		/** Name what is progressing. A bar with no name is a rectangle to a screen reader. */
		label: string;
		/**
		 * The work is held where it stands, and the bar says so by going grey and staying put.
		 *
		 * Here rather than on the caller, which would have to reach into this file's own classes to
		 * do it, and a rule written outside a component that styles the component's insides reaches
		 * further than it looks (see the note on `.track` below).
		 *
		 * A paused bar KEEPS ITS PLACE. That is the whole of what it is for: the fill is how much has
		 * been fetched and is still on disk, so emptying it, or letting it sweep, would say the bytes
		 * had gone. Nothing about a pause moves, so nothing about the bar moves either.
		 */
		paused?: boolean;
	}

	let { value = 0, max = 100, label, paused = false }: Props = $props();

	// Clamped here rather than trusted. These numbers arrive from a job's own reckoning of its
	// progress, and a fill wider than its track escapes the bar and paints over the row.
	//
	// Undefined when the size is unknown, and that is not tidiness: an inline size beats the
	// stylesheet, so a number here would override the sweep's own width and animate nothing.
	const width = $derived(
		value === null ? undefined : `${Math.min(100, Math.max(0, (value / max) * 100))}%`
	);

	/* The fill eases forwards and never back. A job that starts over, or whose own estimate of its
	   size grew, jumps to the lower figure: a bar seen draining reads as work being undone. */
	let back = $state(false);
	let last: number | null = null;
	$effect.pre(() => {
		const now = value === null ? null : value / max;
		back = last !== null && now !== null && now < last;
		last = now;
	});
</script>

<!-- The width is set through `style:`, which compiles to a property set on the element rather than a
	 style attribute in the markup. The policy the app is served under refuses the attribute form. -->
<Progress.Root {value} {max} aria-label={label}>
	{#snippet child({ props })}
		<!-- Rendered here rather than by the library, so this file's own rule reaches it. -->
		<div {...props} class="track" class:paused>
			<div
				class="fill"
				class:back
				class:indeterminate={value === null && !paused}
				style:inline-size={width}
			></div>
		</div>
	{/snippet}
</Progress.Root>

<style>
	/*
	 * Scoped, not `:global(.track)`: a bare global class rule reaches every element in the document
	 * wearing the class (the tab strip has its own `.track`). The library's `child` snippet lets
	 * this file render the element itself, so the rule is scoped like every other.
	 */
	.track {
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

	.fill.back {
		transition: none;
	}

	/* Held: the fill stops being the colour of work in flight and becomes plain decoration, which
	   is what a bar that is not moving is. The sweep cannot run under it either: an animation
	   on a paused bar says the opposite of what the word says, so the indeterminate class is
	   never put on while this is set, rather than being turned off again here. */
	.track.paused .fill {
		background: var(--sift-ink-4);
	}

	/* Size unknown: a sweep that says work is happening without claiming how much is left. It sets
	   its own width, so the inline size the markup passes is ignored while this runs. `sweep` is one
	   of the motions `app.css` names; this segment is a third of the track, so it travels past the
	   far end before it starts again. */
	.fill.indeterminate {
		inline-size: 35%;
		--sweep-to: 386%;
		animation: sweep var(--dur-loop) var(--ease) infinite;
	}

	/* Nothing repeats under reduced motion. The whole track, faint, says "under way" without
	   claiming a share: a still segment would read as a figure it does not have. */
	:global(:root[data-motion='reduce']) .fill.indeterminate {
		inline-size: 100%;
		opacity: 0.4;
		animation: none;
	}
</style>
