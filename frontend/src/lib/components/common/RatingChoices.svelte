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
	/* The answers a rating can have, as a list to pick from, written once for the bar's flyout and
	 * the right-click submenu; rows, so each shows the rating it sets. */
	import { ContextMenu } from 'bits-ui';
	import Separator from './Separator.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { ratingScale } from '$lib/library/rating.svelte';

	interface Props {
		/** The rating everything being acted on shares, or null when they do not share one. */
		rating: number | null;
		/** Chosen. Null clears the rating, which is a different thing from zero: there is no zero. */
		onpick: (rating: number | null) => void;
		/** Rows of a menu, so a submenu hears the choice and closes. */
		menu?: boolean;
	}

	let { rating, onpick, menu = false }: Props = $props();

	/* Stored out of ten; the row count is the account's scale. */
	const outOf = $derived(ratingScale.stars);
	const STARS = $derived(Array.from({ length: outOf }, (_, index) => outOf - index));

	/** The stored rating as a number of stars. Every comparison below is against this. */
	const set = $derived(ratingScale.shown(rating));

	/* No rating is a value no row carries. */
	const chosen = $derived(set === null ? 'none' : String(set));

	function label(value: number): string {
		return `${value} ${value === 1 ? 'star' : 'stars'}`;
	}
</script>

<!-- One star and the number, not a row of stars to count; the star fills to the chosen one. -->

{#snippet face(value: number)}
	<span class="row" aria-hidden="true">
		<Icon name="star" size={16} filled={set !== null && value <= set} />
		<span class="count">{value}</span>
	</span>
{/snippet}

<!-- Menu rows, radio items, so the menu hears the choice. -->
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

	<!-- Clearing is an action, after a separator element, which keeps the row's ground rounded. -->

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
	<!-- The group renders this element, so its geometry stays scoped here. -->
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

		<!-- Only when there is a rating to take back. -->
		{#if rating !== null}
			<Separator />
			<button type="button" class="choice" onclick={() => onpick(null)}> No rating </button>
		{/if}
	</div>
{/if}

<style>
	/* As wide as "No rating", with no floor (`fit`). */
	.choices {
		display: flex;
		flex-direction: column;
	}

	.choices :global(.separator) {
		margin-block: var(--space-1);
	}

	.choice {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		/* The menu's row inset. */
		padding: var(--menu-row-padding);
		border: 0;
		border-radius: var(--menu-row-radius);
		background: transparent;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
		text-align: start;
		cursor: pointer;
	}

	/* `data-highlighted` shows where the keyboard is. */
	.choice {
		transition:
			background var(--dur-instant) var(--ease),
			color var(--dur-instant) var(--ease);
	}

	/* A finger's height on a phone. */
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

	/* Tabular figures. */
	.count {
		font-variant-numeric: tabular-nums;
	}
</style>
