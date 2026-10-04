<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'DropOffer',
		category: 'surface',
		role: 'the whole-window offer to take something being dragged over it, saying where it will go',
		basis: 'own',
		states: ['shown']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/*
	 * WHY NOT BITS-UI: bits-ui has no such primitive: this is a sheet of colour and one sentence, with no behaviour of its own.
	 * What the window looks like while it offers to take a drop: a light veil, a dashed accent
	 * line along the window's own edge, a faint accent wash, and one sentence saying where the
	 * thing will go. Every whole-window offer draws this, so a drop reads the same wherever it is
	 * aimed and only the sentence differs.
	 *
	 * The veil is the drop veil, lighter than a dialog's: the page behind is what somebody is
	 * aiming at, so it stays readable. The pointer is mid-drag, so nothing here takes a pointer.
	 * Hidden from assistive technology: it only repeats a gesture the pointer is already making.
	 *
	 * The caller owns the drag and says when it is shown; this owns only the drawing.
	 */
	import { arrive } from '$lib/shell/motion.svelte';

	interface Props {
		/** Whether something the page would take is being held over the window. */
		shown: boolean;
		/** Where it goes if let go here: "Drop to add". */
		words: string;
	}

	let { shown, words }: Props = $props();
</script>

{#if shown}
	<!-- A fade and never a slide: something the size of the window arriving in one frame reads as
	     the page being replaced, and an overlay that slides has an edge where this has none. -->
	<div class="overlay" aria-hidden="true" transition:arrive={{ pace: 'fast' }}>
		<div class="frame">
			<p>{words}</p>
		</div>
	</div>
{/if}

<style>
	.overlay {
		position: fixed;
		inset: 0;
		z-index: var(--z-drop-overlay);
		display: grid;
		place-items: center;
		background: var(--sift-drop-veil);
		pointer-events: none;
	}

	/* The line traces the WINDOW, not a box inside it: "this whole window takes it". Drawn inside the
	   element box, so `inset: 0` keeps all of it on screen. The wash is mixed from the accent, so it
	   follows whichever accent is chosen, and is light enough to see the page through. */
	.frame {
		position: absolute;
		inset: 0;
		display: grid;
		place-items: center;
		border: 2px dashed var(--sift-accent);
		border-radius: var(--radius-xl);
		background: var(--sift-accent-wash);
	}

	/* Its own ground, so the words hold up over a wall of pictures as well as over an empty screen. */
	p {
		margin: 0;
		padding: var(--space-3) var(--space-5);
		border-radius: var(--radius-lg);
		background: var(--sift-scrim);
		color: var(--foreground);
		font: var(--text-display);
		letter-spacing: var(--tracking-display);
		text-align: center;
	}

	/* In the desktop shell the top-right corner is square: the window's own buttons are drawn over
	   the page there, and the content card gives up its curve for them. */
	:global(:root[data-window='overlaid']) .frame {
		border-start-end-radius: 0;
	}
</style>
