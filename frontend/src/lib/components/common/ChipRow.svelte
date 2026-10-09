<script lang="ts" module>
	/* WHY NOT BITS-UI: bits-ui has no chip and therefore no row of them. This is layout only: a wrapping
	line with a gap in it, with no behaviour to own. */
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'ChipRow',
		category: 'primitive',
		role: 'a wrapping row of chips with one gap, so no chip carries a margin',
		basis: 'own',
		states: ['default']
	} satisfies DesignEntry;

	/** `tight` for a row belonging to the thing above it; `normal` for a row to choose from. */
	export type ChipRowGap = 'tight' | 'normal';
</script>

<script lang="ts">
	/* A wrapping row of chips, as a list: the one place for the rules that undo a `<ul>`. */
	import type { Snippet } from 'svelte';

	interface Props {
		gap?: ChipRowGap;
		/** What the row is, for anybody who cannot see that the chips belong together. */
		label?: string;
		/** The `<li>` elements, the caller's. */
		children: Snippet;
	}

	let { gap = 'normal', label, children }: Props = $props();
</script>

<ul class="chip-row {gap}" aria-label={label}>
	{@render children()}
</ul>

<style>
	.chip-row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.normal {
		gap: var(--space-2);
	}

	.tight {
		gap: var(--space-1);
	}
</style>
