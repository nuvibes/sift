<script lang="ts" module>
	/* WHY NOT BITS-UI: bits-ui has no chip and therefore no row of them. This is layout only: a wrapping
	   line with a gap in it, and there is no behaviour here for a headless library to own. */
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'ChipRow',
		category: 'primitive',
		role: 'a wrapping row of chips with one gap, so no chip carries a margin',
		basis: 'own',
		states: ['default']
	} satisfies DesignEntry;

	/**
	 * How far apart, in the two spacings a row of chips is ever drawn at.
	 *
	 * `normal` is a row of chips somebody chooses from, which is nearly all of them. `tight` is for
	 * a row that belongs to the thing above it (the filters under a search box, the marks on a
	 * card) where the wider gap makes the row read as a section of its own.
	 */
	export type ChipRowGap = 'tight' | 'normal';
</script>

<script lang="ts">
	/*
	 * A wrapping row of chips.
	 *
	 * ## Why this is a component and not four lines of CSS
	 *
	 * Because the same four lines would otherwise be written on every screen that draws chips:
	 *
	 *     display: flex;  flex-wrap: wrap;  gap: var(--space-2);
	 *     margin: 0;  padding: 0;  list-style: none;
	 *
	 * The same failure the chip itself exists for, one level up: nothing about any single copy is
	 * wrong, and there is nowhere to fix the fact that no two have to agree.
	 *
	 * The `margin: 0; padding: 0; list-style: none` half is the tell. Those three exist only to undo
	 * what a browser does to a `<ul>`, and a rule whose job is undoing the site is a rule that
	 * belongs in one place.
	 *
	 * ## It is a list, and that is not decoration
	 *
	 * The chips in a row are a set of related things, so the markup says so: a screen reader
	 * announces "list, six items" and can move through them, which a row of divs does not offer. The
	 * caller writes the `<li>`s (see the usages) because what goes in one varies and a wrapper
	 * that generated them would have to accept a snippet per item to do the same job.
	 */
	import type { Snippet } from 'svelte';

	interface Props {
		gap?: ChipRowGap;
		/** What the row is, for anybody who cannot see that the chips belong together. */
		label?: string;
		/** The `<li>` elements. See above for why they are the caller's. */
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
