<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Skeleton',
		category: 'primitive',
		role: 'the shape of something not yet loaded, with a moving highlight',
		basis: 'own',
		states: ['line', 'block', 'circle']
	} satisfies DesignEntry;

	/** The shape stood in for: a line, a heading, a filled box or a face. */
	export type SkeletonShape = 'text' | 'title' | 'block' | 'circle';
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: there is no skeleton in it. This is a rectangle with a moving highlight: no
	   focus, no keyboard, nothing to announce beyond the one busy region its parent declares. */

	/* The shape of a thing before it arrives (a Spinner is for no known shape); `lines` stacks them,
	 * the last short. Hidden: the caller declares the one busy region. */
	import { motion } from '$lib/shell/motion.svelte';

	interface Props {
		shape?: SkeletonShape;
		/** How many to stack. Only meaningful for `text`; the last one is drawn short. */
		lines?: number;
	}

	let { shape = 'text', lines = 1 }: Props = $props();

	const rows = $derived(shape === 'text' ? Math.max(1, lines) : 1);
</script>

<span class="stack" aria-hidden="true">
	{#each Array.from({ length: rows }, (_, at) => at) as at (at)}
		<span
			class="bone {shape}"
			class:shimmer={!motion.reduced}
			class:last={shape === 'text' && rows > 1 && at === rows - 1}
		></span>
	{/each}
</span>

<style>
	.stack {
		display: grid;
		gap: var(--space-2);
		inline-size: 100%;
	}

	.bone {
		position: relative;
		display: block;
		overflow: hidden;
		background: var(--sift-surface-2);
		border-radius: var(--radius-sm);
	}

	/* A swept gradient, `sweep` as ImportSkeleton uses; its rest offset is a `translate` too. */
	.shimmer::after {
		content: '';
		position: absolute;
		inset: 0;
		background: linear-gradient(
			100deg,
			transparent 20%,
			var(--sift-surface-3) 50%,
			transparent 80%
		);
		translate: -100% 0;
		animation: sweep var(--dur-loop) var(--ease) infinite;
	}

	/* A line of words: the height of one, not of a paragraph. */
	.text {
		block-size: 0.85em;
	}

	/* The last line of a paragraph is never the full width. */
	.text.last {
		inline-size: 62%;
	}

	.title {
		block-size: 1.4em;
		inline-size: 45%;
		border-radius: var(--radius-md);
	}

	/* Fills whatever it is given, so the caller's box decides the size. */
	.block {
		block-size: 100%;
		min-block-size: 48px;
		border-radius: var(--radius-lg);
	}

	.circle {
		aspect-ratio: 1;
		border-radius: var(--radius-full);
	}

	/* The OS setting is honoured even where Sift's own preference has not been read yet. */
	:global(:root[data-motion='reduce']) .shimmer::after {
		animation: none;
	}
</style>
