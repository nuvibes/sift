<script lang="ts">
	/*
	 * One panel of the Stats view: its block's name over its title, what its figures count on the
	 * title's mark, its Copy at the end of the line, its sentence, its chart drawn small, and its
	 * table. A list's table is its own chart, a bar behind each name; a figures table sets its
	 * figures large. A long table scrolls inside the panel, which stands no taller than a story card.
	 */
	import { Avatar, Button, Panel, Scroller, SectionHeading, Tooltip } from '$lib/components/common';
	import HistorySentence from '$lib/components/common/HistorySentence.svelte';
	import FiguresTable from '$lib/components/charts/FiguresTable.svelte';
	import HeatMap from '$lib/components/charts/HeatMap.svelte';
	import { tableText } from '$lib/components/charts/table';
	import { CHART_WORDS } from '$lib/components/charts/words';
	import { figureWords, wordsOf } from '$lib/components/insights/figures';
	import KindBars from '$lib/components/insights/KindBars.svelte';
	import Statements from '$lib/components/insights/Statements.svelte';
	import { INSIGHTS_WORDS, STATS_WORDS } from '$lib/components/insights/words';
	import { copyText } from '$lib/shell/clipboard';
	import { toasts } from '$lib/shell/toasts.svelte';

	import type { Panel as Shown, Table } from './panels';
	import Spark from './Spark.svelte';

	interface Props {
		panel: Shown;
		/** The panel a board tile opened the view at. */
		lit?: boolean;
	}

	let { panel, lit = false }: Props = $props();

	const block = $derived(panel.block);
	const table = $derived(panel.table);
	const list = $derived(panel.part.kind === 'list' ? block.lists[panel.part.index] : null);
	/* A figures table is named by its block; any other by its own caption, under its block's name. */
	const byBlock = $derived(panel.part.kind === 'figures' || !table);
	const called = $derived(byBlock ? block.title : table!.caption);
	const heading = $derived(`stats-${panel.id}`);

	/* The definition a press is holding up. */
	let held = $state(false);
	/* The covers the browser could not draw, drawn as the letter instead. */
	let refused = $state<string[]>([]);

	async function copy(one: Table) {
		const heads = [...(one.ranked ? [CHART_WORDS.rank] : []), one.head, ...one.columns];
		if (await copyText(tableText(heads, one.rows, one.ranked))) toasts.show(STATS_WORDS.copied);
		else toasts.show(STATS_WORDS.copyFailed, { tone: 'error' });
	}
</script>

{#snippet named(at: number)}
	{@const row = list!.rows[at]}
	{#if row.cover !== null}
		{@const address = row.cover}
		<span class="cover" onerrorcapture={() => (refused = [...refused, address])}>
			<Avatar
				src={refused.includes(address) ? null : address}
				name={row.piece.text}
				decorative
				lazy
			/>
		</span>
	{/if}
	<HistorySentence pieces={[row.piece]} />
{/snippet}

{#snippet trend(row: number, column: number)}
	{@const figure = table!.figures![row]}
	{@const values = figure.trend ?? []}
	{#if table!.columns[column] === STATS_WORDS.trend && values.length > 0}
		<Spark
			{values}
			labels={table!.labels ?? []}
			format={(value) => figureWords(value, figure.unit)}
			label={`${figure.label}, ${STATS_WORDS.trend}`}
		/>
	{:else}
		{table!.rows[row].cells[column]}
	{/if}
{/snippet}

<!-- DRESSED BY: .tall .wide (the Stats page gives a panel its rows and columns) -->
<article
	class="panel"
	class:wide={panel.wide}
	class:tall={panel.tall}
	class:lit
	id={panel.id}
	data-block={block.id}
	data-family={panel.family}
	tabindex="-1"
	aria-labelledby={heading}
>
	<span class="edge" aria-hidden="true"></span>
	<Panel tone="raised" corner="lg" inset="md">
		<div class="inside">
			<div class="head">
				<div class="titles">
					{#if !byBlock}<span class="eyebrow">{block.title}</span>{/if}
					<SectionHeading band level={3} id={heading}>
						{called}
						{#snippet actions()}
							{#if panel.defines.length > 0}
								<Tooltip label={INSIGHTS_WORDS.defines} {held}>
									{#snippet detail()}
										{#each panel.defines as one (one.label)}
											<span class="defined"
												><strong>{one.label}</strong>
												<HistorySentence pieces={one.pieces} /></span
											>
										{/each}
									{/snippet}
									<Button
										tone="ghost"
										size="small"
										icon="info"
										aria-label={INSIGHTS_WORDS.defines}
										onclick={() => (held = !held)}
										onkeydown={(event: KeyboardEvent) => {
											if (event.key === 'Escape') held = false;
										}}
										onblur={() => (held = false)}
									/>
								</Tooltip>
							{/if}
						{/snippet}
					</SectionHeading>
				</div>
				{#if table}
					{@const copied = table}
					<Button
						size="small"
						tone="ghost"
						icon="content_copy"
						aria-label={STATS_WORDS.copyLabel}
						onclick={() => copy(copied)}>{STATS_WORDS.copy}</Button
					>
				{/if}
			</div>

			{#if panel.part.kind === 'words'}
				<Statements lines={block.statements} />
			{:else}
				{#if panel.sentence}
					<p class="sentence"><HistorySentence pieces={panel.sentence} /></p>
				{/if}
				<div class="body" class:paired={panel.part.kind === 'bars' || panel.part.kind === 'days'}>
					{#if panel.part.kind === 'bars' && block.chart}
						<div class="chart"><KindBars chart={block.chart} label={block.title} /></div>
					{:else if panel.part.kind === 'days' && block.calendar}
						<div class="chart">
							<HeatMap
								days={block.calendar.days}
								format={wordsOf(block.calendar.days, block.calendar.unit)}
								label={block.title}
							/>
						</div>
					{/if}
					{#if table}
						<div class="capped">
							<Scroller horizontal>
								<FiguresTable
									caption={table.caption}
									head={table.head}
									columns={table.columns}
									rows={table.rows}
									ranked={table.ranked}
									shares={table.shares}
									large={panel.part.kind === 'figures'}
									named={list ? named : undefined}
									cell={table.figures ? trend : undefined}
									unseen
								/>
							</Scroller>
						</div>
					{/if}
				</div>
			{/if}
		</div>
	</Panel>
</article>

<style>
	/* The family's colour down the panel's leading edge, the board's colour for the same figures.
	   The holder carries the address and the ring; the box is `Panel`'s. */
	.panel {
		--edge: var(--sift-accent);
		position: relative;
		display: grid;
		min-inline-size: 0;
		border-radius: var(--radius-lg);
		scroll-margin-block-start: var(--space-4);
		container-type: inline-size;
	}

	.panel:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	.panel[data-family='viewing'] {
		--edge: var(--family-viewing-ink);
	}

	.panel[data-family='people'] {
		--edge: var(--family-people-ink);
	}

	.panel[data-family='sites'] {
		--edge: var(--family-sites-ink);
	}

	.panel[data-family='organizing'] {
		--edge: var(--family-organizing-ink);
	}

	.panel[data-family='theater'] {
		--edge: var(--family-theater-ink);
	}

	.panel[data-family='downloads'] {
		--edge: var(--family-downloads-ink);
	}

	.panel[data-family='alongside'] {
		--edge: var(--family-alongside-ink);
	}

	/* The panel a tile opened the view at: ringed in its family's colour, over the focus ring. */
	.panel.lit {
		box-shadow: 0 0 0 var(--focus-width) var(--edge);
	}

	.edge {
		position: absolute;
		inset-block: var(--space-3);
		inset-inline-start: 0;
		z-index: 1;
		inline-size: var(--space-1);
		border-start-end-radius: var(--radius-sm);
		border-end-end-radius: var(--radius-sm);
		background: var(--edge);
	}

	/* One child in the box, so a panel stretched to its row keeps its contents at the top. */
	.inside {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		min-inline-size: 0;
	}

	/* The titles at the start of the line and Copy at its end. */
	.head {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: var(--space-3);
	}

	.titles {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		min-inline-size: 0;
	}

	.eyebrow {
		font: var(--text-micro);
		letter-spacing: var(--tracking-micro);
		text-transform: uppercase;
		color: var(--edge);
	}

	.defined {
		display: block;
	}

	.defined + .defined {
		margin-block-start: var(--space-2);
	}

	.sentence {
		margin: 0;
		font: var(--text-body);
		color: var(--sift-ink-2);
	}

	.body {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
		min-inline-size: 0;
	}

	.chart {
		min-inline-size: 0;
	}

	/* A chart across a whole row stands beside its table rather than over it. */
	@container (min-width: 960px) {
		.paired {
			display: grid;
			grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
			align-items: start;
		}
	}

	/* A long table scrolls inside its panel rather than drawing the page out. */
	.capped :global(.scroll-root) {
		max-block-size: var(--story-height);
	}

	.cover {
		display: inline-flex;
		flex: none;
		inline-size: var(--list-cover);
		block-size: var(--list-cover);
	}
</style>
