<script lang="ts">
	/*
	 * BARS ON ONE BASELINE, EACH SPLIT INTO ITS SERIES: a day to a bar, an hour, or a month.
	 *
	 * Length from a common baseline is the encoding people read most accurately, so every chart of
	 * amounts over time is this one: a week's seven days, a month's days, a year's months, the
	 * twenty-four hours. The parts stack in the order the series are given, always that order, so a
	 * colour keeps its place as the figures move. The whole width is the chart's; a bar grows only
	 * to `--chart-bar-width`, past which it reads as a block rather than a mark.
	 *
	 * The bar that is still counting (today, this month) is hatched: its figure will grow, and the
	 * hatch says so without a word. A bar's figures are its tooltip, under the pointer or the
	 * keyboard (`Pointing`); every figure is also on the Stats view. Heights are shares of the
	 * tallest bar, whose figure is the quiet mark at the top of the scale.
	 */
	import type { Snippet } from 'svelte';

	import { Pointing } from '$lib/components/charts/pointing.svelte';
	import type { ChartBar, Series } from '$lib/components/charts/series';
	import { CHART_WORDS } from '$lib/components/charts/words';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	interface Props {
		bars: readonly ChartBar[];
		/** The series, in the order they stack from the baseline and the key reads. */
		series: readonly Series[];
		/** The bar still counting, drawn hatched. */
		today?: number | null;
		format: (value: number) => string;
		/** What the chart is of: its name to assistive technology. */
		label: string;
		/** The one sentence under the chart. */
		caption?: Snippet;
		/** A bar whose tooltip stays up whatever the pointer does: the DESIGN GALLERY's open bubble. */
		held?: number | null;
	}

	let { bars, series, today = null, format, label, caption, held = null }: Props = $props();

	/* How many labels fit under the bars before they run into each other. A month's thirty-one
	   days are labelled every other day, the hours every third; every bar is still drawn. */
	const MOST_LABELS = 16;

	const paint = $derived(new Map(series.map((one) => [one.id, one])));
	const totals = $derived(bars.map((bar) => bar.parts.reduce((sum, p) => sum + p.value, 0)));
	const top = $derived(Math.max(0, ...totals));
	const drawn = $derived(
		series.filter((one) =>
			bars.some((bar) => bar.parts.some((p) => p.series === one.id && p.value > 0))
		)
	);
	const every = $derived(Math.max(1, Math.ceil(bars.length / MOST_LABELS)));

	/** A bar's parts in the series' order, the empty ones left out. */
	function partsOf(bar: ChartBar) {
		return series.flatMap((one) => bar.parts.filter((p) => p.series === one.id && p.value > 0));
	}

	const whenOf = (index: number) =>
		index === today ? `${bars[index].label}, ${CHART_WORDS.soFar}` : bars[index].label;

	const pointing = new Pointing(() => bars.length);

	/** A bar said in words: when, then each part, for the plot's name while the bar is pointed. */
	function saidOf(index: number): string {
		const parts = partsOf(bars[index]).map(
			(part) => `${paint.get(part.series)?.label ?? part.series} ${format(part.value)}`
		);
		return [whenOf(index), ...(parts.length > 0 ? parts : [format(0)])].join(', ');
	}
</script>

<figure class="bar-chart">
	{#if drawn.length > 1}
		<ul class="key" aria-hidden="true">
			{#each drawn as one (one.id)}
				<li><span class="swatch" style:background-color={one.paint}></span>{one.label}</li>
			{/each}
		</ul>
	{/if}

	<p class="scale" aria-hidden="true">
		{#if top > 0}{CHART_WORDS.top} {format(top)}{/if}
	</p>

	<!-- One tab stop for the whole chart; the arrows move along the bars (`Pointing`). -->
	<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
	<div
		class="plot"
		role="group"
		tabindex="0"
		aria-label={pointing.at === null ? label : `${label}: ${saidOf(pointing.at)}`}
		onkeydown={pointing.keydown}
		onfocus={pointing.focus}
		onblur={pointing.blur}
	>
		{#each bars as bar, index (index)}
			{#snippet detail()}
				<span class="tip">
					{#each partsOf(bar) as part (part.series)}
						<span class="part-words"
							><span class="swatch" style:background-color={paint.get(part.series)?.paint}
							></span>{paint.get(part.series)?.label ?? part.series}
							<span class="figure">{format(part.value)}</span></span
						>
					{:else}
						<span class="part-words">{format(0)}</span>
					{/each}
				</span>
			{/snippet}
			<div
				class="column"
				class:pointed={pointing.at === index || held === index}
				class:today={today === index}
				aria-hidden="true"
				onpointerenter={() => pointing.point(index)}
				onpointerleave={() => pointing.point(null)}
			>
				<Tooltip
					label={whenOf(index)}
					{detail}
					placement="top"
					stretch
					shrinks
					held={pointing.held(index) || held === index}
				>
					<div class="mark">
						<div class="room">
							{#if top > 0 && totals[index] > 0}
								<div class="stack" style:block-size={`${(totals[index] / top) * 100}%`}>
									{#each partsOf(bar) as part (part.series)}
										<span
											class="part"
											data-series={part.series}
											style:flex-grow={part.value}
											style:background-color={paint.get(part.series)?.paint}
										></span>
									{/each}
								</div>
							{/if}
						</div>
						<span class="tick">{index % every === 0 ? bar.label : ''}</span>
					</div>
				</Tooltip>
			</div>
		{/each}
	</div>

	{#if caption}
		<figcaption>{@render caption()}</figcaption>
	{/if}
</figure>

<style>
	.bar-chart {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		min-inline-size: 0;
		margin: 0;
	}

	.key {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-1) var(--space-4);
		margin: 0;
		padding: 0;
		list-style: none;
		font: var(--text-label);
		color: var(--sift-ink-2);
	}

	.key li,
	.part-words {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
	}

	.swatch {
		display: inline-block;
		inline-size: var(--chart-key-mark);
		block-size: var(--chart-key-mark);
		border-radius: var(--radius-sm);
	}

	/* The top of the scale: a quiet mark over the plot, one line tall whether or not it says anything. */
	.scale {
		min-block-size: 1lh;
		margin: 0;
		font: var(--text-micro);
		color: var(--sift-ink-3);
	}

	/* A bar's tooltip: a part to a line, its figure in the bubble's full ink. */
	.tip {
		display: grid;
		gap: var(--space-1);
		color: var(--sift-ink-2);
	}

	.figure {
		color: var(--sift-ink);
		font-variant-numeric: tabular-nums;
	}

	/* The bars, side by side and equal in width, on one baseline. No gap between the columns, so
	   the baseline is one unbroken line; each bar is one mark gap narrower than its column, so
	   neighbouring bars still read as separate marks. */
	.plot {
		display: grid;
		grid-auto-flow: column;
		grid-auto-columns: minmax(0, 1fr);
		block-size: var(--chart-height);
	}

	/* A column holds its tooltip, which holds the bar and its tick: each box fills the one around it. */
	.column {
		display: flex;
		min-inline-size: 0;
	}

	.mark {
		display: flex;
		flex: 1;
		flex-direction: column;
		min-inline-size: 0;
	}

	/* The baseline is the foot of this box, drawn as a hairline under every bar alike. */
	.room {
		display: flex;
		flex: 1;
		align-items: flex-end;
		min-block-size: 0;
		border-block-end: 1px solid var(--sift-line);
	}

	/* One bar: its parts stacked from the baseline up, a mark gap of ground between them, the top
	   rounded where the data ends and square where it meets the baseline. */
	.stack {
		display: flex;
		flex-direction: column-reverse;
		gap: var(--chart-mark-gap);
		inline-size: calc(100% - var(--chart-mark-gap));
		max-inline-size: var(--chart-bar-width);
		margin-inline: auto;
		overflow: hidden;
		border-start-start-radius: var(--radius-sm);
		border-start-end-radius: var(--radius-sm);
	}

	.part {
		flex-basis: 0;
		transition: filter var(--dur-instant) var(--ease);
	}

	.pointed .part {
		filter: brightness(1.15);
	}

	/* Still counting: stripes of the ground across the bar, the mark gap wide. */
	.today .part {
		background-image: repeating-linear-gradient(
			135deg,
			transparent 0 var(--chart-mark-gap),
			var(--sift-bg) var(--chart-mark-gap) calc(2 * var(--chart-mark-gap))
		);
	}

	/* Every tick is one line tall, labelled or not. The columns are flex columns, so an empty tick
	   taking no height would give its bar the extra room and stand it lower than its neighbours:
	   the baseline has to be the same line under every bar. */
	.tick {
		min-block-size: 1lh;
		overflow: hidden;
		font: var(--text-micro);
		color: var(--sift-ink-3);
		text-align: center;
		white-space: nowrap;
	}

	figcaption {
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}
</style>
