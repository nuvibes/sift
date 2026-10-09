<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'BarPanel',
		category: 'surface',
		role: 'a panel hung from the screen bar: ground, edge, corner and inset in one place',
		basis: 'composes:Panel',
		states: ['default']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: this is a box on a surface: ground, edge, corner, inset. There is no
	behaviour in it; the screen bar owns opening and closing it. */

	/*
	 * The panel that drops out of the screen bar; the inset is the host's so it lines up with it.
	 */
	import type { Snippet } from 'svelte';
	import Panel from '$lib/components/common/Panel.svelte';

	interface Props {
		children: Snippet;
		/** Names the region for a screen reader: "Filters", "Walls". */
		label?: string;
		/** Lay the contents out as a wrapping row of small choices, not a column. */
		row?: boolean;
	}

	let { children, label, row = false }: Props = $props();
</script>

<!-- The box is Panel's; this file places it under its bar. -->
<div class="bar-panel">
	<Panel inset="md" corner="lg" floating {label} {row}>
		{@render children()}
	</Panel>
</div>

<style>
	.bar-panel {
		/* Set by the bar this drops out of; the fallback is the theater's. */
		margin-inline: var(--bar-panel-inset, var(--space-4));
		margin-block-end: var(--space-2);
	}

	/* While filled, the bars' own scrim and blur: a flat slab over video would not read. */
	:global(.screen-box:fullscreen) .bar-panel :global(.panel) {
		background: var(--sift-scrim);
		backdrop-filter: blur(var(--blur-glass));
		box-shadow: var(--elev-3);
	}

	/* Every surface-3 inside becomes the same translucent pane, without reaching into other
	   components' classes. No blur: the panel behind is already blurred. */
	:global(.screen-box:fullscreen) .bar-panel :global(.panel) {
		--sift-surface-3: var(--sift-scrim);
	}
</style>
