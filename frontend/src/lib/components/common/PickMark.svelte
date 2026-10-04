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

	/**
	 * What a pick on a wall is for. The look is one; only the mark in the middle says which.
	 *
	 * `refused` is the same shape in the danger colour: in swap mode, a card or a tile that will
	 * NOT go (Kept local, or "Don't swap", on it or on something it is filed under). One state of
	 * this thing rather than a second overlay, so a refused card and a picked one are the same
	 * mark read two ways, and a thing is never both: what will not go cannot be picked.
	 */
	export type PickPurpose = 'filter' | 'swap' | 'refused';

	/**
	 * The mark for each purpose, and both are FILLED shapes: a mark that is only an outline reads
	 * lighter than one that is solid at the same size, so a swap pick would look fainter than a
	 * filter pick. The swap's arrows have no filled form of their own, so theirs is the arrows
	 * inside a filled disc.
	 */
	export const PICK_MARKS: Readonly<Record<PickPurpose, IconName>> = {
		filter: 'filter_alt',
		swap: 'swap_horizontal_circle',
		// The menus' own "Don't swap" glyph, filled.
		refused: 'do_not_disturb_on'
	};
</script>

<script lang="ts">
	/*
	 * WHY NOT BITS-UI: a wash and a glyph over a picture. There is no behaviour in it and bits-ui has no primitive for one.
	 *
	 * A thing picked on a wall: the accent wash over its picture and the filled mark in the middle.
	 *
	 * One component for a media tile and an entity card, so a pick looks the same whatever it was
	 * picked on and whatever it is for. It fills the nearest positioned box it is placed in, the
	 * picture, and takes no presses: pressing the thing again is what takes the pick off.
	 *
	 * Two things are the host's to say, as custom properties on an ancestor, because only the host
	 * knows them: `--pick-radius` (its picture's corner, where nothing clips it) and `--pick-layer`
	 * (the layer above the picture and under its own corner controls).
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
	/* A wash strong enough to find a picked thing at a glance on a wall of photographs, and still
	   light enough that the picture under it is recognizable. */
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

	/* The accent, with the scrim's shadow that keeps it legible over a pale picture. No ground of
	   its own: the wash already tints the whole picture. */
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
