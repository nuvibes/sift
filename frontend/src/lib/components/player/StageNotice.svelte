<script lang="ts">
	/*
	 * The quiet mark in the corner of a picture, and what it says when you reach it.
	 *
	 * A component because two places use it (the item detail explains why a file plays from a
	 * corrected copy; a Theater cell why it is being converted as you watch), and two copies would
	 * drift in size, scrim and when the words appear.
	 *
	 * Inside the picture: a tooltip portals to the document body, and fullscreen paints only the
	 * fullscreened element's subtree, so a portalled bubble would be invisible whenever somebody
	 * watches full screen.
	 *
	 * Information, never a warning: nothing is wrong and nothing is asked. The file plays either
	 * way; the words say why it takes the path it takes. So no tone colour: a red mark over
	 * somebody's video is a fault report, and this is not one.
	 */
	import type { Snippet } from 'svelte';

	import Button from '$lib/components/common/Button.svelte';

	interface Props {
		/** What the mark is called for a screen reader. A question, because it opens an answer. */
		label: string;
		/** The answer. Arbitrary markup, so a caller can put a link to the setting inside it. */
		children: Snippet;
	}

	let { label, children }: Props = $props();
</script>

<div class="notice">
	<Button tone="ghost" size="small" shape="circle" class="mark" icon="info" aria-label={label} />
	<p class="words">{@render children()}</p>
</div>

<style>
	/* `row-reverse`, so the mark sits at the corner it is placed in and the words open INWARDS,
	   away from the edge. Placed the other way they would open off the picture. */
	.notice {
		display: flex;
		flex-direction: row-reverse;
		align-items: flex-start;
		gap: var(--space-2);
	}

	/* The glyph is a font character, and a circle around one needs an explicit box or it sits on
	   its baseline rather than in the middle. The box is the small control's height, not a number of
	   its own, and that height stays small on a phone: this is a small press inside the picture, so
	   it keeps its size there and its REACH grows to a finger's instead, through the ring `Button`
	   draws round every small button. */
	.notice :global(.mark) {
		flex: none;
		transition:
			background var(--dur-fast) var(--ease),
			color var(--dur-fast) var(--ease);
		inline-size: var(--control-height-sm);
		block-size: var(--control-height-sm);
		line-height: 1;
		border-radius: var(--radius-full);
		background: var(--sift-scrim);
		color: var(--sift-ink);
	}

	/* Closed until the mark is hovered or reached by keyboard. `max-inline-size` in characters keeps
	   it to a readable measure and, with the flex above, keeps it inside the frame. */
	.words {
		margin: 0;
		max-inline-size: 34ch;
		padding: var(--space-2) var(--space-3);
		border-radius: var(--radius-sm);
		background: var(--sift-scrim-strong);
		color: var(--sift-ink);
		font: var(--text-body-sm);
		opacity: 0;
		/* Not a hover target while it is invisible: the box keeps its width so the mark does not move
		   when the words appear, and without this that empty width opens the panel by itself. */
		pointer-events: none;
		transform: translateY(calc(var(--space-1) * -1));
		transition:
			opacity var(--dur-fast) var(--ease),
			transform var(--dur-fast) var(--ease);
	}

	/* Something happens under the pointer. A control that opens a panel and does not react to being
	   touched reads as decoration, and the panel then looks like it appeared on its own. */
	.notice :global(.mark:hover),
	.notice :global(.mark:focus-visible) {
		background: var(--sift-scrim-strong);
		color: var(--sift-ink);
	}

	.notice:hover .words,
	.notice:focus-within .words {
		opacity: 1;
		pointer-events: auto;
		transform: none;
	}
</style>
