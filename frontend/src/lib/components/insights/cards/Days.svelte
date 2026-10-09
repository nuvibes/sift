<script lang="ts">
	/*
	 * A week's or a month's days as squares in rows of seven, each shaded by its time against this
	 * person's own days, the brightest ringed and named under them. Every square says its day and
	 * its figure under the pointer or the keyboard. A year's days are the calendar's (`Heatmap`).
	 */
	import { levels } from '$lib/components/charts/series';
	import { Pointing } from '$lib/components/charts/pointing.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { calendarDay } from '$lib/shell/when';

	interface Props {
		days: readonly { day: string; value: number }[];
		format: (value: number) => string;
		label: string;
	}

	let { days, format, label }: Props = $props();

	const level = $derived(levels(days.map((one) => one.value)));
	const top = $derived(Math.max(0, ...days.map((one) => one.value)));
	const best = $derived(top > 0 ? days.findIndex((one) => one.value === top) : -1);
	const pointing = new Pointing(() => days.length, {
		ArrowLeft: -1,
		ArrowRight: 1,
		ArrowUp: -7,
		ArrowDown: 7
	});
	/* A week to a row, Monday first: the first day stands under its own weekday. */
	const blank = $derived(days.length > 0 ? weekday(days[0].day) : 0);
	/* As many weeks as there are rows: a holder short of room caps their height (`--days-high`). */
	const weeks = $derived(Math.ceil((days.length + blank) / 7));

	/** The weekday of an ISO day, Monday 0: a calendar day has no zone, so it is read as UTC. */
	function weekday(iso: string): number {
		const [year, month, day] = iso.split('-').map(Number);
		return (new Date(Date.UTC(year, month - 1, day)).getUTCDay() + 6) % 7;
	}
	const said = (index: number) => `${calendarDay(days[index].day)}, ${format(days[index].value)}`;
</script>

<figure class="days">
	<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
	<div
		class="squares"
		style:--weeks={weeks}
		role="group"
		tabindex="0"
		aria-label={pointing.at === null ? label : `${label}: ${said(pointing.at)}`}
		onkeydown={pointing.keydown}
		onfocus={pointing.focus}
		onblur={pointing.blur}
	>
		{#each { length: blank }, at (at)}
			<span class="cell" aria-hidden="true"></span>
		{/each}
		{#each days as one, index (one.day)}
			{#snippet detail()}
				<span class="figure">{format(one.value)}</span>
			{/snippet}
			<div
				class="cell"
				aria-hidden="true"
				onpointerenter={() => pointing.point(index)}
				onpointerleave={() => pointing.point(null)}
			>
				<Tooltip label={calendarDay(one.day)} {detail} held={pointing.held(index)} stretch>
					<span
						class="square level-{level(one.value)}"
						class:best={index === best}
						class:pointed={pointing.at === index}
					></span>
				</Tooltip>
			</div>
		{/each}
	</div>
	{#if best >= 0}
		<figcaption>
			<span class="named">{calendarDay(days[best].day)}</span>
			{format(days[best].value)}
		</figcaption>
	{/if}
</figure>

<style>
	.days {
		display: flex;
		flex: 1;
		flex-direction: column;
		justify-content: center;
		gap: var(--space-2);
		min-block-size: 0;
		margin: 0;
	}

	.squares {
		display: grid;
		grid-template-columns: repeat(7, minmax(0, 1fr));
		gap: var(--chart-mark-gap);
		max-inline-size: calc(var(--days-high, 100cqi) * 7 / var(--weeks));
		border-radius: var(--radius-sm);
	}

	.squares:focus-visible {
		box-shadow: var(--focus-ring);
		outline: none;
	}

	.cell {
		display: flex;
		min-inline-size: 0;
	}

	.square {
		display: block;
		inline-size: 100%;
		aspect-ratio: 1;
		border-radius: var(--radius-sm);
		background-color: color-mix(in srgb, var(--sift-accent-tint-2) 10%, transparent);
	}

	.level-1 {
		background-color: color-mix(in srgb, var(--sift-accent-tint-2) 30%, transparent);
	}

	.level-2 {
		background-color: color-mix(in srgb, var(--sift-accent-tint-2) 58%, transparent);
	}

	.level-3 {
		background-color: var(--sift-accent-tint-1);
	}

	.best,
	.pointed {
		box-shadow: 0 0 0 var(--chart-mark-gap) var(--sift-ink);
	}

	.figure {
		color: var(--sift-ink);
		font-variant-numeric: tabular-nums;
	}

	figcaption {
		font: var(--text-label);
		color: var(--sift-ink-2);
		font-variant-numeric: tabular-nums;
	}

	.named {
		font-weight: 700;
		color: var(--sift-accent-tint-1);
	}
</style>
