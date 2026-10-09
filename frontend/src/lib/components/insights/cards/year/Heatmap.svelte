<script lang="ts">
	/*
	 * THE YEAR'S DAYS, each a square shaded by its time viewed: the Insights page's own calendar,
	 * with its tooltips and its keyboard, standing in the card, and over it the brightest square
	 * named: a year's over its own week, a mark down to it and the day beside the mark; a shorter
	 * calendar's at its start, where the calendar is narrower than the card.
	 */
	import HeatMap from '$lib/components/charts/HeatMap.svelte';
	import type { components } from '$lib/api/schema';
	import { wordsOf } from '$lib/components/insights/figures';
	import { brightest, weekOf } from '$lib/components/insights/cards/year/pictures';
	import { calendarDay } from '$lib/shell/when';

	type Calendar = components['schemas']['Calendar'];

	interface Props {
		calendar: Calendar;
		label: string;
	}

	let { calendar, label }: Props = $props();

	const words = $derived(wordsOf(calendar.days, calendar.unit));
	const best = $derived(brightest(calendar.days));
	/* How far across the calendar the brightest day's week stands, as a share of its width: only
	   a calendar of more weeks than the card is wide for fills the card's width. */
	const WIDE = 26;
	const week = $derived(best ? weekOf(calendar.days, best.day) : null);
	const at = $derived(week && week.weeks > WIDE ? week.share : 0);
</script>

<div class="heat">
	{#if best}
		<p class="best" class:flip={at > 0.5} style:--at={at}>
			<span class="tick"></span>
			<span class="day">{calendarDay(best.day)}</span>
			<span class="time">{words(best.value)}</span>
		</p>
	{/if}
	<HeatMap days={calendar.days} format={words} {label} />
</div>

<style>
	.heat {
		display: flex;
		flex: 1;
		flex-direction: column;
		justify-content: center;
		gap: var(--space-2);
		min-block-size: 0;
		overflow: hidden;
	}

	/* The name stands over its week: from the mark on, or up to it past the middle. */
	.best {
		display: flex;
		align-items: baseline;
		gap: var(--space-2);
		margin: 0;
		margin-inline-start: calc(var(--at) * 100%);
	}

	.best.flip {
		flex-direction: row-reverse;
		margin-inline: 0 calc((1 - var(--at)) * 100%);
	}

	.tick {
		align-self: stretch;
		inline-size: var(--chart-mark-gap);
		background: var(--sift-accent-tint-1);
	}

	.day {
		font: var(--text-h2);
		white-space: nowrap;
		color: var(--sift-ink);
	}

	.time {
		font: var(--text-label);
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
		color: var(--sift-accent-tint-1);
	}
</style>
