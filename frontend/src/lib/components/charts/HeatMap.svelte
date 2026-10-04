<script lang="ts">
	/*
	 * A CALENDAR OF DAYS, EACH A CELL SHADED BY ITS FIGURE: a month, or a year.
	 *
	 * A column is a week, Monday at the top, so a habit (the weekends, one weekday) shows as a row.
	 * The shade is measured against this person's own days (`levels`), never a fixed scale: a light
	 * week of somebody who views a lot and a busy week of somebody who views a little both use the
	 * whole range. A day with nothing is the empty ground, and the shades are the accent's three,
	 * darkest for the least, with a key from less to more. Every cell is the same size in a month
	 * and in a year, a fixed share of the reading measure, so the two maps read as one kind of thing.
	 *
	 * Pointing at a cell says its day and figure in the line above; the table under the drawing says
	 * every day in words, and the drawing is hidden from assistive technology.
	 */
	import type { Snippet } from 'svelte';

	import FiguresTable from '$lib/components/charts/FiguresTable.svelte';
	import { LEVEL_PAINT, levels } from '$lib/components/charts/series';
	import { CHART_WORDS } from '$lib/components/charts/words';
	import { calendarDay } from '$lib/shell/when';

	interface Props {
		/** Every day drawn, in order, each an ISO day with its figure. */
		days: readonly { day: string; value: number }[];
		format: (value: number) => string;
		/** What the map is of, for the table's caption. */
		label: string;
		caption?: Snippet;
	}

	let { days, format, label, caption }: Props = $props();

	/** Days since the Unix epoch of an ISO day: a calendar day has no zone, so it is read as UTC. */
	function dayNumber(iso: string): number {
		const [year, month, day] = iso.split('-').map(Number);
		return Math.floor(Date.UTC(year, month - 1, day) / 86_400_000);
	}

	const first = $derived(days.length > 0 ? dayNumber(days[0].day) : 0);
	/* The weekday of the first day, Monday first: 1970-01-01 was a Thursday. */
	const lead = $derived((((first + 3) % 7) + 7) % 7);
	const level = $derived(levels(days.map((one) => one.value)));

	const cells = $derived(
		days.map((one) => {
			const at = dayNumber(one.day) - first + lead;
			return { ...one, row: (at % 7) + 1, column: Math.floor(at / 7) + 1 };
		})
	);

	let pointed = $state<number | null>(null);
</script>

<figure class="heat-map">
	<p class="readout" aria-hidden="true">
		{#if pointed !== null && cells[pointed]}
			<span class="when">{calendarDay(cells[pointed].day)}</span>
			{format(cells[pointed].value)}
		{/if}
	</p>

	<div class="grid" aria-hidden="true">
		{#each cells as cell, index (cell.day)}
			<span
				class="cell"
				class:pointed={pointed === index}
				data-level={level(cell.value)}
				role="presentation"
				style:grid-row={cell.row}
				style:grid-column={cell.column}
				style:background-color={LEVEL_PAINT[level(cell.value)]}
				onpointerenter={() => (pointed = index)}
				onpointerleave={() => (pointed = null)}
			></span>
		{/each}
	</div>

	<div class="key" aria-hidden="true">
		<span>{CHART_WORDS.less}</span>
		{#each LEVEL_PAINT as paint, index (index)}
			<span class="cell" style:background-color={paint}></span>
		{/each}
		<span>{CHART_WORDS.more}</span>
	</div>

	{#if caption}
		<figcaption>{@render caption()}</figcaption>
	{/if}

	<FiguresTable
		summary={CHART_WORDS.days}
		caption={label}
		columns={[label]}
		rows={days.map((one) => ({ label: calendarDay(one.day), cells: [format(one.value)] }))}
	/>
</figure>

<style>
	.heat-map {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		min-inline-size: 0;
		margin: 0;
	}

	/* A year's fifty-three weeks across the reading measure sets the cell, and a month keeps it.
	   On a narrower screen every column gives up the same share, so a year still fits. */
	.grid {
		display: grid;
		grid-template-rows: repeat(7, auto);
		grid-auto-columns: minmax(0, calc(var(--page-measure) / 53 - var(--chart-mark-gap)));
		gap: var(--chart-mark-gap);
	}

	.cell {
		aspect-ratio: 1;
		border-radius: var(--radius-sm);
		transition: outline-color var(--dur-instant) var(--ease);
		outline: 1px solid transparent;
	}

	.grid .pointed {
		outline-color: var(--sift-ink-2);
	}

	.readout {
		min-block-size: 1lh;
		margin: 0;
		font: var(--text-label);
		color: var(--sift-ink-2);
	}

	.when {
		margin-inline-end: var(--space-2);
		color: var(--sift-ink);
	}

	.key {
		display: flex;
		align-items: center;
		gap: var(--chart-mark-gap);
		font: var(--text-label);
		color: var(--sift-ink-3);
	}

	.key .cell {
		inline-size: calc(var(--page-measure) / 53 - var(--chart-mark-gap));
	}

	.key span:first-child {
		margin-inline-end: var(--space-1);
	}

	.key span:last-child {
		margin-inline-start: var(--space-1);
	}

	figcaption {
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}
</style>
