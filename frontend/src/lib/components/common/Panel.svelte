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

	/* The box everything stands in: `raised` a step lighter, `recessed` a step darker, `caution`
	 * on the warn pair for something to read twice, its words left in ordinary ink. */
	import type { Snippet } from 'svelte';

	interface Props {
		children: Snippet;
		tone?: PanelTone;
		/** Room inside: `sm` controls, `md` words, `xs` menu rows (concentric with `lg`). */
		inset?: 'xs' | 'sm' | 'md';
		/** The corner. `lg` for a panel on the page, `md` for one inside something else. */
		corner?: 'md' | 'lg';
		/** Lifted off the page with a shadow, for a panel that floats over content. Floats too. */
		elevated?: boolean;
		/** Over the screen (a bar's panel): the flat card tone, no light of its own. */
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

	/* A card on the page, lit top-left; the edge is a layer under a transparent border. */
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

	/* The edge carries the colour too, as the tint alone barely shows in a dark theme. */
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
