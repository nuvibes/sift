<script lang="ts">
	/* One picture on a card, by the kind the server names; an unknown kind draws nothing. */
	import { thumbUrl } from '$lib/entity/art';
	import Icon from '$lib/components/Icon.svelte';
	import { cropUrl, blankOnRefusal } from '$lib/people/faces.svelte';
	import type { IconName } from '$lib/design/icons';

	interface Props {
		kind: string;
		id: string;
		/** Where pressing it goes. Without one it stays a picture, which is what a card wants. */
		href?: string | null;
		/** What a press does beyond following the link (open the file in place). */
		onclick?: (event: MouseEvent) => void;
		/** The token that lets the browser keep this picture a week; without it, a bare address. */
		art?: string | null;
		/**
		 * The picture could not be drawn: the caller takes it away; absent, a blank keeps the
		 * shape.
		 */
		onmissing?: () => void;
		/** A glyph on a picture's ground instead, when no still can be drawn. */
		standIn?: IconName;
		/** `strip`: a small cropped square. `whole`: square and uncropped, for comparing copies. */
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

	/* A lost picture wears the `hide_image` glyph, never an empty square. */
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
	A link where the queue gave one, named neutrally: the same still is on the board, undecided.
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
	<!-- Decoration: the card's words say what the pile is. -->
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

	/* Whole: the box's width, square, uncropped. */
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
