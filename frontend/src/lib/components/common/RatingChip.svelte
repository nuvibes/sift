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
	   `RatingChoices`. What IS bits-ui is the part with behaviour: the popover that opens the chooser
	   over the page, with its focus handling, its outside-press and its Escape. Rebuilding that by
	   hand is the mistake this file exists to avoid, and the chooser inside it is already a real
	   radio group. */

	/*
	 * A rating, as one star beside the heart, and the chooser it opens.
	 *
	 * A row of stars would be five targets at the five-star scale and ten at the ten-star one (the
	 * setting every rating is stored as), too many to hit on a card, a menu or a selection bar,
	 * where reaching for eight and landing on seven is a silent wrong answer. A number is also the
	 * better readout: "8" is read, eight filled stars are counted. So the value is one mark, the
	 * star with its number, and setting it is a deliberate second act: press the star, pick from a
	 * short list.
	 *
	 * One dressing everywhere, the heart's twin: the heart is the same glyph on every surface, and
	 * the rating is the other half of the same opinion, so the pair must read as one pair. One look
	 * is also what a gate can hold.
	 *
	 * The chooser is `RatingChoices`, also rendered in the flyout above a selection bar and the
	 * submenu off a right-click, so the answers a rating can have are listed once. This component
	 * is a trigger and a surface.
	 *
	 * Stored both ways, always out of ten as on the wire. How many stars that is belongs to
	 * `ratingScale`, applied there, so the conversion is one rule rather than one per screen.
	 */
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
		/**
		 * On a dark translucent disc of its own, for a rating drawn over a picture (an entity
		 * card's corner): the scrim token every control over media stands on.
		 */
		grounded?: boolean;
		/**
		 * What this rating is OF, for a screen reader.
		 *
		 * The chip says "3" and a star, which is a complete sentence to look at and half of one to
		 * hear. Every caller knows what it is rating, so every caller says so here.
		 */
		label?: string;
		/**
		 * How big the glyph is, and it is `Heart`'s own list on purpose.
		 *
		 * The star stands beside a heart on every surface that draws both, so the one thing this has
		 * to be able to do is match it: an 18px heart beside a 20px star reads as two controls
		 * rather than one pair. Same three sizes, same default, so the two
		 * are set together at each call site or neither is.
		 */
		size?: 16 | 18 | 20;
		/**
		 * A word under the mark, for a place where every press beside it has one: the selection
		 * bar's columns on a phone, glyph over word. Nowhere else draws one.
		 */
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

<!--
	The mark: the heart's button with a star in it. The reset, the round shape, the hover scale and
	the focus ring are the same rules `Heart` carries, written here because six declarations are
	cheaper than a component between a press and its handler.

	The number goes before the star, the order it is read in: the value, then what kind of value it
	is. An unrated mark has no number, so the star is all of it.

	`filled` means rated, the heart's rule exactly: an unrated star is an outline, and hovering
	turns it the star's own yellow, so a file nobody has rated never shows a filled star.

	No handler of its own. The trigger props go on a wrapper below (a component is not an element to
	spread listeners onto), and the wrapper opens the surface; a handler here as well would toggle
	it twice. Enter and Space on a real button raise a click, which bubbles, so the keyboard opens
	it the same way.
-->
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

	/*
	 * THE MARK, which is the heart's button with a star in it.
	 *
	 * Round, for the reason the heart is: it is a glyph with nothing else in it, it sits next to a
	 * circle, and a rounded square beside a circle has to agree with it and never does.
	 *
	 * It GROWS rather than gaining a ground, which is the same judgement `Heart` records and the
	 * same reasoning: a colour step on a small mark is very nearly nothing against a light frame,
	 * a round ground under a glyph reads as a smudge, and SCALE is visible on any ground because it
	 * is not a colour at all. It is animatable, so the motion rule is met.
	 *
	 * The figure keeps the ink of the row it is in rather than the star's yellow: it is a number to
	 * read, and a number in the same colour as the mark beside it reads as part of the mark. That is
	 * why the yellow is on `.star` and not here. See below.
	 */
	.mark {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
		/*
		 * `Heart`'s box exactly: the same padding either side and the same corner, so the star chip
		 * and the heart chip in a card's two corners are one pair.
		 */
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
	 * The star's three states are the heart's three states: off is the quiet ink, hover is the
	 * mark's own colour on the outline glyph, and on is that colour filled. The star uses
	 * `--sift-star` where the heart uses `--sift-heart`, so the two halves of one opinion answer
	 * the pointer the same way, and the yellow is the same token the chooser uses. An unrated file
	 * must not wear the yellow at rest, or the loudest thing on the row would claim a rating.
	 *
	 * The colour is on the glyph rather than the button, because the figure beside it must not take
	 * it: a yellow digit reads as part of the mark instead of the value.
	 *
	 * No `:global` needed: `Icon` sets no colour of its own, so it inherits this one.
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

	/* A finger's reach on a phone, the glyph keeping its size: at three cards to a phone's line a
	   card is about 112px wide, so the mark cannot grow, and the reach does instead (the ring
	   `Pressable` draws, for the reason given there). */
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
