<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'RatingChoices',
		category: 'control',
		role: 'the stars or the number a rating is picked from',
		basis: 'bits-ui:ContextMenu',
		states: ['stars', 'number']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/*
	 * The answers a rating can have, as a list to pick from.
	 *
	 * Written once and rendered in two places (above the button in the selection bar, out to the
	 * side in the right-click menu) for the same reason the verbs themselves are declared once:
	 * two hand-built copies of the same rows are two lists that can stop agreeing, and the one
	 * that would drift is the one nobody looks at.
	 *
	 * Rows rather than a live row of stars, because this is a chooser. Each row shows what the
	 * rating would look like, so picking three stars means looking at three stars rather than
	 * aiming at the third one, and the row that clears it is a row, with a word on it, instead of
	 * the undiscoverable "click the star it already has".
	 */
	import { ContextMenu } from 'bits-ui';
	import Separator from './Separator.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { ratingScale } from '$lib/library/rating.svelte';

	interface Props {
		/** The rating everything being acted on shares, or null when they do not share one. */
		rating: number | null;
		/** Chosen. Null clears the rating, which is a different thing from zero: there is no zero. */
		onpick: (rating: number | null) => void;
		/**
		 * Draw these as rows of a menu rather than as a panel of buttons.
		 *
		 * Not cosmetic. Inside a menu the rows have to BE menu rows, or the menu never learns that
		 * anything was chosen and stays open over the file it just rated. Set where this is rendered
		 * into a submenu; left alone in the flyout above the selection bar, which owns its own open
		 * state and closes itself.
		 */
		menu?: boolean;
	}

	let { rating, onpick, menu = false }: Props = $props();

	/* Stored in, stored out: the same contract `Stars` has, and for the same reason. `rating` is
	   always out of ten; how many rows there are is the account's scale. */
	const outOf = $derived(ratingScale.stars);
	const STARS = $derived(Array.from({ length: outOf }, (_, index) => outOf - index));

	/** The stored rating as a number of stars. Every comparison below is against this. */
	const set = $derived(ratingScale.shown(rating));

	/* The group's value. A rating of nothing is a value no row carries, rather than the empty
	 * string, so "none of them" cannot be mistaken for a row whose value happens to be blank. */
	const chosen = $derived(set === null ? 'none' : String(set));

	function label(value: number): string {
		return `${value} ${value === 1 ? 'star' : 'stars'}`;
	}
</script>

<!--
	One star and the number, not a row of stars drawn up to it.

	A row of stars is ten glyphs per option, ten options, a hundred stars in a menu, and reading
	it means COUNTING, which is the one thing a number is for. Worse, such rows differ from each
	other by one glyph at one end, so telling four from five is a comparison of two nearly
	identical bars rather than reading a digit.

	The star is still here because the star is what says these are ratings rather than a list of
	numbers, and it fills up to the chosen one so the row somebody is on still lights.
-->
{#snippet face(value: number)}
	<span class="row" aria-hidden="true">
		<Icon name="star" size={16} filled={set !== null && value <= set} />
		<span class="count">{value}</span>
	</span>
{/snippet}

<!-- The rows of a menu. Each one has to BE a menu row: a plain button inside a submenu is chosen
     without the menu ever hearing about it, so the menu stays open over the file it just rated.
     Radio rather than plain rows, because these are one value picked out of several. -->
{#snippet menuRows()}
	{#each STARS as value (value)}
		<ContextMenu.RadioItem value={String(value)} textValue={label(value)}>
			{#snippet child({ props })}
				<button
					{...props}
					type="button"
					class="choice"
					class:current={value === set}
					aria-label={label(value)}
				>
					{@render face(value)}
				</button>
			{/snippet}
		</ContextMenu.RadioItem>
	{/each}

	<!-- Not one of the stars. Taking a rating away is an action, not one more value.

	     The line above it is an ELEMENT between the rows rather than an edge on the row itself. As a
	     `border-block-start` it would be part of the row's own box, so the row's highlighted ground
	     would be squared off at the top and ruled across while every other row in the flyout lights
	     as a rounded block. The shared `Separator` rather than the menu's own, because these rows
	     are drawn twice:
	     in a submenu, and in the flyout above the selection bar, which has no menu around it for
	     `ContextMenuSeparator` to find. -->
	{#if rating !== null}
		<Separator />
		<ContextMenu.Item onSelect={() => onpick(null)} textValue="No rating">
			{#snippet child({ props })}
				<button {...props} type="button" class="choice"> No rating </button>
			{/snippet}
		</ContextMenu.Item>
	{/if}
{/snippet}

{#if menu}
	<!-- The group renders THIS element rather than one of its own, so the panel's geometry stays
	     scoped CSS in this file. A class handed to a component as a prop is not reached by it. -->
	<ContextMenu.RadioGroup
		value={chosen}
		onValueChange={(next: string) => onpick(ratingScale.stored(Number(next)))}
	>
		{#snippet child({ props })}
			<div {...props} class="choices">
				{@render menuRows()}
			</div>
		{/snippet}
	</ContextMenu.RadioGroup>
{:else}
	<div class="choices" role="group" aria-label="Rating">
		{#each STARS as value (value)}
			<button
				type="button"
				class="choice"
				class:current={value === set}
				aria-label={label(value)}
				aria-pressed={value === set}
				onclick={() => onpick(ratingScale.stored(value))}
			>
				{@render face(value)}
			</button>
		{/each}

		<!-- Only when there is something to take back. A permanent "no rating" row on something that
		     is already unrated is a row that does nothing, at the end of every menu. -->
		{#if rating !== null}
			<Separator />
			<button type="button" class="choice" onclick={() => onpick(null)}> No rating </button>
		{/if}
	</div>
{/if}

<style>
	/* As wide as the widest row in it, which is the words "No rating", with no floor of its own: a
	   column of a glyph and a digit drawn at a menu's floor would be half empty. The surface's
	   floor is lifted by the `fit` prop on the row that opens this out. */
	.choices {
		display: flex;
		flex-direction: column;
	}

	/* The line between the stars and the row that takes a rating away. Room either side of it, the
	   way a menu separates its groups. */
	.choices :global(.separator) {
		margin-block: var(--space-1);
	}

	.choice {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		/* The app's row inset. See `--menu-row-padding` in `app.css`. These rows are a menu's rows
		   and must be inset like them. */
		padding: var(--menu-row-padding);
		border: 0;
		border-radius: var(--menu-row-radius);
		background: transparent;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
		text-align: start;
		cursor: pointer;
	}

	/* `data-highlighted` is the menu's own idea of where the keyboard is. Without it the rows only
	   respond to a pointer, and arrow-keying down the flyout moves an invisible cursor. */
	.choice {
		transition:
			background var(--dur-instant) var(--ease),
			color var(--dur-instant) var(--ease);
	}

	/* A finger's height on a phone, as every menu row is there (`ContextMenu`): these are a menu's
	   rows by another name, in the sheet a star opens. */
	@media (max-width: 767px) {
		.choice {
			min-block-size: var(--touch-target);
		}
	}

	.choice:hover,
	.choice[data-highlighted] {
		background: var(--menu-row-highlight);
		color: var(--sift-ink);
	}

	.choice:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	/* The one it already has, in the colour the stars are drawn in everywhere else. */
	.choice.current {
		color: var(--sift-star);
	}

	.row {
		display: inline-flex;
		align-items: center;
		gap: var(--space-2);
	}

	/* Tabular, so the column of numbers down the menu is a column rather than a ragged edge: the
	   same reason every other figure in this interface is. */
	.count {
		font-variant-numeric: tabular-nums;
	}
</style>
