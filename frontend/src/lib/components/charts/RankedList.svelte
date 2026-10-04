<script lang="ts" generics="Row extends { value: number; said: string }">
	/*
	 * THE TOP FEW OF SOMETHING, ranked, with a bar behind each row as long as its share of the top.
	 *
	 * The bar makes the gaps between the ranks visible at a glance (the first may be twice the
	 * second, or barely ahead of it) where a column of figures leaves that to arithmetic. It sits
	 * behind the words as a quiet tint of the accent, so the name stays the thing read. The name is
	 * in full ink, never the link colour, because a column of blue names makes blue the section's
	 * text colour; the link colour comes back under the pointer. The figure is right-aligned in
	 * tabular digits so a column of them lines up.
	 *
	 * `lead` draws the first row as the list's headline, the way a chart of the week leads with its
	 * number one: its picture large, its name and figure in display type, the rank in the accent's
	 * tint, and the rest ranked under it by number. The data is the same five rows; only the first
	 * is given the room its rank has earned.
	 */
	import type { Snippet } from 'svelte';

	import { SectionHeading } from '$lib/components/common';
	import { BEHIND } from '$lib/components/charts/series';

	interface Props {
		/** The heading over the list; none where the words around it already name it. */
		title?: string;
		rows: readonly Row[];
		/** How many rows to draw; the rest are left to the thing's own wall. */
		most?: number;
		/** The row's name: a link to the thing, or its words. */
		name: Snippet<[Row]>;
		/** The row's picture, where it has one. */
		cover?: Snippet<[Row]>;
		/** Lead with the first row, drawn large, and number the rest. */
		lead?: boolean;
	}

	let { title, rows, most = 5, name, cover, lead = false }: Props = $props();

	const shown = $derived(rows.slice(0, most));
	const first = $derived(lead ? (shown[0] ?? null) : null);
	const rest = $derived(lead ? shown.slice(1) : shown);
	const top = $derived(Math.max(0, ...shown.map((row) => row.value)));
</script>

<section class="ranked-list">
	{#if title}
		<SectionHeading band>{title}</SectionHeading>
	{/if}
	{#if first}
		<div class="first">
			{#if cover}
				<span class="lead-cover">{@render cover(first)}</span>
			{/if}
			<span class="first-words">
				<span class="rank">1</span>
				<span class="name first-name">{@render name(first)}</span>
				<span class="first-figure">{first.said}</span>
			</span>
		</div>
	{/if}
	<ol start={first ? 2 : 1}>
		{#each rest as row, index (index)}
			<li>
				{#if lead}
					<span class="rank small">{index + 2}</span>
				{/if}
				<span
					class="share"
					aria-hidden="true"
					style:inline-size={`${top > 0 ? (row.value / top) * 100 : 0}%`}
					style:background-color={BEHIND}
				></span>
				{#if cover}
					<span class="cover">{@render cover(row)}</span>
				{/if}
				<span class="name">{@render name(row)}</span>
				<span class="figure">{row.said}</span>
			</li>
		{/each}
	</ol>
</section>

<style>
	.ranked-list {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		min-inline-size: 0;
	}

	ol {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	li {
		position: relative;
		display: flex;
		align-items: center;
		gap: var(--space-2);
		min-inline-size: 0;
		padding: var(--space-1) var(--space-2);
		font: var(--text-body-sm);
	}

	/* The bar behind the row, from its start edge, as long as the row's share of the top. Painted
	   from the chart palette in the markup, as every other chart's marks are. */
	.share {
		position: absolute;
		inset-block: 0;
		inset-inline-start: 0;
		border-radius: var(--radius-sm);
	}

	.cover,
	.name,
	.figure {
		position: relative;
	}

	/* The first row, led with: its picture large beside its rank, name and figure. */
	.first {
		display: flex;
		align-items: flex-end;
		gap: var(--space-4);
		min-inline-size: 0;
		padding-block-end: var(--space-2);
	}

	.lead-cover {
		flex: none;
		inline-size: var(--lead-cover);
		block-size: var(--lead-cover);
		overflow: hidden;
		border-radius: var(--radius-md);
		box-shadow: var(--elev-2);
	}

	.first-words {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		min-inline-size: 0;
	}

	.rank {
		font: var(--text-display);
		letter-spacing: var(--tracking-display);
		font-variant-numeric: tabular-nums;
		color: var(--sift-accent-tint-1);
	}

	.rank.small {
		position: relative;
		flex: none;
		min-inline-size: var(--space-4);
		font: var(--text-label);
		letter-spacing: normal;
		color: var(--sift-ink-3);
	}

	.first-name {
		overflow: hidden;
		font: var(--text-h2);
		letter-spacing: var(--tracking-h2);
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.first-figure {
		font: var(--text-body-sm);
		font-variant-numeric: tabular-nums;
		color: var(--sift-ink-2);
	}

	.cover {
		flex: none;
		inline-size: var(--list-cover);
		block-size: var(--list-cover);
		overflow: hidden;
		border-radius: var(--radius-sm);
	}

	.name {
		flex: 1;
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		color: var(--sift-ink);
	}

	/* The name's link reads as the name: full ink, the link colour and its underline under the
	   pointer only. */
	.name :global(a) {
		color: var(--sift-ink);
		text-decoration: underline;
		text-decoration-color: transparent;
		text-underline-offset: 3px;
		transition:
			color var(--dur-instant) var(--ease),
			text-decoration-color var(--dur-instant) var(--ease);
	}

	.name :global(a:hover),
	.name :global(a:focus-visible) {
		color: var(--sift-accent-text);
		text-decoration-color: currentColor;
	}

	.figure {
		flex: none;
		font-variant-numeric: tabular-nums;
		color: var(--sift-ink-2);
	}
</style>
