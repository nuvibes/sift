<script lang="ts">
	/*
	 * The period tabs and the arrows either side of the days they cover: the tools row of Insights
	 * and of its Stats view, so stepping through periods reads the same on both. Each screen keeps
	 * its own address (`path`), so the tabs and arrows of the Stats view stay on the Stats view.
	 */
	import type { Snippet } from 'svelte';
	import { goto } from '$app/navigation';

	import { Button, Tooltip } from '$lib/components/common';
	import Tabs from '$lib/components/common/Tabs.svelte';
	import {
		addressOf,
		stepsFrom,
		tabsFor,
		type InsightsPage,
		type Place
	} from '$lib/components/insights/period';
	import { INSIGHTS_WORDS } from '$lib/components/insights/words';
	import { calendarDay } from '$lib/shell/when';

	interface Props {
		place: Place;
		/** The answer drawn, which says where the period starts and ends. */
		answer: InsightsPage | null;
		/** A period's answer on its way: the arrows wait for it and the days are dimmed. */
		loading: boolean;
		/** The screen's own address, which the tabs and arrows keep. */
		path: string;
		/** A press after the arrows. */
		after?: Snippet;
	}

	let { place, answer, loading, path, after }: Props = $props();

	const tabs = $derived(tabsFor(place, path));
	const steps = $derived(answer && !loading ? stepsFrom(answer) : { earlier: null, later: null });
	/* The days the answer covers, as every date on screen is written (`$lib/shell/when`). */
	const days = $derived(
		answer === null
			? ''
			: answer.from === answer.to
				? calendarDay(answer.from)
				: `${calendarDay(answer.from)} — ${calendarDay(answer.to)}`
	);

	function go(to: Place | null) {
		if (to) void goto(addressOf(to, path));
	}
</script>

<div class="periods">
	<Tabs {tabs} current={place.period} label={INSIGHTS_WORDS.periods} />
	{#if place.period !== 'all'}
		<div class="steps">
			<Tooltip label={INSIGHTS_WORDS.earlier}>
				<Button
					icon="chevron_left"
					size="small"
					tone="ghost"
					aria-label={INSIGHTS_WORDS.earlier}
					disabled={steps.earlier === null}
					onclick={() => go(steps.earlier)}
				/>
			</Tooltip>
			<span class="days" class:stale={loading} aria-live="polite">{days}</span>
			<Tooltip label={INSIGHTS_WORDS.later}>
				<Button
					icon="chevron_right"
					size="small"
					tone="ghost"
					aria-label={INSIGHTS_WORDS.later}
					disabled={steps.later === null}
					onclick={() => go(steps.later)}
				/>
			</Tooltip>
		</div>
	{:else if days}
		<span class="days">{days}</span>
	{/if}
	{#if after}<div class="after">{@render after()}</div>{/if}
</div>

<style>
	.periods {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2) var(--space-4);
	}

	/* On a phone the steps wrap under the periods, and a finger's reach round each needs the two
	   lines further apart than a desk does, or a press just under "Week" would go to Earlier. */
	@media (max-width: 767px) {
		.periods {
			row-gap: var(--space-4);
		}
	}

	.steps {
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}

	.days {
		font: var(--text-label);
		color: var(--sift-ink-2);
		white-space: nowrap;
	}

	/* The press after the arrows stands at the row's end. */
	.after {
		margin-inline-start: auto;
	}

	.stale {
		opacity: 0.55;
		transition: opacity var(--dur-instant) var(--ease);
	}
</style>
