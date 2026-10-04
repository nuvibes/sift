<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Panel',
		category: 'surface',
		role: 'a box standing on a surface: ground, edge, corner and inset, decided once',
		basis: 'own',
		states: ['raised', 'recessed', 'caution', 'elevated', 'edgeless', 'row']
	} satisfies DesignEntry;

	/** The ground a panel stands on. See the three tones in the file's header. */
	export type PanelTone = 'raised' | 'recessed' | 'caution';
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: a box on a surface has no behaviour (ground, edge, corner, inset) and the
	   library ships behaviour. What opens and closes a panel is whatever draws it. */

	/*
	 * The box everything else stands in: a ground, a hairline edge, a corner and an inset, answered
	 * once. `BarPanel` is this plus the margins a bar's panel needs.
	 *
	 * The two grounds. `raised` is a step lighter than what it stands on, a panel over the page.
	 * `recessed` is a step darker, a box set into a surface that is already raised, which keeps a
	 * box inside a dialog from being the same grey as the dialog. These are the same two steps the
	 * whole surface scale uses.
	 *
	 * And a third, which is not a step of that scale: `caution`. A box holding something worth
	 * reading twice before going on: what a compression cannot do to some of the files, what an
	 * edit is not allowed to do at all. A tone here rather than a block form of `Note`: `Note` is a
	 * sentence with a mark and has no box, this is the box, and a boxed caution is the two
	 * together.
	 *
	 * The ground is `--sift-warn-bg` and the edge `--sift-warn`, the pair the status chips use for
	 * the same meaning. The ink is untouched: a whole sentence in warning amber reads as something
	 * having gone wrong (see `Note`'s header). The box says it; the words stay ordinary.
	 */
	import type { Snippet } from 'svelte';

	interface Props {
		children: Snippet;
		/**
		 * Lighter than what it stands on (`raised`), set into it (`recessed`), or something to read
		 * twice (`caution`).
		 */
		tone?: PanelTone;
		/**
		 * How much room inside. `sm` for a panel of controls, `md` for one with words in it, `xs`
		 * for a panel of MENU ROWS: the menu's own inset, so rows that carry `--menu-row-radius`
		 * (the menu's corner less this inset) sit concentric in a panel with the `lg` corner.
		 */
		inset?: 'xs' | 'sm' | 'md';
		/** The corner. `lg` for a panel on the page, `md` for one inside something else. */
		corner?: 'md' | 'lg';
		/** Lifted off the page with a shadow, for a panel that floats over content. Floats too. */
		elevated?: boolean;
		/**
		 * Over the screen rather than standing on it: a bar's panel, a sheet hanging from a bar. It
		 * keeps the flat card tone, because a floating thing with a light of its own reads as a
		 * second lamp. A raised panel standing on the page wears the card's light (`--sift-card`).
		 */
		floating?: boolean;
		/** The hairline edge. A card on a wall of cards has none: the ground is its boundary. */
		edge?: boolean;
		/** The room between the things inside. `sm` for a card of small parts. */
		gap?: 'sm' | 'md';
		/** Lay the contents out as a wrapping row instead of a column. */
		row?: boolean;
		/** Names the region for a screen reader, where it is a region: "Filters", "More controls". */
		label?: string;
	}

	let {
		children,
		tone = 'raised',
		inset = 'md',
		corner = 'lg',
		elevated = false,
		floating = false,
		edge = true,
		gap = 'md',
		row = false,
		label
	}: Props = $props();
</script>

<div
	class="panel {tone} inset-{inset} corner-{corner} gap-{gap}"
	class:elevated
	class:floating={floating || elevated}
	class:edgeless={!edge}
	class:row
	role={label ? 'group' : undefined}
	aria-label={label}
>
	{@render children()}
</div>

<style>
	.panel {
		display: grid;
		gap: var(--space-3);
		min-inline-size: 0;
		border: 1px solid var(--sift-line);
	}

	.raised {
		background: var(--sift-surface-2);
	}

	/* A card standing on the page: lit from the top-left, its hairline catching that light and lost
	   into the fill at the bottom-right. The edge is a layer of the ground under a transparent
	   border, so the border keeps its width and nothing moves. Without an edge the fill runs under
	   the border too, and the ground alone is the boundary. */
	.raised:not(.floating) {
		border-color: transparent;
		background: var(--sift-card);
	}

	.raised.edgeless:not(.floating) {
		background: var(--sift-card-fill);
	}

	.recessed {
		background: var(--sift-surface-1);
	}

	/* The one tone that is not a step of the surface scale. The edge carries the colour as well as
	   the ground, because the tint alone is a few percent of light in a dark theme and a box that
	   only nearly looks different is worse than one that does not try. */
	.caution {
		background: var(--sift-warn-bg);
		border-color: var(--sift-warn);
	}

	.inset-xs {
		padding: var(--space-1);
	}

	.inset-sm {
		padding: var(--space-2);
	}

	.inset-md {
		padding: var(--space-3);
	}

	.corner-md {
		border-radius: var(--radius-md);
	}

	.corner-lg {
		border-radius: var(--radius-lg);
	}

	.elevated {
		box-shadow: var(--elev-3);
	}

	.edgeless {
		border-color: transparent;
	}

	.gap-sm {
		gap: var(--space-2);
	}

	.row {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-start;
		gap: var(--space-2);
	}
</style>
