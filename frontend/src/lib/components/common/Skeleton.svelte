<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Skeleton',
		category: 'primitive',
		role: 'the shape of something not yet loaded, with a moving highlight',
		basis: 'own',
		states: ['line', 'block', 'circle']
	} satisfies DesignEntry;

	/**
	 * The shape being stood in for.
	 *
	 * `text` is one line of words. `title` is a heading, so it is taller and shorter across. `block`
	 * fills whatever box it is given: a card, a picture, a panel. `circle` is a face or an avatar.
	 */
	export type SkeletonShape = 'text' | 'title' | 'block' | 'circle';
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: there is no skeleton in it. This is a rectangle with a moving highlight: no
	   focus, no keyboard, nothing to announce beyond the one busy region its parent declares. */

	/*
	 * The shape of a thing, drawn before the thing arrives.
	 *
	 * ## Why a shimmer and not a spinner
	 *
	 * A shimmer says "this is becoming content" and a spinner says "something is working". Use this
	 * where the shape is already known (a row, a card, a face, a paragraph) because then the
	 * screen does not jump when the real thing lands: it was already the right size. Use `Spinner`
	 * where there is no shape to promise.
	 *
	 * ## Why the count is here rather than in every caller
	 *
	 * A list writing its own `{#each Array(6)}` picks its own number of lines and its own gap, and
	 * so does the next. `lines` puts the repetition in one place, and
	 * the last line is drawn short: a paragraph's last line is never full width, and a stack of
	 * identical bars reads as a table rather than as text.
	 *
	 * ## One busy region, not twenty
	 *
	 * A screen replacing itself with forty of these announces "busy" forty times to a screen reader
	 * if each one says so. So none of them says anything: the whole group is hidden, and whoever
	 * draws the group is responsible for the one `aria-busy` that describes it.
	 */
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

	/*
	 * A gradient swept across the surface, not a box blinking.
	 *
	 * The same movement `ImportSkeleton` uses, because they are the same idea at two sizes and two
	 * of them fading differently on one screen is the drift this component exists to remove. `sweep`
	 * is one of the motions `app.css` names; it travels `translate`, so the resting offset here is a
	 * `translate` too, or the two would add up.
	 */
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
