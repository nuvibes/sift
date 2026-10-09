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
	 * WHY NOT BITS-UI: bits-ui has no such primitive: this is a filled box over text, shape and
	 * colour only. Text not to be read over a shoulder, covered and never blurred (a blur can be
	 * read back); the frost is made-up light on the box, never the text. `shown` lifts it; the
	 * press is the caller's.
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
	/* Opaque ground, transparent text: nothing of the words is drawn. */
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

	/* The light on its own layer, so the blur reaches only it. */
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
