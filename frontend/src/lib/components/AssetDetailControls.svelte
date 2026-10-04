<script lang="ts">
	/*
	 * The heart and the stars, as they appear in the detail view, at the trailing end of the action
	 * row.
	 *
	 * They reuse the tile's own components, so a file's opinion controls behave the same
	 * everywhere. Tags are not here: a tag is a thing a file belongs to, so it lives in the band
	 * with People, Sites, collections and photo sets, loaded by `AssetView`.
	 */
	import {
		Button,
		ContextMenu,
		ContextMenuGroup,
		ContextMenuItem,
		Heart,
		MenuButton,
		RatingChip
	} from '$lib/components/common';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import { judge, tally, type Subject } from '$lib/library/judgement.svelte';

	/* Which file, and what it is to you, which is exactly what `judge` is handed below, so it is
	   that type rather than a third copy of the same three fields. The tally is the fourth, and it
	   is beside them rather than inside `Subject` because it is written as an ACT rather than as a
	   value; see `tally` for the whole of why those two cannot share one write. */
	type Props = Subject & { oCount?: number };

	let { id, favorite, rating, oCount = 0 }: Props = $props();

	/* The one piece of state behind every heart and every star in the application. The tile this was
	 * opened from and the player's own controls read the same thing, so none of them can end up
	 * showing something the others do not. */
	const judged = judge(() => ({ id, favorite, rating }));

	/* The same arrangement for the tally, and it reaches the same screens by the same route: a
	   press here is on the change bus a moment later, so a tile drawing this file follows. */
	const counted = tally(() => ({ id, o_count: oCount }));
	/* The figure is the label on screen; the name says what the figure IS, or a screen reader
	   announces a bare "0" between the stars and Add to. Said once, for both shapes. */
	const counterName = $derived(`O counter: ${counted.count}`);
</script>

<!--
	The rating wears the heart's dressing (the same glyph size, hover step and no pill) because both
	say the same kind of thing about the file. The size is set here beside the heart's so the pair
	cannot drift apart.
-->
<div class="detail-controls">
	<Heart favorite={judged.favorite} onchange={judged.setFavorite} size={20} />
	<RatingChip rating={judged.rating} onchange={judged.setRating} size={20} label="This file" />
	<!--
		The third mark, a button rather than a bare mark like the two beside it: it draws a glyph
		and a number, which is a control with content, and the app already has one of those,
		`Button` in its quietest tone. A third bare mark with a digit in it would be a hand-rolled
		control the fence refuses.

		The number is drawn even at nought, where the tile's rating mark hides itself: a tile is
		skimmed and this row is read, and a control that disappears until used is one nobody
		discovers.

		A left press is one more. One fewer and starting again are on the right-click menu, where
		the two rare halves belong. On a phone the press opens those rows as a sheet instead, since a
		phone has no right button and the control has no three dots.

		Dressed as the other two though it is a Button: the heart's box, resting ink and hover step,
		so the row's gaps are even. See `.o-mark` below.
	-->
	<!-- Two parts, a line between them: counting one at a time, then starting the count again. -->
	{#snippet oCounterRows()}
		<ContextMenuGroup>
			<ContextMenuItem label="One more" icon="water_drop" filled onselect={counted.more} />
			<ContextMenuItem
				label="One fewer"
				icon="do_not_disturb_on"
				disabled={counted.count === 0}
				onselect={counted.less}
			/>
		</ContextMenuGroup>
		<ContextMenuGroup>
			<ContextMenuItem
				label="Start again"
				icon="restore_from_trash"
				disabled={counted.count === 0}
				onselect={counted.clear}
			/>
		</ContextMenuGroup>
	{/snippet}
	{#if phoneWidth.yes}
		<!-- A phone has no right button and this control has no three dots, so the press itself is
		     the door: the three rows as a sheet, One more first. -->
		<MenuButton label="O counter">
			{#snippet trigger({ props })}
				<Button
					{...props}
					tone="quiet"
					size="small"
					class="o-mark"
					icon="water_drop"
					iconFilled={counted.count > 0}
					iconSize={20}
					aria-label={counterName}
				>
					{counted.count}
				</Button>
			{/snippet}
			{@render oCounterRows()}
		</MenuButton>
	{:else}
		<ContextMenu label="O counter" triggerClass="o-counter">
			<!-- The figure is the label on screen; the name says what the figure IS, or a screen reader
			     announces a bare "0" between the stars and Add to. -->
			<Button
				tone="quiet"
				size="small"
				class="o-mark"
				icon="water_drop"
				iconFilled={counted.count > 0}
				iconSize={20}
				aria-label={counterName}
				onclick={counted.more}
			>
				{counted.count}
			</Button>

			{#snippet items()}
				{@render oCounterRows()}
			{/snippet}
		</ContextMenu>
	{/if}
</div>

<style>
	/*
	 * No spacing of its own: the action row owns the gaps, and padding here would push the pair off
	 * the line its neighbours are centred on.
	 */
	.detail-controls {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
	}

	/*
	 * The O counter wears the heart's dressing so the three marks on this row read as one row: the
	 * same box, corner, resting ink and 1.18 hover step as `Heart`'s `.heart` and `RatingChip`'s
	 * `.mark`. It steps to the row's full ink rather than a colour, since a drop has no colour that
	 * means it. `AssetDetailControls.svelte.test.ts` reads both files and holds the values
	 * together.
	 *
	 * Scoped to this row and specific enough to outrank the quiet tone without `!important`
	 * (`.quiet` is one class and its hover four; these are three and five).
	 */
	.detail-controls :global(.btn.o-mark) {
		padding: var(--space-1) var(--space-2);
		border-radius: var(--radius-md);
		color: var(--sift-ink-2);
		transition:
			transform var(--dur-fast) var(--ease),
			color var(--dur-fast) var(--ease);
	}

	.detail-controls :global(.btn.o-mark:hover:not(:disabled)) {
		transform: scale(1.18);
		color: var(--sift-ink);
		text-decoration: none;
	}

	:global(:root[data-motion='reduce']) .detail-controls :global(.btn.o-mark) {
		transition: none;
	}
</style>
