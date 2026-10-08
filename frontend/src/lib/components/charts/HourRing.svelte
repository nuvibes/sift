<script lang="ts">
	/*
	 * THE TWENTY-FOUR HOURS AS A RING, midnight at the top, each hour a segment shaded by its
	 * figure: a favourite time of day at a glance, the way a clock face is read.
	 *
	 * The shades are the heat-map's (`levels`, against this person's own hours), so the ring and
	 * the calendar speak one language. The middle holds whatever the caller puts there (the
	 * favourite hour, said). An hour's figure is its tooltip, under the pointer or the keyboard: the
	 * arrows go round the clock.
	 */
	import type { Snippet } from 'svelte';

	import { Pointing } from '$lib/components/charts/pointing.svelte';
	import { LEVEL_PAINT, levels } from '$lib/components/charts/series';
	import { hourMark, hourWords } from '$lib/components/charts/words';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	interface Props {
		/** The twenty-four hours from midnight, each with its figure. */
		hours: readonly number[];
		format: (value: number) => string;
		/** What the ring is of: its name to assistive technology. */
		label: string;
		/** What the middle of the ring says. */
		middle?: Snippet;
		/** An hour whose tooltip stays up whatever the pointer does: the DESIGN GALLERY's open bubble. */
		held?: number | null;
	}

	let { hours, format, label, middle, held = null }: Props = $props();

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

	const pointing = new Pointing(() => hours.length);
</script>

<figure class="hour-ring">
	<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
	<div
		class="dial"
		role="group"
		tabindex="0"
		aria-label={pointing.at === null
			? label
			: `${label}: ${hourWords(pointing.at)}, ${format(hours[pointing.at] ?? 0)}`}
		onkeydown={pointing.keydown}
		onfocus={pointing.focus}
		onblur={pointing.blur}
	>
		<div class="ring" style:background-image={paint} aria-hidden="true"></div>
		<div class="middle" aria-hidden="true">
			{#if middle}{@render middle()}{/if}
		</div>
		<!-- Where the pointer finds each hour: a seat on the ring's band, turned to its segment. -->
		<div class="seats" aria-hidden="true">
			{#each hours as value, hour (hour)}
				{#snippet detail()}
					<span class="figure">{format(value)}</span>
				{/snippet}
				<span
					class="seat"
					role="presentation"
					class:pointed={pointing.at === hour || held === hour}
					style:rotate={`${hour * SEGMENT + (SEGMENT - GAP) / 2}deg`}
					onpointerenter={() => pointing.point(hour)}
					onpointerleave={() => pointing.point(null)}
				>
					<Tooltip
						label={hourWords(hour)}
						{detail}
						placement="top"
						stretch
						shrinks
						held={pointing.held(hour) || held === hour}
					>
						<span class="hit"></span>
					</Tooltip>
				</span>
			{/each}
		</div>
		<!-- The quarters of the day, on the reader's clock like the time in the middle. -->
		<span class="mark top" aria-hidden="true">{hourMark(0)}</span>
		<span class="mark right" aria-hidden="true">{hourMark(6)}</span>
		<span class="mark bottom" aria-hidden="true">{hourMark(12)}</span>
		<span class="mark left" aria-hidden="true">{hourMark(18)}</span>
	</div>
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

	/* The seats stand over the ring, the size of it, a container so a seat can reach its middle. */
	.seats {
		grid-area: 1 / 1;
		position: relative;
		inline-size: 100%;
		aspect-ratio: 1;
		container-type: inline-size;
	}

	/* One hour's seat: the band's depth tall and its arc wide at the middle of the band, turned
	   about the ring's centre to stand over its segment. */
	.seat {
		position: absolute;
		inset-block-start: 0;
		inset-inline-start: 50%;
		display: flex;
		inline-size: calc((100% + var(--hole)) * 3.1416 / 48);
		block-size: calc((100% - var(--hole)) / 2);
		translate: -50% 0;
		transform-origin: 50% 50cqi;
		border-radius: var(--radius-sm);
	}

	.seat.pointed {
		outline: 1px solid var(--sift-ink-2);
	}

	.hit {
		flex: 1;
	}

	.figure {
		font-variant-numeric: tabular-nums;
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
