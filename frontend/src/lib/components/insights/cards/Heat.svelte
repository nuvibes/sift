<script lang="ts">
	/*
	 * The year's days, each a cell shaded by its time viewed: the Insights page's own calendar,
	 * with its tooltips, standing in the card.
	 */
	import HeatMap from '$lib/components/charts/HeatMap.svelte';
	import type { components } from '$lib/api/schema';
	import { wordsOf } from '$lib/components/insights/figures';

	type Calendar = components['schemas']['Calendar'];

	interface Props {
		calendar: Calendar;
		label: string;
	}

	let { calendar, label }: Props = $props();

	const words = $derived(wordsOf(calendar.days, calendar.unit));
</script>

<div class="heat">
	<HeatMap days={calendar.days} format={words} {label} />
</div>

<style>
	.heat {
		display: grid;
		flex: 1;
		align-content: center;
		min-block-size: 0;
	}
</style>
