<script lang="ts">
	/*
	 * STATS: every figure of a period as tables, the raw view under Insights' story.
	 *
	 * The same answer Insights draws (`GET /api/insights`), block by block in the page's order: the
	 * block's figures as rows (the label, the server's words, the hidden part while the vault is
	 * open, the trend, what the figure counts), its chart's bars and its calendar's days, and each of
	 * its lists whole. A block under its floor is its one line. No other sentence: the story is
	 * Insights', and this is where a figure is looked up. Every table has its Copy, which puts it on
	 * the clipboard as tab-separated text a spreadsheet reads.
	 *
	 * Its own period tabs and arrows (`PeriodBar`) keep the address on this screen, so a reader steps
	 * through periods without leaving the figures, and Back returns to Insights at the same period.
	 */
	import type { Snippet } from 'svelte';

	import {
		Button,
		Problem,
		Scroller,
		SectionHeading,
		Skeleton,
		type Crumb
	} from '$lib/components/common';
	import HistorySentence from '$lib/components/common/HistorySentence.svelte';
	import FiguresTable from '$lib/components/charts/FiguresTable.svelte';
	import { tableText, type TableRow } from '$lib/components/charts/table';
	import { CHART_WORDS } from '$lib/components/charts/words';
	import { figureWords, saidOf, wordsOf, type Figure } from '$lib/components/insights/figures';
	import { PeriodAnswer } from '$lib/components/insights/answer.svelte';
	import { chartWords } from '$lib/components/insights/KindBars.svelte';
	import PeriodBar from '$lib/components/insights/PeriodBar.svelte';
	import {
		STATS_PATH,
		addressOf,
		type InsightsBlock as Block,
		type Place
	} from '$lib/components/insights/period';
	import { seriesOf } from '$lib/components/insights/series';
	import Statements from '$lib/components/insights/Statements.svelte';
	import { INSIGHTS_WORDS, STATS_WORDS } from '$lib/components/insights/words';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import { onAssetStateChange, reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import { copyText } from '$lib/shell/clipboard';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { calendarDay } from '$lib/shell/when';

	let { data }: { data: Place } = $props();

	/* Drawn from other answers on Insights, and not figures of the period. */
	const DRAWN_ELSEWHERE = new Set(['recaps', 'path']);

	const read = new PeriodAnswer(() => data);
	reloadOnLibraryChange(() => void read.reread());
	onAssetStateChange(() => void read.reread());
	const answer = $derived(read.answer);
	const failed = $derived(read.failed);
	const loading = $derived(read.loading);

	const crumbs = $derived<Crumb[]>([
		{ label: INSIGHTS_WORDS.title, href: addressOf(data) },
		{ label: STATS_WORDS.title }
	]);
	const blocks = $derived(answer?.blocks.filter((block) => !DRAWN_ELSEWHERE.has(block.id)) ?? []);

	/** One table as the page draws it and as Copy puts it on the clipboard. */
	interface Table {
		caption: string;
		head: string;
		columns: string[];
		rows: TableRow[];
		ranked?: boolean;
		/** The block whose figures these are, so a definition is drawn as its sentence. */
		figures?: Figure[];
	}

	/* What a figure counts, as text for its copy; the page draws the server's sentence. */
	const definesOf = (figure: Figure) => figure.defines.map((piece) => piece.text).join('');

	function figuresOf(block: Block): Table | null {
		if (block.figures.length === 0) return null;
		const hidden = block.figures.some((figure) => figure.hidden_part > 0);
		const trend = block.figures.some((figure) => (figure.trend ?? []).length > 0);
		const defines = block.figures.some((figure) => definesOf(figure) !== '');
		return {
			figures: block.figures,
			caption: STATS_WORDS.figures,
			head: STATS_WORDS.what,
			columns: [
				STATS_WORDS.figure,
				...(hidden ? [INSIGHTS_WORDS.hidden] : []),
				...(trend ? [STATS_WORDS.trend] : []),
				...(defines ? [STATS_WORDS.defines] : [])
			],
			rows: block.figures.map((figure) => ({
				label: figure.label,
				cells: [
					saidOf(figure.said, figure.value, figure.unit),
					...(hidden
						? [
								figure.hidden_part > 0
									? saidOf(figure.hidden_said, figure.hidden_part, figure.unit)
									: ''
							]
						: []),
					...(trend
						? [(figure.trend ?? []).map((value) => figureWords(value, figure.unit)).join(', ')]
						: []),
					...(defines ? [definesOf(figure)] : [])
				]
			}))
		};
	}

	function barsOf(block: Block): Table | null {
		const chart = block.chart;
		if (!chart || chart.bars.length === 0) return null;
		const series = seriesOf(chart.bars.flatMap((bar) => bar.parts.map((part) => part.kind)));
		const words = chartWords(chart);
		return {
			caption: STATS_WORDS.bars,
			head: CHART_WORDS.when,
			columns: series.map((one) => one.label),
			rows: chart.bars.map((bar, index) => ({
				label: index === chart.today ? `${bar.label}, ${CHART_WORDS.soFar}` : bar.label,
				cells: series.map((one) => words(bar.parts.find((p) => p.kind === one.id)?.value ?? 0))
			}))
		};
	}

	function daysOf(block: Block): Table | null {
		const calendar = block.calendar;
		if (!calendar || calendar.days.length === 0) return null;
		const words = wordsOf(calendar.days, calendar.unit);
		return {
			caption: STATS_WORDS.days,
			head: CHART_WORDS.when,
			columns: [block.title],
			rows: calendar.days.map((one) => ({ label: calendarDay(one.day), cells: [words(one.value)] }))
		};
	}

	function listTable(list: Block['lists'][number]): Table {
		return {
			caption: list.title,
			head: STATS_WORDS.name,
			columns: [STATS_WORDS.figure],
			ranked: true,
			rows: list.rows.map((row) => ({
				label: row.piece.text,
				cells: [saidOf(row.said, row.value, row.unit)]
			}))
		};
	}

	async function copy(table: Table) {
		const heads = [...(table.ranked ? [CHART_WORDS.rank] : []), table.head, ...table.columns];
		if (await copyText(tableText(heads, table.rows, table.ranked))) toasts.show(STATS_WORDS.copied);
		else toasts.show(STATS_WORDS.copyFailed, { tone: 'error' });
	}
</script>

<svelte:head><title>{STATS_WORDS.title}</title></svelte:head>

{#snippet drawn(table: Table, named?: Snippet<[number]>)}
	{#snippet defined(row: number, column: number)}
		{@const figure = table.figures?.[row]}
		{#if figure && column === table.columns.length - 1 && table.columns.at(-1) === STATS_WORDS.defines}
			<p class="defines"><HistorySentence pieces={figure.defines} /></p>
		{:else}
			{table.rows[row].cells[column]}
		{/if}
	{/snippet}
	{#snippet press()}
		<Button size="small" tone="ghost" aria-label={STATS_WORDS.copyLabel} onclick={() => copy(table)}
			>{STATS_WORDS.copy}</Button
		>
	{/snippet}
	<Scroller horizontal>
		<FiguresTable
			caption={table.caption}
			head={table.head}
			columns={table.columns}
			rows={table.rows}
			ranked={table.ranked}
			{named}
			cell={table.figures ? defined : undefined}
			action={press}
		/>
	</Scroller>
{/snippet}

<PageFrame {crumbs}>
	{#snippet header()}
		<PageHeader title={STATS_WORDS.title} icon="insights">
			{#snippet lede()}{STATS_WORDS.headLine}{/snippet}
		</PageHeader>
	{/snippet}
	{#snippet tools()}
		<PeriodBar place={data} {answer} {loading} path={STATS_PATH} />
	{/snippet}

	{#if failed}
		<Problem message={INSIGHTS_WORDS.failed} />
	{:else if answer}
		<div class="stats" class:stale={loading} aria-busy={loading}>
			{#each blocks as block (block.id)}
				<section class="block" data-block={block.id} aria-labelledby={`stats-${block.id}`}>
					<SectionHeading id={`stats-${block.id}`}>{block.title}</SectionHeading>
					{#if !block.floor_reached}
						<Statements lines={block.statements} />
					{:else}
						{@const figures = figuresOf(block)}
						{@const bars = barsOf(block)}
						{@const days = daysOf(block)}
						{#if figures}{@render drawn(figures)}{/if}
						{#if bars}{@render drawn(bars)}{/if}
						{#if days}{@render drawn(days)}{/if}
						{#each block.lists as list, index (index)}
							{@const table = listTable(list)}
							{#snippet name(at: number)}
								<HistorySentence pieces={[list.rows[at].piece]} />
							{/snippet}
							{@render drawn(table, name)}
						{/each}
					{/if}
				</section>
			{/each}
		</div>
	{:else}
		<Skeleton lines={6} />
	{/if}
</PageFrame>

<style>
	.stats {
		display: flex;
		flex-direction: column;
		gap: var(--space-8);
	}

	.block {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
		min-inline-size: 0;
	}

	.defines {
		margin: 0;
		white-space: normal;
		text-align: start;
	}

	.stale {
		opacity: 0.55;
		transition: opacity var(--dur-instant) var(--ease);
	}
</style>
