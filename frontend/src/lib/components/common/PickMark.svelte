<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';
	import type { IconName } from '$lib/design/icons';

	export const design = {
		name: 'PickMark',
		category: 'primitive',
		role: 'the one look of a pick on a wall: the accent wash over the picture and a filled mark in the middle saying what the pick is for, or in swap mode the danger wash and the do-not-swap mark on what will not go',
		basis: 'own',
		states: ['filter', 'swap', 'refused']
	} satisfies DesignEntry;

	/** What a pick is for; `refused` (will not swap) is the same mark in the danger colour. */
	export type PickPurpose = 'filter' | 'swap' | 'refused';

	/** Each purpose's mark, all filled so no pick looks fainter. */
	export const PICK_MARKS: Readonly<Record<PickPurpose, IconName>> = {
		filter: 'filter_alt',
		swap: 'swap_horizontal_circle',
		// The menus' own "Don't swap" glyph, filled.
		refused: 'do_not_disturb_on'
	};
</script>

<script lang="ts">
	/*
	 * WHY NOT BITS-UI: a wash and a glyph over a picture. There is no behaviour in it and bits-ui
	 * has no primitive for one. A picked thing on a wall: the accent wash and its mark, filling the
	 * nearest positioned box. The host sets `--pick-radius` and `--pick-layer`.
	 */
	import Icon from '$lib/components/Icon.svelte';

	interface Props {
		purpose: PickPurpose;
	}

	let { purpose }: Props = $props();
</script>

<span class="pick" data-purpose={purpose} aria-hidden="true">
	<span class="mark"><Icon name={PICK_MARKS[purpose]} filled size={34} /></span>
</span>

<style>
	/* Strong enough to find, light enough to see through. */
	.pick {
		position: absolute;
		inset: 0;
		z-index: var(--pick-layer, 1);
		display: grid;
		place-items: center;
		border-radius: var(--pick-radius, 0);
		background: var(--sift-pick-wash);
		pointer-events: none;
	}

	/* The accent with the scrim's shadow, no ground. */
	.mark {
		display: grid;
		place-items: center;
		color: var(--sift-accent);
		filter: drop-shadow(0 1px 2px var(--sift-scrim));
	}

	/* What will not go: the same wash and mark in the danger colour. */
	.pick[data-purpose='refused'] {
		background: var(--sift-refused-wash);
	}

	.pick[data-purpose='refused'] .mark {
		color: var(--sift-bad-text);
	}
</style>
