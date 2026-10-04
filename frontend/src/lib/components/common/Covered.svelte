<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Covered',
		category: 'primitive',
		role: 'a run of text painted over by a filled box the width of the text, so it is withheld from whoever is looking at the screen',
		basis: 'own',
		states: ['covered', 'shown']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/*
	 * WHY NOT BITS-UI: bits-ui has no such primitive: this is a filled box over text, shape and colour only.
	 *
	 * Text somebody should not read over a shoulder: the rest of a machine's address, the name of
	 * the profile folder in a file's place. The one drawing for both, so a hidden part reads the
	 * same wherever it is.
	 *
	 * ## Covered, never blurred
	 *
	 * The text is painted OVER (a filled box the width of the text) rather than blurred. A blur is
	 * not a mask: the shapes are still there, a long number is still legible at a glance from the
	 * right distance, and a screenshot of it can be sharpened. A solid ground says plainly that
	 * something is being withheld, and it cannot be read back. The text stays in the page under it,
	 * so the line does not change width when it is shown, and it still reads as a word rather than
	 * as a gap. The same surface a field is drawn on, so it reads as a filled-in box and not as a
	 * rendering fault.
	 *
	 * ## Drawn with the veils' softness
	 *
	 * The box wears the frost (`--frost-edge`, `--frost-line` in `app.css`): its edge faded inward
	 * along a gaussian's curve and a band of light across it blurred by the veil, so it reads as
	 * a line of text out of focus rather than a hard tile. What is blurred is that
	 * made-up light and never the text, which stays transparent on the opaque ground: the look
	 * of a blur, with nothing under it to sharpen.
	 *
	 * `shown` lifts the cover (an address somebody pressed to read). The press is the caller's:
	 * this draws, and holds no answer of its own.
	 */
	import type { Snippet } from 'svelte';

	interface Props {
		/** Whether the cover is lifted and the text drawn as it is. */
		shown?: boolean;
		children: Snippet;
	}

	let { shown = false, children }: Props = $props();
</script>

<!-- No whitespace inside the span on purpose: any would be painted over as part of the box. -->
<span class="covered" class:shown>{@render children()}</span>

<style>
	/* The ground is opaque and the text on it transparent, so nothing of the words is drawn at all;
	   the frost (the soft edge and the blurred light) is laid on that ground, never made from the text. */
	.covered {
		position: relative;
		color: transparent;
		text-shadow: none;
		background: var(--sift-surface-3);
		border-radius: var(--radius-sm);
		mask-image: var(--frost-edge);
		mask-composite: intersect;
		user-select: none;
	}

	/* The light, on its own layer so the blur reaches only it: the box, never the text in it, and
	   never anything a caller draws beside it. */
	.covered::after {
		content: '';
		position: absolute;
		inset: 0;
		border-radius: inherit;
		background: var(--frost-line);
		filter: blur(var(--blur-veil));
		pointer-events: none;
	}

	.covered.shown {
		color: inherit;
		background: none;
		mask-image: none;
		user-select: text;
	}

	.covered.shown::after {
		content: none;
	}
</style>
