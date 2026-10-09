<script lang="ts">
	/*
	 * THE YEAR'S TOP FIVE PEOPLE, MONTH BY MONTH, as a podium a month: the month's first in the
	 * middle and highest, its second to the left, its third to the right, each her face, the months
	 * in reading order. The five stand in a row over the months, each face with her name, so a face
	 * on a podium is read without a table. A month with no time is a dot. Under each podium the
	 * month and its first's time; the podium names its three and their times to assistive
	 * technology. No tooltip a face: a podium of them near the card's foot set the page's
	 * scrollbar flickering on and off without end.
	 *
	 * A face is the picture itself over her initial, not an `Avatar`: one person stands on up to
	 * twelve podiums, and as many `Avatar`s of one address loading together hold the page.
	 */
	import { HistorySentence } from '$lib/components/common';
	import type { components } from '$lib/api/schema';
	import { wordsOf } from '$lib/components/insights/figures';
	import { podiums } from '$lib/components/insights/cards/year/pictures';

	type Chart = components['schemas']['Chart'];
	type NamedRow = components['schemas']['NamedRow'];

	interface Props {
		rows: readonly NamedRow[];
		chart: Chart;
	}

	let { rows, chart }: Props = $props();

	/* The faces that did not load, drawn as their initial. */
	let failed = $state<ReadonlySet<string>>(new Set());

	const people = $derived(new Map(rows.map((row) => [row.piece.id ?? '', row])));
	const months = $derived(
		podiums(chart.bars).map((places, at) => {
			const bar = chart.bars[at];
			const step = (id: string | undefined, place: number) => ({
				id,
				place,
				value: bar.parts.find((part) => part.kind === id)?.value ?? 0
			});
			return {
				label: bar.label,
				/* Second, first, third: the podium's order from the left. */
				steps: [step(places[1], 2), step(places[0], 1), step(places[2], 3)],
				ranked: places.map((id, at) => step(id, at + 1))
			};
		})
	);
	const words = $derived(
		wordsOf(
			chart.bars.flatMap((bar) => bar.parts),
			chart.unit
		)
	);
</script>

{#snippet face(row: NamedRow | undefined, small: boolean)}
	<span class="who-face" class:small aria-hidden="true">
		{#if row}
			<span class="initial">{row.piece.text.charAt(0).toUpperCase()}</span>
			{#if row.cover && !failed.has(row.cover)}
				<img
					src={row.cover}
					alt=""
					decoding="async"
					onerror={() => (failed = new Set([...failed, row.cover ?? '']))}
				/>
			{/if}
		{/if}
	</span>
{/snippet}

<div class="race">
	<ol class="five">
		{#each rows.slice(0, 5) as row, index (row.piece.id ?? index)}
			<li class="who">
				{@render face(row, true)}
				<span class="name"><HistorySentence pieces={[row.piece]} /></span>
			</li>
		{/each}
	</ol>
	<ol class="months">
		{#each months as month, at (at)}
			<li class="month">
				{#if month.steps.every((step) => !step.id)}
					<span class="none"></span>
				{:else}
					<div
						class="podium"
						role="img"
						aria-label={`${month.label}: ${month.ranked
							.map((step) => `${people.get(step.id ?? '')?.piece.text ?? ''}, ${words(step.value)}`)
							.join('; ')}`}
					>
						{#each month.steps as step (step.place)}
							<div class="step place-{step.place}">
								{#if step.id}
									{@render face(people.get(step.id), step.place !== 1)}
								{/if}
								<span class="plinth"></span>
							</div>
						{/each}
					</div>
				{/if}
				<span class="month-name">{month.label}</span>
				{#if month.ranked[0]}
					<span class="lead-time">{words(month.ranked[0].value)}</span>
				{/if}
			</li>
		{/each}
	</ol>
</div>

<style>
	/* Within the room the card's words leave, never more: the months share what is left. */
	.race {
		display: flex;
		flex: 1;
		flex-direction: column;
		justify-content: center;
		gap: var(--space-3);
		min-block-size: 0;
		overflow: hidden;
	}

	/* The five, a face and a name each. */
	.five {
		display: grid;
		grid-template-columns: repeat(5, minmax(0, 1fr));
		gap: var(--space-2);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.who {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: var(--space-1);
		min-inline-size: 0;
	}

	.who .name {
		overflow: hidden;
		max-inline-size: 100%;
		font: var(--text-micro);
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.months {
		display: grid;
		flex: 0 1 auto;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: var(--space-3) var(--space-4);
		min-block-size: 0;
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.month {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: flex-end;
		gap: var(--space-1);
	}

	/* Three steps on one baseline, the first the highest. */
	.podium {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		align-items: end;
		inline-size: 100%;
	}

	.step {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: var(--chart-mark-gap);
	}

	.plinth {
		inline-size: 100%;
		block-size: var(--space-2);
		background: var(--sift-accent-shade-1);
	}

	.place-1 .plinth {
		block-size: var(--space-4);
		background: var(--sift-accent-tint-2);
	}

	.place-3 .plinth {
		block-size: var(--space-1);
	}

	.who-face {
		display: grid;
		inline-size: calc(var(--list-cover) * 9 / 10);
		aspect-ratio: 1;
		overflow: hidden;
		border-radius: 50%;
		background: var(--sift-accent-shade-2);
	}

	.who-face.small {
		inline-size: calc(var(--list-cover) * 2 / 3);
	}

	/* The picture over the initial, both filling the round. */
	.who-face > * {
		grid-area: 1 / 1;
	}

	.who-face img {
		inline-size: 100%;
		block-size: 100%;
		object-fit: cover;
	}

	.initial {
		place-self: center;
		font: var(--text-label);
		color: var(--sift-ink);
	}

	/* A month nobody had time in: a dot where its podium would stand. */
	.none {
		margin-block: auto var(--space-2);
		inline-size: var(--space-1);
		aspect-ratio: 1;
		border-radius: 50%;
		background: var(--sift-ink-3);
	}

	.month-name {
		font: var(--text-micro);
		letter-spacing: var(--tracking-micro);
		text-transform: uppercase;
		color: var(--sift-ink-2);
	}

	.lead-time {
		font: var(--text-micro);
		font-variant-numeric: tabular-nums;
		color: var(--sift-accent-tint-1);
	}
</style>
