<script lang="ts">
	/* The heart, the stars and the O counter in the detail view, on the tile's own components. */
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

	/* `Subject` plus the tally, which is written as an ACT (`tally`). */
	type Props = Subject & { oCount?: number };

	let { id, favorite, rating, oCount = 0 }: Props = $props();

	/* The one state behind every heart and star. */
	const judged = judge(() => ({ id, favorite, rating }));

	const counted = tally(() => ({ id, o_count: oCount }));
	/* The name says what the figure IS, or a screen reader hears a bare "0". */
	const counterName = $derived(`O counter: ${counted.count}`);
</script>

<div class="detail-controls">
	<Heart favorite={judged.favorite} onchange={judged.setFavorite} size={20} />
	<RatingChip rating={judged.rating} onchange={judged.setRating} size={20} label="This file" />
	<!--
	The O counter: a quiet `Button` with a glyph and a number, drawn at nought too, dressed as the
	heart. A press is one more; the right-click menu (a sheet on a phone) holds the rest.
	-->
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
	/* No spacing of its own: the action row owns the gaps. */
	.detail-controls {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
	}

	/*
	 * The heart's dressing (`AssetDetailControls.svelte.test.ts` holds the values), specific enough
	 * to beat `.quiet`.
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
