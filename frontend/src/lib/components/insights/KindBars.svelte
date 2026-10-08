<script lang="ts" module>
	import type { components } from '$lib/api/schema';
	import { wordsOf } from '$lib/components/insights/figures';

	type Chart = components['schemas']['Chart'];

	/** A chart's figures in the server's words: every bar's whole and every part. */
	export function chartWords(chart: Chart): (value: number) => string {
		return wordsOf(
			[
				...chart.bars.map((bar) => ({
					value: bar.parts.reduce((sum, p) => sum + p.value, 0),
					said: bar.said
				})),
				...chart.bars.flatMap((bar) => bar.parts)
			],
			chart.unit
		);
	}
</script>

<script lang="ts">
	/*
	 * A BLOCK'S ONE CHART, as the server sent it, handed to the chart primitive that draws it.
	 *
	 * Bars over time (a day's hours, a week's days, a month's, a year's months, the library's
	 * growth) are `BarChart`; one whole split into its shares (By kind) is `ShareBar`. Each part is
	 * a kind, drawn in the kind's own series (`series.ts`), so a colour means one kind everywhere.
	 * The sentence under the chart is the server's, built as pieces like every sentence here.
	 *
	 * Every figure a bar says (its tooltip, the top of the scale) is in the server's words (`said`
	 * on each bar and part, through `wordsOf`), never worded here. `ring` draws the twenty-four
	 * hours of a day as the hour ring instead of bars: the When block's hours, a favourite time of
	 * day read the way a clock face is.
	 */
	import BarChart from '$lib/components/charts/BarChart.svelte';
	import HourRing from '$lib/components/charts/HourRing.svelte';
	import ShareBar from '$lib/components/charts/ShareBar.svelte';
	import HistorySentence from '$lib/components/common/HistorySentence.svelte';

	import { timeWords } from '$lib/components/insights/figures';
	import { seriesOf } from '$lib/components/insights/series';
	import { INSIGHTS_WORDS } from '$lib/components/insights/words';

	interface Props {
		chart: Chart;
		/** What the chart is of, its name to assistive technology: the block's title. */
		label: string;
		/** Draw a day's twenty-four hours as the hour ring rather than as bars. */
		ring?: boolean;
	}

	let { chart, label, ring = false }: Props = $props();

	const series = $derived(seriesOf(chart.bars.flatMap((bar) => bar.parts.map((p) => p.kind))));
	const bars = $derived(
		chart.bars.map((bar) => ({
			label: bar.label,
			parts: bar.parts.map((p) => ({ series: p.kind, value: p.value }))
		}))
	);
	const words = $derived(chartWords(chart));
	const hours = $derived(chart.bars.map((bar) => bar.parts.reduce((sum, p) => sum + p.value, 0)));
	const asRing = $derived(ring && hours.length === 24);
	/* The favourite hour, for the middle of the ring: the one with the most, the earliest on a tie. */
	const favourite = $derived(hours.indexOf(Math.max(...hours)));
	const said = $derived(chart.caption ?? []);
</script>

{#snippet sentence()}
	<HistorySentence pieces={said} />
{/snippet}

{#snippet hour()}
	<span class="favourite">{timeWords(favourite * 60)}</span>
{/snippet}

{#if asRing}
	<div class="ringed">
		<HourRing {hours} format={words} {label} middle={hour} />
		{#if said.length > 0}
			<p class="caption"><HistorySentence pieces={said} /></p>
		{/if}
	</div>
{:else if chart.kind === 'share'}
	<ShareBar
		parts={series.map((one) => ({
			series: one,
			value: chart.bars[0]?.parts.find((p) => p.kind === one.id)?.value ?? 0
		}))}
		format={words}
		label={INSIGHTS_WORDS.byKind}
	/>
{:else}
	<BarChart
		{bars}
		{series}
		today={chart.today ?? null}
		format={words}
		{label}
		caption={said.length > 0 ? sentence : undefined}
	/>
{/if}

<style>
	.ringed {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	.favourite {
		font: var(--text-display);
		letter-spacing: var(--tracking-display);
		color: var(--sift-accent-tint-1);
	}

	.caption {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}
</style>
