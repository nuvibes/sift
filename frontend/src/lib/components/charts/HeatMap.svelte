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
	 * A cell's day and figure are its tooltip, under the pointer or the keyboard: the arrows step a
	 * day up and down and a week across.
	 */
	import type { Snippet } from 'svelte';

	import { Pointing } from '$lib/components/charts/pointing.svelte';
	import { LEVEL_PAINT, levels } from '$lib/components/charts/series';
	import { CHART_WORDS } from '$lib/components/charts/words';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { calendarDay } from '$lib/shell/when';

	interface Props {
		/** Every day drawn, in order, each an ISO day with its figure. */
		days: readonly { day: string; value: number }[];
		format: (value: number) => string;
		/** What the map is of: its name to assistive technology. */
		label: string;
		caption?: Snippet;
		/** A day whose tooltip stays up whatever the pointer does: the DESIGN GALLERY's open bubble. */
		held?: number | null;
	}

	let { days, format, label, caption, held = null }: Props = $props();

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

	const pointing = new Pointing(() => days.length, {
		ArrowUp: -1,
		ArrowDown: 1,
		ArrowLeft: -7,
		ArrowRight: 7
	});
	const pointed = $derived(pointing.at === null ? null : (cells[pointing.at] ?? null));
</script>

<figure class="heat-map">
	<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
	<div
		class="grid"
		role="group"
		tabindex="0"
		aria-label={pointed === null
			? label
			: `${label}: ${calendarDay(pointed.day)}, ${format(pointed.value)}`}
		onkeydown={pointing.keydown}
		onfocus={pointing.focus}
		onblur={pointing.blur}
	>
		{#each cells as cell, index (cell.day)}
			{#snippet detail()}
				<span class="figure">{format(cell.value)}</span>
			{/snippet}
			<span
				class="cell"
				class:pointed={pointing.at === index || held === index}
				data-level={level(cell.value)}
				aria-hidden="true"
				style:grid-row={cell.row}
				style:grid-column={cell.column}
				style:background-color={LEVEL_PAINT[level(cell.value)]}
				onpointerenter={() => pointing.point(index)}
				onpointerleave={() => pointing.point(null)}
			>
				<Tooltip
					label={calendarDay(cell.day)}
					{detail}
					placement="top"
					stretch
					shrinks
					held={pointing.held(index) || held === index}
				>
					<span class="hit"></span>
				</Tooltip>
			</span>
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
	/* As wide as its weeks, so the keyboard's ring hugs the days rather than the row. */
	.grid {
		display: grid;
		align-self: flex-start;
		max-inline-size: 100%;
		grid-template-rows: repeat(7, auto);
		grid-auto-columns: minmax(0, calc(var(--page-measure) / 53 - var(--chart-mark-gap)));
		gap: var(--chart-mark-gap);
	}

	.cell {
		display: flex;
		aspect-ratio: 1;
		border-radius: var(--radius-sm);
		transition: outline-color var(--dur-instant) var(--ease);
		outline: 1px solid transparent;
	}

	.grid .pointed {
		outline-color: var(--sift-ink-2);
	}

	/* The cell's whole square is what the pointer finds. */
	.hit {
		flex: 1;
	}

	.figure {
		font-variant-numeric: tabular-nums;
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
