<script lang="ts">
	/*
	 * What fills the tile's overlay: the heart, and the rating if the file has one.
	 *
	 * The writes are optimistic. A heart that waits for a round trip before filling in feels
	 * broken, and the request almost always succeeds, so the value moves immediately and is put
	 * back if the server disagrees. Putting it back matters more than it looks: a heart left
	 * showing a state the server never accepted is a lie the person has no way to notice.
	 */
	import { Heart } from '$lib/components/common';
	import { judge, type Judgement, type Subject } from '$lib/library/judgement.svelte';
	import { onAssetStateChange } from '$lib/library/changes.svelte';
	import { ratingScale } from '$lib/library/rating.svelte';
	import { FAVORITE_MARK, RATING_MARK, tileMarks } from '$lib/grid/tile-marks.svelte';

	type Props = Subject & {
		/** Told what the server ended up holding, so the grid's own copy stays in step. */
		onchange?: (state: Judgement) => void;
	};

	let { id, favorite, rating, onchange }: Props = $props();

	/*
	 * The same state the asset's own screen and the player's controls read, so the heart on a tile
	 * and the heart in the player cannot hold different answers for one file.
	 */
	const judged = judge(() => ({ id, favorite, rating }));

	// The grid keeps its own row, so it is told as well: both when this tile was pressed and when
	// the same file was hearted somewhere else on the screen.
	onAssetStateChange((state) => {
		if (state.asset_id === id) onchange?.({ favorite: state.favorite, rating: state.rating });
	});

	/* Stored out of ten, drawn on the account's scale: the same conversion every other surface
	   that shows a rating makes (`Stars`, `RatingChoices`). A rating is STORED out of ten always;
	   how many stars that is depends on a preference. Written raw against a hard-coded "out of 5",
	   a file rated four stars would say `8 of 5` on its tile, which is not a number that can be
	   read at all. */
	const stars = $derived(ratingScale.shown(judged.rating));
	const outOf = $derived(ratingScale.stars);

	/* The two answers this overlay honours.
	 *
	 * They are asked here rather than by whatever hands this in, because they are facts about a
	 * tile and this is the part of a tile that draws them. The class is the shared one in
	 * `app.css`: the badges at the other end of the tile fade on the same rule, so the two corners
	 * cannot come to disagree about what "only when I point at it" looks like.
	 */
	const showsHeart = $derived(tileMarks.shows(FAVORITE_MARK));
	const showsScore = $derived(tileMarks.shows(RATING_MARK));

	/* The row's two marks in their fixed order, drawn resting-first. See the markup. */
	const BOTTOM_ROW = [FAVORITE_MARK, RATING_MARK] as const;
</script>

<!--
	A heart, and a rating if there is one to report.

	No five stars here. They do not fit: the rows are justified, so tiles in one row share a height
	and differ wildly in width, and a heart plus five targets big enough to hit is most of a
	portrait tile before the duration chip in the same corner is counted. Worse than cramped, they
	would be five small targets sitting on top of the picture they describe: reaching for
	four and landing on three is a silent wrong answer, and the grid is where somebody is skimming
	rather than deciding. Rating belongs where there is room to mean it, which is the asset itself.

	What stays is the number, read-only, so a rated file still says so at a glance without being
	opened. One glyph's worth of chrome for a fact, instead of five controls for a decision nobody
	makes here.
-->
<!--
	The heart and the rating, drawn from one ordered list: whichever is drawn always comes first, the
	one waiting for the hover after it: `tileMarks.restingFirst`, the same answer the marks in the
	opposite corner are drawn by. A waiting mark keeps its space, so in a fixed order a heart
	waiting for the hover would hold a gap open in front of a rating drawn always. The markup order rather
	than CSS `order`, because the heart is a button and the keyboard walks the markup.
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

	/*
	 * The heart, on the same line as the chip at the other end of the row.
	 *
	 * `Heart` is a button and carries `padding: var(--space-1)` so it has a target bigger than its
	 * glyph. That padding makes its box four pixels taller than the duration chip beside it, and with
	 * both anchored to the same bottom edge the taller box puts the heart's ink four pixels low. The
	 * padding is dropped HERE (in the tile, where the whole tile is the real target and the heart
	 * is a small control on it) so the two boxes are the same height and their ink lines up.
	 */
	.controls-row :global(button) {
		padding: 0;
	}

	/*
	 * The rating, said rather than offered, in the same shape as the clock at the other end of the
	 * row. It reads the `--tile-chip-*` tokens the rest of the tile's furniture does, including the
	 * step down on a small tile, which arrives through the variables rather than a second container
	 * query here. `--text-micro` at the token's own size, so this chip matches the weight of the
	 * count above it.
	 *
	 * The shape is `.tile-badge` in `app.css`, the same object as the clock, the legend and the
	 * settings picture. What is left here is only what is true of a badge that is text all the way
	 * through.
	 */
	.rating {
		/* No glyph inside it, so the ink correction is on the badge. See `--tile-chip-ink`. */
		padding-block-start: var(--tile-chip-ink);
		white-space: nowrap;
	}

	/*
	 * No step-down for a small tile, here or in `Tile`. The size control's notches are targets the
	 * justified grid quantises, not tile heights, so which notches fall either side of a height
	 * threshold depends on the window width; furniture keyed to it would follow the window rather
	 * than the setting. Both halves go together, or the rating chip would shrink while the clock
	 * and marks beside it held their size. The browser test measures the rating as well as the
	 * corner for that reason.
	 */

	/*
	 * No heart at the grid's smallest setting.
	 *
	 * 150px, from the size control rather than taste: the steps are 120, 180, 260 and 340, and rows
	 * are justified, so a row asked for 180 routinely settles at 160 (the default grid on a 1280px
	 * window draws 160px tiles). 150 sits above everything the 120 step produces and below
	 * everything the 180 step does.
	 *
	 * `display: none` rather than hiding it: an invisible control is still a tab stop and still
	 * announced. The rating chip goes with it; see the rule.
	 */
	@container (max-height: 150px) {
		/* At the grid's smallest setting the tile is chrome-free: no heart to press and no rating chip
		   either. Both are furniture the picture has no room for when skimming, and the rating is a
		   fact you can still get by opening the file. */
		.controls-row :global(button),
		.rating {
			display: none;
		}
	}
</style>
