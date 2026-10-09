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
	 * WHY NOT BITS-UI: bits-ui has no such primitive: this is a sheet of colour and one sentence,
	 * with no behaviour of its own. The window's offer to take a drop: a light veil, a dashed
	 * accent edge and one sentence; hidden from assistive technology, as it repeats a gesture in
	 * progress. The caller owns the drag.
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
	<!-- A fade, never a slide, which would give it an edge. -->
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

	/* The line traces the window; the wash follows the accent. */
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

	/* Square top-right under the desktop window's own buttons. */
	:global(:root[data-window='overlaid']) .frame {
		border-start-end-radius: 0;
	}
</style>
