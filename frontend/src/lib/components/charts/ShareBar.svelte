<script lang="ts">
	/*
	 * ONE WHOLE, SPLIT INTO ITS SHARES: a single bar the width of its box, and a key under it that
	 * says each part's share and figure in words.
	 *
	 * The one honest drawing of parts of a whole: lengths along one line, which people compare far
	 * better than the angles of a pie. The parts keep the order they are given, a part with nothing
	 * in it is left out, and the shares add up to exactly 100 (`shares`). The key is the reading of
	 * it, so the bar itself is hidden from assistive technology.
	 */
	import { shares, type Series } from '$lib/components/charts/series';

	interface Props {
		parts: readonly { series: Series; value: number }[];
		format: (value: number) => string;
		/** What the whole is: the key's name for assistive technology. */
		label: string;
	}

	let { parts, format, label }: Props = $props();

	const shown = $derived(parts.filter((part) => part.value > 0));
	const percent = $derived(shares(shown.map((part) => part.value)));
</script>

{#if shown.length > 0}
	<figure class="share-bar">
		<div class="whole" aria-hidden="true">
			{#each shown as part (part.series.id)}
				<span
					class="part"
					data-series={part.series.id}
					style:flex-grow={part.value}
					style:background-color={part.series.paint}
				></span>
			{/each}
		</div>
		<ul class="key" aria-label={label}>
			{#each shown as part, index (part.series.id)}
				<li>
					<span class="swatch" style:background-color={part.series.paint}></span>
					<span class="name">{part.series.label}</span>
					<span class="share">{percent[index]}%</span>
					<span class="amount">{format(part.value)}</span>
				</li>
			{/each}
		</ul>
	</figure>
{/if}

<style>
	.share-bar {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		min-inline-size: 0;
		margin: 0;
	}

	/* The parts end to end with a mark gap of ground between them, so two neighbours stay two. */
	.whole {
		display: flex;
		gap: var(--chart-mark-gap);
		block-size: calc(2 * var(--chart-key-mark));
		overflow: hidden;
		border-radius: var(--radius-sm);
	}

	.part {
		flex-basis: 0;
		min-inline-size: var(--chart-mark-gap);
	}

	.key {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2) var(--space-6);
		margin: 0;
		padding: 0;
		list-style: none;
		font: var(--text-label);
	}

	.key li {
		display: inline-flex;
		align-items: center;
		gap: var(--space-2);
	}

	.swatch {
		inline-size: var(--chart-key-mark);
		block-size: var(--chart-key-mark);
		border-radius: var(--radius-sm);
	}

	.name {
		color: var(--sift-ink);
	}

	.share {
		font-variant-numeric: tabular-nums;
		color: var(--sift-ink);
	}

	.amount {
		color: var(--sift-ink-3);
	}
</style>
