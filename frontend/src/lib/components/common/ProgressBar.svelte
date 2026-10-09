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
		/** How far along, 0..max; null is a size not known yet, drawn as a sweep. */
		value?: number | null;
		max?: number;
		/** Name what is progressing. A bar with no name is a rectangle to a screen reader. */
		label: string;
		/**
		 * Held where it stands: grey, keeping its place, since the fetched bytes are still there.
		 */
		paused?: boolean;
	}

	let { value = 0, max = 100, label, paused = false }: Props = $props();

	// Clamped, so a fill cannot escape its track; undefined while unknown, so the sweep sets it.
	const width = $derived(
		value === null ? undefined : `${Math.min(100, Math.max(0, (value / max) * 100))}%`
	);

	/* Eases forwards, jumps back: a draining bar reads as work undone. */
	let back = $state(false);
	let last: number | null = null;
	$effect.pre(() => {
		const now = value === null ? null : value / max;
		back = last !== null && now !== null && now < last;
		last = now;
	});
</script>

<!-- `style:` sets a property; the served policy refuses the style attribute. -->
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
	/* Scoped, since a global `.track` reaches the tab strip's too. */
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

	/* Held: plain decoration, and the sweep never runs under it. */
	.track.paused .fill {
		background: var(--sift-ink-4);
	}

	/* Size unknown: a third-wide segment sweeps past the end. */
	.fill.indeterminate {
		inline-size: 35%;
		--sweep-to: 386%;
		animation: sweep var(--dur-loop) var(--ease) infinite;
	}

	/* Reduced motion: the whole track, faint. */
	:global(:root[data-motion='reduce']) .fill.indeterminate {
		inline-size: 100%;
		opacity: 0.4;
		animation: none;
	}
</style>
