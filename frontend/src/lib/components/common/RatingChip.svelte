<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'RatingChip',
		category: 'primitive',
		role: 'the rating drawn as the star beside the heart, and the chooser it opens',
		basis: 'composes:RatingChoices,Popover',
		states: ['unrated', 'hover', 'rated', 'read-only']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: the mark and the chooser are Sift's own: a button with a glyph in it, and
	`RatingChoices`; the popover that opens the chooser is bits-ui's. */

	/* A rating as one star with its number beside the heart, and the chooser it opens: five or ten
	 * stars are too many targets. Stored out of ten; `ratingScale` converts. */
	import Popover from '$lib/components/common/Popover.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import RatingChoices from './RatingChoices.svelte';
	import { ratingScale } from '$lib/library/rating.svelte';

	interface Props {
		/** Out of ten, as stored, or null for unrated. */
		rating: number | null;
		/** Given the new value, or null when the rating is being cleared. */
		onchange?: (rating: number | null) => void;
		/** Shown rather than set: a readout with nothing to press. */
		readonly?: boolean;
		/** On a dark disc of its own, for a rating drawn over a picture. */
		grounded?: boolean;
		/** What this rating is of, for a screen reader. */
		label?: string;
		/** The glyph size, Heart's own list, so the pair is set together. */
		size?: 16 | 18 | 20;
		/** A word under the mark, on a phone's selection bar. */
		words?: string;
	}

	let {
		rating,
		onchange,
		readonly = false,
		size = 18,
		label = 'Rating',
		grounded = false,
		words
	}: Props = $props();

	let open = $state(false);

	/** The stored rating on the account's scale. Null when there is none. */
	const shown = $derived(ratingScale.shown(rating));
	const outOf = $derived(ratingScale.stars);

	const said = $derived(
		shown === null ? `${label}: not rated` : `${label}: ${shown} out of ${outOf}`
	);
</script>

<!-- The heart's button with a star: number first, filled when rated. No handler: the wrapper
below carries the trigger props, and a click opens it from the keyboard too. -->

{#snippet face()}
	<button
		type="button"
		class="mark"
		class:rated={shown !== null}
		class:grounded
		disabled={readonly}
		aria-label={said}
	>
		{#if words}
			<span class="glyphs">{@render glyphs()}</span>
			<span class="words">{words}</span>
		{:else}
			{@render glyphs()}
		{/if}
	</button>
{/snippet}

<!-- The value and the star, drawn once for both shapes of the mark. -->
{#snippet glyphs()}
	{#if shown !== null}<span class="figure">{shown}</span>{/if}
	<span class="star"><Icon name="star" {size} filled={shown !== null} /></span>
{/snippet}

{#if readonly}
	{@render face()}
{:else}
	<Popover
		bind:open
		label="Rate this"
		side="bottom"
		align="start"
		sideOffset={6}
		shape="menu"
		width="auto"
	>
		{#snippet trigger({ props })}
			<span {...props}>{@render face()}</span>
		{/snippet}
		<RatingChoices
			{rating}
			onpick={(value) => {
				// Shut on the way out. A chooser left standing over a mark whose value has just
				// changed is a list showing the answer to a question already answered.
				open = false;
				onchange?.(value);
			}}
		/>
	</Popover>
{/if}

<style>
	/* The value and the star as one line over the word, where there is a word (`words`). */
	.glyphs {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
	}

	.words {
		color: var(--sift-ink);
	}

	/* The mark: round and growing like the heart's, the figure in the row's ink, not the star's. */
	.mark {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
		/* Heart's box exactly, so the two are one pair. */
		padding: var(--space-1) var(--space-2);
		border: 0;
		border-radius: var(--radius-md);
		background: transparent;
		color: var(--sift-ink-2);
		font: var(--text-label);
		line-height: 1;
		cursor: pointer;
		transition:
			transform var(--dur-fast) var(--ease),
			color var(--dur-fast) var(--ease);
	}

	.mark:disabled {
		cursor: default;
	}

	.mark:not(:disabled):hover {
		transform: scale(1.18);
	}

	.mark:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	/*
	 * The star's three states are the heart's, in `--sift-star`; an unrated file never wears
	 * yellow.
	 */
	.star {
		display: inline-flex;
		color: inherit;
		transition: color var(--dur-fast) var(--ease);
	}

	.mark:not(:disabled):hover .star,
	.mark.rated .star {
		color: var(--sift-star);
	}

	.figure {
		font-variant-numeric: tabular-nums;
	}

	:global(:root[data-motion='reduce']) .mark,
	:global(:root[data-motion='reduce']) .star {
		transition: none;
	}
	/* The disc under a rating over a picture. A ground alone: the shape is `.mark`'s. */
	.grounded {
		background: var(--sift-scrim);
	}

	/* A finger's reach on a phone through Pressable's ring, the glyph keeping its size. */
	@media (max-width: 767px) {
		:where(.mark) {
			position: relative;
		}

		.mark::after {
			content: '';
			position: absolute;
			inset-block: min(0px, calc((100% - var(--touch-target)) / 2));
			inset-inline: min(0px, calc((100% - var(--touch-target)) / 2));
		}
	}
</style>
