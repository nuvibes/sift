<script lang="ts">
	/*
	 * An entity's cover in its header, and the presses on it: open the picture full size, change it,
	 * reframe it, take it off. The presses are reveals, out of the way until the pointer or the
	 * keyboard is on the cover and there for good while the record is being edited. What each press
	 * does is the header's; this draws them in their corners.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import { Avatar, Pressable } from '$lib/components/common';
	import type { IconName } from '$lib/design/icons';

	interface Props {
		name: string;
		/** The cover's address, or null for the letter (or `glyph`). */
		picture: string | null;
		/** The file's own still, drawn while the cover's address has nothing behind it yet. */
		insteadOf: string | null;
		/** Whether the picture is a site's own logo, drawn whole rather than cropped. */
		mark: boolean;
		glyph?: IconName;
		editing: boolean;
		mayEdit: boolean;
		/** Change the picture: the pencil. */
		onpicture?: () => void;
		/** Take the chosen cover off; absent where nothing is chosen. */
		onremove?: () => void;
		/** Reframe the chosen cover; absent where it cannot be. */
		onreframe?: () => void;
		/** Whether a removal is on its way. */
		removing: boolean;
		/** Open the picture full size. */
		onenlarge: () => void;
	}

	let {
		name,
		picture,
		insteadOf,
		mark,
		glyph = undefined,
		editing,
		mayEdit,
		onpicture,
		onremove,
		onreframe,
		removing,
		onenlarge
	}: Props = $props();
</script>

<div class="cover" class:editing>
	<!-- `mark` keeps a logo whole: it is drawn to its own edges, where a still has room to crop. -->
	<Avatar src={picture} instead={insteadOf} {name} shape="portrait" {mark} {glyph} />

	<!-- The pencil is there whether or not the record is being edited: the gesture for changing a
	     thing you are looking at is to press it. "Use as the cover" on the Files tab is the
	     equivalent without a pointer. -->
	{#if mayEdit && onpicture}
		<Pressable
			feedback="wash"
			radius="full"
			class="pencil"
			onclick={onpicture}
			aria-label="Change the picture"
		>
			<Icon name="edit" size={20} />
		</Pressable>
	{/if}
	{#if onremove}
		<!-- In the bottom corner across from the pencil, so neither is pressed for the other. -->
		<Pressable
			feedback="wash"
			radius="full"
			class="remove-cover"
			onclick={onremove}
			disabled={removing}
			aria-label="Remove the cover"
		>
			<Icon name="delete" size={20} />
		</Pressable>
	{/if}
	{#if onreframe}
		<!-- Beside the pencil: the pencil changes WHICH picture, this how it sits in the box. -->
		<Pressable
			feedback="wash"
			radius="full"
			class="reframe"
			onclick={onreframe}
			aria-label="Reframe the cover"
		>
			<Icon name="crop" size={20} />
		</Pressable>
	{/if}
	{#if picture && !editing}
		<Pressable
			feedback="none"
			radius="lg"
			class="enlarge"
			onclick={onenlarge}
			aria-label="See the picture of {name} full size"
		>
			<span class="reach"></span>
		</Pressable>
	{/if}
</div>

<style>
	.cover {
		position: relative;
		aspect-ratio: 3 / 4;
		border-radius: var(--radius-lg);
		overflow: hidden;
		background: var(--sift-surface-3);
		border: 1px solid var(--sift-line);
	}

	/* The rules below are `:global` because the classes land on the shared pressable's own element,
	   which carries its scoping hash and not this file's. What is said here is position and shape. */

	/* The whole picture as one target; the cursor says it opens. */
	.cover :global(.enlarge) {
		position: absolute;
		inset: 0;
		padding: 0;
		background: transparent;
		cursor: zoom-in;
	}

	.reach {
		display: block;
		inline-size: 100%;
		block-size: 100%;
	}

	/* On the scrim, so a press reads against a light frame and a dark one, and small: it sits over
	   somebody's face. */
	.cover :global(.pencil),
	.cover :global(.remove-cover),
	.cover :global(.reframe) {
		position: absolute;
		inset-block-end: var(--space-2);
		inset-inline-end: var(--space-2);
		display: grid;
		place-items: center;
		inline-size: 2.25rem;
		block-size: 2.25rem;
		border: 1px solid var(--sift-line);
		background: var(--sift-scrim);
		backdrop-filter: blur(var(--blur-glass));
		color: var(--sift-ink);
		/* Above the target that opens the picture, which fills the cover. */
		z-index: 1;
		opacity: 0;
		transition: opacity var(--dur-fast) var(--ease);
	}

	/* Under the pointer the press lightens ON its scrim: the shared wash is a layer over nothing,
	   which here would leave the glyph bare on the picture. */
	.cover :global(.pencil:hover:not(:disabled)),
	.cover :global(.remove-cover:hover:not(:disabled)),
	.cover :global(.reframe:hover:not(:disabled)) {
		background: color-mix(in srgb, currentColor var(--layer-hover), var(--sift-scrim));
	}

	.cover :global(.pencil:active:not(:disabled)),
	.cover :global(.remove-cover:active:not(:disabled)),
	.cover :global(.reframe:active:not(:disabled)) {
		background: color-mix(in srgb, currentColor var(--layer-pressed), var(--sift-scrim));
	}

	/* Revealed by the pointer or by the keyboard: a control reachable by Tab has to show itself. */
	.cover:hover :global(.pencil),
	.cover :global(.pencil:focus-visible) {
		opacity: 1;
	}

	/* Bottom-left: along the pencil's edge, and off the top of the frame where a face usually is. */
	.cover :global(.remove-cover) {
		inset-inline-start: var(--space-2);
		inset-inline-end: auto;
	}

	.cover:hover :global(.remove-cover),
	.cover :global(.remove-cover:focus-visible),
	.cover.editing :global(.remove-cover) {
		opacity: 1;
	}

	/* One control-width and a gap in from the pencil's corner. */
	.cover :global(.reframe) {
		inset-inline-end: calc(var(--space-2) * 2 + 2.25rem);
	}

	.cover:hover :global(.reframe),
	.cover :global(.reframe:focus-visible),
	.cover.editing :global(.reframe) {
		opacity: 1;
	}

	/* While editing the picture is one of the fields, so the pencil stays. */
	.cover.editing :global(.pencil) {
		opacity: 1;
	}
</style>
