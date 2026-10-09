<script lang="ts">
	/*
	 * THE YEAR'S FIRST MONTH AGAINST ITS LAST, as two columns side by side: each the month's
	 * name, what it was mostly, the files viewed most that month as pictures one over the other,
	 * and its time split by kind as one standing bar, every part with its tooltip and its time. A
	 * change of lead is seen before it is read.
	 *
	 * `rows` holds the first month's files and then the last month's, as many of each, so the two
	 * columns stand level; with none, the bar takes the pictures' room.
	 */
	import { Avatar, HistorySentence, Tooltip } from '$lib/components/common';
	import type { components } from '$lib/api/schema';
	import { shares } from '$lib/components/charts/series';
	import { wordsOf } from '$lib/components/insights/figures';
	import { seriesOf } from '$lib/components/insights/series';

	type Chart = components['schemas']['Chart'];
	type NamedRow = components['schemas']['NamedRow'];

	interface Props {
		chart: Chart;
		rows?: readonly NamedRow[];
	}

	let { chart, rows = [] }: Props = $props();

	const series = $derived(seriesOf(chart.bars.flatMap((bar) => bar.parts.map((p) => p.kind))));
	const words = $derived(
		wordsOf(
			chart.bars.flatMap((bar) => bar.parts),
			chart.unit
		)
	);
	const half = $derived(Math.floor(rows.length / 2));
	const months = $derived(
		chart.bars.slice(0, 2).map((bar, at) => {
			const parts = series
				.map((one) => ({
					series: one,
					value: bar.parts.find((part) => part.kind === one.id)?.value ?? 0
				}))
				.filter((part) => part.value > 0);
			const percent = shares(parts.map((part) => part.value));
			const lead = parts.reduce<(typeof parts)[number] | null>(
				(best, part) => (part.value > (best?.value ?? 0) ? part : best),
				null
			);
			return {
				label: bar.label,
				parts: parts.map((part, index) => ({ ...part, percent: percent[index] })),
				lead: lead?.series.label ?? '',
				pictures: half > 0 ? rows.slice(at * half, (at + 1) * half) : []
			};
		})
	);
</script>

<div class="before-after">
	{#each months as month, index (month.label)}
		<section class="month" class:after={index === 1}>
			<p class="month-name">{month.label}</p>
			<p class="lead">{month.lead}</p>
			{#if month.pictures.length > 0}
				<ol class="pictures">
					{#each month.pictures as row, at (row.piece.id ?? at)}
						<li class="picture">
							<Avatar src={row.cover} name={row.piece.text} decorative />
							<span class="name"><HistorySentence pieces={[row.piece]} /></span>
						</li>
					{/each}
				</ol>
			{/if}
			<div
				class="bar"
				class:tall={month.pictures.length === 0}
				role="list"
				aria-label={month.label}
			>
				{#each month.parts as part (part.series.id)}
					{#snippet detail()}
						<span class="time">{words(part.value)}</span>
					{/snippet}
					<span
						class="part"
						role="listitem"
						aria-label={`${part.series.label}: ${words(part.value)}`}
						style:flex-grow={part.value}
						style:background-color={part.series.paint}
					>
						<Tooltip label={part.series.label} {detail} placement="top" stretch shrinks>
							<span class="hit"></span>
						</Tooltip>
					</span>
				{/each}
			</div>
			<ul class="key" aria-hidden="true">
				{#each month.parts as part (part.series.id)}
					<li>
						<span class="swatch" style:background-color={part.series.paint}></span>
						<span class="kind">{part.series.label}</span>
						<span class="share">{part.percent}%</span>
					</li>
				{/each}
			</ul>
		</section>
	{/each}
</div>

<style>
	.before-after {
		display: grid;
		flex: 1;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: var(--space-4);
		min-block-size: 0;
		overflow: hidden;
	}

	.month {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		min-block-size: 0;
		min-inline-size: 0;
	}

	.month-name {
		margin: 0;
		font: var(--text-micro);
		letter-spacing: var(--tracking-micro);
		text-transform: uppercase;
		color: var(--sift-ink-2);
	}

	.lead {
		margin: 0;
		font: var(--story-text-headline);
		color: var(--sift-ink);
	}

	.after .lead {
		color: var(--sift-accent-tint-1);
	}

	/* The month's files one over the other, sharing the column's room. */
	.pictures {
		display: grid;
		flex: 1;
		grid-auto-rows: minmax(0, 1fr);
		gap: var(--space-2);
		min-block-size: 0;
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.picture {
		position: relative;
		display: grid;
		overflow: hidden;
		border-radius: var(--radius-md);
		box-shadow: var(--elev-2);
	}

	.picture > :global(.avatar) {
		grid-area: 1 / 1;
		aspect-ratio: auto;
		block-size: 100%;
	}

	.name {
		position: relative;
		grid-area: 1 / 1;
		align-self: end;
		overflow: hidden;
		padding: var(--space-1) var(--space-2);
		background: var(--sift-scrim);
		font: var(--text-label);
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	/* The month's time by kind, one band across the column; with no pictures, a column of its own. */
	.bar {
		display: flex;
		flex: none;
		gap: var(--chart-mark-gap);
		block-size: var(--space-4);
		overflow: hidden;
		border-radius: var(--radius-sm);
	}

	.bar.tall {
		flex: 1;
		flex-direction: column-reverse;
		block-size: auto;
	}

	.part {
		display: flex;
		min-inline-size: var(--chart-mark-gap);
		min-block-size: var(--chart-mark-gap);
	}

	.hit {
		flex: 1;
	}

	.time {
		font-variant-numeric: tabular-nums;
	}

	.key {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		margin: 0;
		padding: 0;
		list-style: none;
		font: var(--text-label);
		color: var(--sift-ink-2);
	}

	.key li {
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}

	.swatch {
		inline-size: var(--space-2);
		aspect-ratio: 1;
		border-radius: var(--chart-mark-gap);
	}

	.kind {
		flex: 1;
	}

	.share {
		font-variant-numeric: tabular-nums;
		color: var(--sift-ink);
	}
</style>
