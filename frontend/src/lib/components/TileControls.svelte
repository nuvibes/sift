<script lang="ts">
	/* The tile's heart and rating. Writes are optimistic and put back if the server disagrees. */
	import { Heart } from '$lib/components/common';
	import { judge, type Judgement, type Subject } from '$lib/library/judgement.svelte';
	import { onAssetStateChange } from '$lib/library/changes.svelte';
	import { ratingScale } from '$lib/library/rating.svelte';
	import { FAVORITE_MARK, RATING_MARK, tileMarks } from '$lib/grid/tile-marks.svelte';

	type Props = Subject & {
		onchange?: (state: Judgement) => void;
	};

	let { id, favorite, rating, onchange }: Props = $props();

	const judged = judge(() => ({ id, favorite, rating }));

	// The grid keeps its own row, so it is told too.
	onAssetStateChange((state) => {
		if (state.asset_id === id) onchange?.({ favorite: state.favorite, rating: state.rating });
	});

	/* Stored out of ten, drawn on the account's scale. */
	const stars = $derived(ratingScale.shown(judged.rating));
	const outOf = $derived(ratingScale.stars);

	const showsHeart = $derived(tileMarks.shows(FAVORITE_MARK));
	const showsScore = $derived(tileMarks.shows(RATING_MARK));

	const BOTTOM_ROW = [FAVORITE_MARK, RATING_MARK] as const;
</script>

<!--
No stars here: a justified row leaves no room for five targets over the picture, so the rating is
read-only. The order is the markup's, not CSS `order`, because the keyboard walks the markup.
-->
{#snippet heartMark()}
	{#if showsHeart}
		<span class={tileMarks.revealed(FAVORITE_MARK)}>
			<Heart favorite={judged.favorite} onchange={judged.setFavorite} size={16} />
		</span>
	{/if}
{/snippet}

{#snippet ratingMark()}
	{#if stars && showsScore}
		<span
			class="rating tile-badge {tileMarks.revealed(RATING_MARK)}"
			aria-label="Rated {stars} out of {outOf}">{stars}&#9733;</span
		>
	{/if}
{/snippet}

<span class="controls-row">
	{#each tileMarks.restingFirst(BOTTOM_ROW) as key (key)}
		{@const draw = { [FAVORITE_MARK]: heartMark, [RATING_MARK]: ratingMark }[key]}
		{@render draw()}
	{/each}
</span>

<style>
	.controls-row {
		display: flex;
		align-items: center;
		gap: var(--space-1);
		width: 100%;
	}

	/* No padding here, so the heart's ink lines up with the chip at the other end of the row. */
	.controls-row :global(button) {
		padding: 0;
	}

	/* The rating, said rather than offered: `.tile-badge`, as the clock is. */
	.rating {
		padding-block-start: var(--tile-chip-ink);
		white-space: nowrap;
	}

	/* No step-down for a small tile: the size notches are quantised by the window width. */

	/* No heart at the smallest setting; `display: none` so it is not a tab stop. */
	@container (max-height: 150px) {
		.controls-row :global(button),
		.rating {
			display: none;
		}
	}
</style>
