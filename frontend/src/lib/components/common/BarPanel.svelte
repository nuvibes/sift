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
	   behaviour in it: what opens and closes it is the screen bar, which already owns that, and
	   putting a Popover here would give it a second opinion about when it is showing. */

	/*
	 * The panel that drops out of the screen bar.
	 *
	 * ## Why this exists
	 *
	 * Four panels drop out of a bar (the filters, the theater's walls, its layouts, its cell
	 * sources), and written separately each would choose its own edge, corner and inset. One
	 * component answers the four questions once.
	 *
	 * The edge is here because a panel drops onto the
	 * page over content of the same ground as itself, so without a line it has no boundary: it
	 * reads as the page having grown a lighter region rather than as a thing that opened.
	 *
	 * ## The inset is the host's, not this one's
	 *
	 * A panel lines up with the bar it came out of, and the bars are inset differently: the top bar
	 * takes the page's own padding and the theater's takes less, because the theater gives its wall
	 * every pixel it can. So the inset is a variable the host sets and this reads: one rule, two
	 * answers, and neither screen has to know about the other.
	 */
	import type { Snippet } from 'svelte';
	import Panel from '$lib/components/common/Panel.svelte';

	interface Props {
		children: Snippet;
		/** Names the region for a screen reader: "Filters", "Walls". */
		label?: string;
		/**
		 * Lay the contents out as a row that wraps, instead of a column.
		 *
		 * For a panel that is a set of small things to choose between: the theater's layouts and
		 * walls. A column is right for a panel of controls with labels.
		 */
		row?: boolean;
	}

	let { children, label, row = false }: Props = $props();
</script>

<!-- The box is `Panel`'s. What is this file's is where a bar's panel sits: the inset the host sets,
     the room under it, and what it becomes while the screen is filled. -->
<div class="bar-panel">
	<Panel inset="md" corner="lg" floating {label} {row}>
		{@render children()}
	</Panel>
</div>

<style>
	.bar-panel {
		/* Set by whatever bar this drops out of; see the comment above. The fallback is the theater's,
		   because a panel with no host saying otherwise is sitting on a screen that wants its width. */
		margin-inline: var(--bar-panel-inset, var(--space-4));
		margin-block-end: var(--space-2);
	}

	/*
	 * WHILE THE SCREEN IS FILLED, IT IS THE SAME PANE THE TWO BARS ARE.
	 *
	 * A filled screen already draws its chrome as one surface: the row at the top and the wall's
	 * controls at the foot are both a scrim with a blur behind a hairline edge, which is what stays
	 * legible over moving pictures. This drops out from under that row, and the flat `surface-2`
	 * it wears on an ordinary page would be an opaque grey slab hanging off a translucent pill,
	 * over video.
	 *
	 * The values are the bars' own, cited rather than invented: the surface and the blur are the
	 * facts panel's, and it is the corner and the shadow that make it one object with the row above.
	 * Only while filled: on a page it sits on the page's own ground, where a blur would be
	 * blurring nothing.
	 */
	:global(.screen-box:fullscreen) .bar-panel :global(.panel) {
		background: var(--sift-scrim);
		backdrop-filter: blur(var(--blur-glass));
		box-shadow: var(--elev-3);
	}

	/*
	 * And every grey inside it becomes the same pane, in one line rather than in five files.
	 *
	 * The panel is full of surfaces that are `--sift-surface-3` on an ordinary page (a column's
	 * chooser, its "type to filter" box, the date field, the kept-filter pills), which would read
	 * as opaque slabs on a translucent panel over video. Redefining the token on this element
	 * reaches every descendant that asks for that surface, whichever file drew it, without rules
	 * reaching into other components' class names.
	 *
	 * No blur here: the panel behind is already blurred, and blurring a blur costs a composited
	 * layer per control for no visible difference.
	 *
	 * The hovers need nothing here: each is the state layer over its own ground, so it follows the
	 * scrim by itself.
	 */
	:global(.screen-box:fullscreen) .bar-panel :global(.panel) {
		--sift-surface-3: var(--sift-scrim);
	}
</style>
