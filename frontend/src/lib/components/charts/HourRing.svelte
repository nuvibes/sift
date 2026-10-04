<script lang="ts">
	/*
	 * THE TWENTY-FOUR HOURS AS A RING, midnight at the top, each hour a segment shaded by its
	 * figure: a favourite time of day at a glance, the way a clock face is read.
	 *
	 * The shades are the heat-map's (`levels`, against this person's own hours), so the ring and
	 * the calendar speak one language. The middle holds whatever the caller puts there (the
	 * favourite hour, said). The ring is hidden from assistive technology and the table under it
	 * says every hour in words.
	 */
	import type { Snippet } from 'svelte';

	import FiguresTable from '$lib/components/charts/FiguresTable.svelte';
	import { LEVEL_PAINT, levels } from '$lib/components/charts/series';
	import { CHART_WORDS, hourMark, hourWords } from '$lib/components/charts/words';

	interface Props {
		/** The twenty-four hours from midnight, each with its figure. */
		hours: readonly number[];
		format: (value: number) => string;
		/** What the ring is of, for the table's caption. */
		label: string;
		/** What the middle of the ring says. */
		middle?: Snippet;
	}

	let { hours, format, label, middle }: Props = $props();

	/* Each hour is a fifteen-degree segment with one degree of ground after it, so the hours read
	   as separate marks the way the bars do. */
	const SEGMENT = 15;
	const GAP = 1;

	const level = $derived(levels(hours));
	const paint = $derived(
		`conic-gradient(${hours
			.map((value, hour) => {
				const from = hour * SEGMENT;
				return `${LEVEL_PAINT[level(value)]} ${from}deg ${from + SEGMENT - GAP}deg, transparent ${from + SEGMENT - GAP}deg ${from + SEGMENT}deg`;
			})
			.join(', ')})`
	);
</script>

<figure class="hour-ring">
	<div class="dial" aria-hidden="true">
		<div class="ring" style:background-image={paint}></div>
		<div class="middle">
			{#if middle}{@render middle()}{/if}
		</div>
		<!-- The quarters of the day, on the reader's clock like the time in the middle. -->
		<span class="mark top">{hourMark(0)}</span>
		<span class="mark right">{hourMark(6)}</span>
		<span class="mark bottom">{hourMark(12)}</span>
		<span class="mark left">{hourMark(18)}</span>
	</div>
	<FiguresTable
		summary={CHART_WORDS.hours}
		caption={label}
		columns={[label]}
		rows={hours.map((value, hour) => ({ label: hourWords(hour), cells: [format(value)] }))}
	/>
</figure>

<style>
	/* As wide as the box it is in, so the dial's cap is measured against that box: a ring centred
	   in a shrink-to-fit cell (a recap card's picture) would be sized by the fold under it and come
	   out a hole narrower than the time in its middle. */
	.hour-ring {
		display: flex;
		flex-direction: column;
		align-items: center;
		inline-size: 100%;
		gap: var(--space-3);
		margin: 0;
	}

	/* The ring a quarter of the page measure across, or what the box leaves where that is
	   narrower. The room beside it is the width of a quarter's mark ("6 PM"), so the marks at the
	   sides stand clear of the ring rather than over its edge; the room above and below is a line
	   of the marks' own type. */
	.dial {
		position: relative;
		display: grid;
		place-items: center;
		inline-size: min(
			100%,
			calc(var(--page-measure) / 4 + 2 * var(--space-10) - 2 * var(--space-5))
		);
		padding: var(--space-5) var(--space-10);
		/* How wide the hole is, as a share of the ring. */
		--hole: 62%;
	}

	/* The hole is cut out of the ring rather than painted over it, so the middle is whatever ground
	   the ring stands on: a card, a raised panel or the page. */
	.ring {
		grid-area: 1 / 1;
		inline-size: 100%;
		aspect-ratio: 1;
		border-radius: var(--radius-full);
		mask-image: radial-gradient(
			closest-side,
			transparent calc(var(--hole) - 0.5%),
			var(--sift-ink) var(--hole)
		);
	}

	/* The middle of the ring, holding the caller's words. */
	.middle {
		grid-area: 1 / 1;
		display: grid;
		place-items: center;
		inline-size: var(--hole);
		aspect-ratio: 1;
		font: var(--text-h2);
		color: var(--sift-ink);
		text-align: center;
	}

	.mark {
		position: absolute;
		font: var(--text-micro);
		color: var(--sift-ink-3);
	}

	.top {
		inset-block-start: 0;
	}

	.bottom {
		inset-block-end: 0;
	}

	.right {
		inset-inline-end: 0;
	}

	.left {
		inset-inline-start: 0;
	}
</style>
