<script lang="ts">
	/*
	 * The year's first month and its last, each as its time viewed split by kind: two bars one
	 * over the other, so a change of lead is seen before it is read.
	 */
	import ShareBar from '$lib/components/charts/ShareBar.svelte';
	import type { components } from '$lib/api/schema';
	import { wordsOf } from '$lib/components/insights/figures';
	import { seriesOf } from '$lib/components/insights/series';

	type Chart = components['schemas']['Chart'];

	interface Props {
		chart: Chart;
	}

	let { chart }: Props = $props();

	const series = $derived(seriesOf(chart.bars.flatMap((bar) => bar.parts.map((p) => p.kind))));
	const words = $derived(
		wordsOf(
			chart.bars.flatMap((bar) => bar.parts),
			chart.unit
		)
	);
</script>

<div class="before-after">
	{#each chart.bars as bar (bar.label)}
		<section class="month">
			<p class="month-name">{bar.label}</p>
			<ShareBar
				parts={series.map((one) => ({
					series: one,
					value: bar.parts.find((part) => part.kind === one.id)?.value ?? 0
				}))}
				format={words}
				label={bar.label}
			/>
		</section>
	{/each}
</div>

<style>
	.before-after {
		display: flex;
		flex: 1;
		flex-direction: column;
		justify-content: center;
		gap: var(--space-6);
		min-block-size: 0;
	}

	.month {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.month-name {
		margin: 0;
		font: var(--text-micro);
		letter-spacing: var(--tracking-micro);
		text-transform: uppercase;
		color: var(--sift-ink-2);
	}
</style>
