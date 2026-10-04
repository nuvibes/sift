<script lang="ts">
	/*
	 * One picture on a card, addressed by what KIND of thing it is.
	 *
	 * The kind comes from the server rather than being worked out from the id, because the queues
	 * draw different things (a still from a file, a crop of a face) and an id says nothing about
	 * which. A queue whose kind this version does not know draws nothing rather than a broken
	 * picture: an older client meeting a newer server should look plain, not faulty.
	 */
	import { thumbUrl } from '$lib/entity/art';
	import Icon from '$lib/components/Icon.svelte';
	import { cropUrl, blankOnRefusal } from '$lib/people/faces.svelte';
	import type { IconName } from '$lib/design/icons';

	interface Props {
		kind: string;
		id: string;
		/** Where pressing it goes. Without one it stays a picture, which is what a card wants. */
		href?: string | null;
		/**
		 * What a press on the link does beyond following it: the match rows open the file in the
		 * viewer in place, as their title does.
		 */
		onclick?: (event: MouseEvent) => void;
		/**
		 * The token that lets the browser keep this picture for a week (see `thumbUrl`).
		 *
		 * Sent by the server on every picture a card draws, minted in one place for the whole
		 * answer rather than by each queue. A card drawn without one gets a bare address, which is
		 * re-checked on every use: slower, never wrong. The server makes the week-long `immutable`
		 * promise only to an address carrying a token, because an address that does not name its
		 * contents cannot honour it: the picture could be regenerated, or its vault shut, while the
		 * browser went on drawing the old one.
		 *
		 * A face crop takes it too. Its token carries the account's stamp and nothing about the
		 * picture, because a crop is written once and never rewritten (see `cropUrl`).
		 */
		art?: string | null;
		/**
		 * The picture could not be drawn (not made yet, or refused). Given, the caller takes the
		 * picture away, which a strip wants: a blank square among stills reads as a hidden or broken
		 * file. Without it the picture is drawn as a blank, which keeps a card's shape.
		 */
		onmissing?: () => void;
		/**
		 * Draw this glyph on a picture's ground instead of a picture: the one stand-in a strip keeps
		 * when none of its stills can be drawn. Decoration, like the stills.
		 */
		standIn?: IconName;
		/**
		 * `strip`: the small square a strip or a row holds, cropped to fill it. `whole`: the picture
		 * fills the width it is given, square, drawn whole and never cropped, for a screen where the
		 * difference between two pictures is the point (choosing which copy to keep).
		 */
		fit?: 'strip' | 'whole';
		/** The file is in a vault this session has not opened: the hidden mark stands in for it. */
		concealed?: boolean;
	}

	let {
		kind,
		id,
		href = null,
		art = null,
		onclick,
		onmissing,
		standIn,
		fit = 'strip',
		concealed = false
	}: Props = $props();

	/* A picture that could not be drawn, where the caller keeps its place: the glyph a file wears
	   for the same thing (`Tile`'s `hide_image`), on the picture's ground, rather than an empty
	   square that reads as a picture still loading or a broken one. */
	let lost = $state(false);

	function refused(event: Event): void {
		blankOnRefusal(event);
		if (onmissing) onmissing();
		else lost = true;
	}

	const src = $derived(
		kind === 'asset'
			? thumbUrl({ id, art, concealed })
			: kind === 'face'
				? cropUrl({ track_id: id, art })
				: null
	);
</script>

{#if standIn}
	<span class="stand-in" aria-hidden="true"><Icon name={standIn} size={20} /></span>
{:else if lost && fit === 'strip' && !href}
	<span class="stand-in" aria-hidden="true"><Icon name="hide_image" size={20} /></span>
{:else if src && href}
	<!--
		A link, where the queue said where it goes.

		A record of what was decided is worth much more when the things in it can be opened: reading
		that 21 faces were set aside answers less than one press on the ones it happened to. It keeps
		an accessible name because a link with no name is announced as its address.

		The name says "what this is about" rather than "what this DECISION was about", and the two
		words are the difference between true and nearly true: the same picture is drawn on the
		Organize board, where a queue sends the group a still belongs to and nothing has been decided
		yet. Neutral, because the one thing the queue does not send is a sentence, and a card cannot
		invent one without knowing which queue it is holding.
	-->
	<a {href} {onclick} aria-label="Open what this is about">
		{#if lost && fit === 'strip'}
			<span class="stand-in" aria-hidden="true"><Icon name="hide_image" size={20} /></span>
		{:else}
			<img
				class:whole={fit === 'whole'}
				{src}
				alt=""
				loading="lazy"
				decoding="async"
				onerror={refused}
			/>
		{/if}
	</a>
{:else if src}
	<!-- Decoration. The card's own words say what the pile is, and a row of eight thumbnails read
	     out one by one is eight interruptions saying nothing. -->
	<img
		class:whole={fit === 'whole'}
		{src}
		alt=""
		loading="lazy"
		decoding="async"
		onerror={refused}
	/>
{/if}

<style>
	a {
		display: block;
		border-radius: var(--radius-sm);
	}

	a:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	img,
	.stand-in {
		width: 44px;
		height: 44px;
		border-radius: var(--radius-sm);
		object-fit: cover;
		background: var(--sift-surface-3);
	}

	/* Whole: as wide as the box it is in, square, and the picture uncropped on the same ground a
	   picture that could not be drawn leaves behind. */
	img.whole {
		display: block;
		inline-size: 100%;
		block-size: auto;
		aspect-ratio: 1 / 1;
		object-fit: contain;
	}

	.stand-in {
		display: grid;
		place-items: center;
		flex: none;
		color: var(--sift-ink-3);
	}
</style>
