<script lang="ts">
	/*
	 * ONE LARGE FIGURE ON A CARD: its label, the number, and at most one sentence under it.
	 *
	 * The number counts up from zero when the card arrives (a period's answer, a recap card turned
	 * to), over the ambient duration, eased; a later change is drawn at once, and with reduced
	 * motion it is drawn at once (`countUp`). A figure that is not a quantity, such as a time of day, is always drawn at once.
	 * The final figure sits unseen in the same cell as the counting one, so the card is as wide as
	 * its answer from the first frame and nothing beside it moves while it counts.
	 *
	 * A card can carry its figure's TREND: the same figure bar by bar over the period (a week's
	 * days, a year's months), drawn as a line of little bars along its foot, the Overview's chart in
	 * miniature. It is the bar chart's own encoding (length from one baseline, the accent's middle
	 * shade), hidden from assistive technology because the chart it repeats says every bar in words.
	 * `lit` stands the card on the accent's deepest shade with its figure in the accent's tint: the
	 * one card a screen leads with. `hero` sets the figure in the figure size, `--text-figure`.
	 */
	import type { Snippet } from 'svelte';
	import { untrack } from 'svelte';

	import { Panel } from '$lib/components/common';
	import { countUp } from '$lib/shell/motion.svelte';

	interface Props {
		label: string;
		value: number;
		/** The number as words: "41 hours", "1,240". */
		format: (value: number) => string;
		/** Whether the number counts up. False for a time of day, which is not an amount. */
		counts?: boolean;
		/** `hero` for the one figure a screen leads with, `large` for a period's headline figures,
		 *  `small` for a card among several. */
		size?: 'hero' | 'large' | 'small';
		/** The card's ground: `plain`, or `lit` on the accent's run for the figure a screen leads with. */
		tone?: 'plain' | 'lit';
		/** The figure bar by bar over the period, drawn along the card's foot. */
		trend?: readonly number[];
		/** Stand on a card of its own (the default), or bare, for a figure inside a card already. */
		ground?: boolean;
		/** Anything the figure carries beside it: how much of it is hidden. */
		aside?: Snippet;
		/** The one sentence under the figure. */
		caption?: Snippet;
	}

	let {
		label,
		value,
		format,
		counts = true,
		size = 'large',
		tone = 'plain',
		trend = [],
		ground = true,
		aside,
		caption
	}: Props = $props();

	/* The tallest bar of the trend, which every other is a share of. */
	const trendTop = $derived(Math.max(0, ...trend));

	/* What the figure shows while it counts. Absent at rest, so a figure at rest draws its own value. */
	let shown = $state<number | undefined>(undefined);
	let arrived = false;

	$effect(() => {
		const to = value;
		const moving = counts;
		return untrack(() => {
			shown = undefined;
			if (!moving || arrived) return () => {};
			arrived = true;
			return countUp(to, (at) => {
				shown = at === to ? undefined : at;
			});
		});
	});
</script>

{#snippet card()}
	<div class="figure-card {size}" class:lit={tone === 'lit'} class:bare={!ground}>
		<span class="label">{label}</span>
		<span class="value" data-final={format(value)}>
			<span class="count">{format(shown ?? value)}</span>
		</span>
		{#if aside}
			<span class="aside">{@render aside()}</span>
		{/if}
		{#if caption}
			<p class="caption">{@render caption()}</p>
		{/if}
		{#if trendTop > 0}
			<div class="trend" aria-hidden="true">
				{#each trend as one, index (index)}
					<span class="spark" style:block-size={`${(one / trendTop) * 100}%`}></span>
				{/each}
			</div>
		{/if}
	</div>
{/snippet}

{#if ground}
	<Panel tone="raised" corner="lg" inset="md">{@render card()}</Panel>
{:else}
	{@render card()}
{/if}

<style>
	.figure-card {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		min-inline-size: 0;
	}

	.label {
		font: var(--text-label);
		color: var(--sift-ink-2);
	}

	/*
	 * The final figure is drawn invisibly in the same grid cell as the counting one, so the cell
	 * takes the final width from the first frame. Tabular digits, so a digit turning over does not
	 * move the digits beside it.
	 */
	.value {
		display: grid;
		font-variant-numeric: tabular-nums;
		color: var(--sift-ink);
	}

	.large .value {
		font: var(--text-display-lg);
		letter-spacing: var(--tracking-display);
	}

	.small .value {
		font: var(--text-display);
		letter-spacing: var(--tracking-display);
	}

	.hero .value {
		font: var(--text-figure);
		letter-spacing: var(--tracking-display);
	}

	/* Lit: the card's own ground is the accent's run, deepest at the foot, and the figure is drawn
	   in its tint; the words stay in the ink, which `contrast.test.ts` holds to the floor on both
	   shades. Drawn by the card's content box across the whole panel: the panel's inset is given
	   back as a negative margin, and the ground is clipped to the panel's corner rather than given
	   a corner of its own (the panel is the box; this is only its ground). */
	.lit {
		margin: calc(-1 * var(--space-3));
		padding: var(--space-5) var(--space-4);
		clip-path: inset(0 round var(--radius-lg));
		background-image: linear-gradient(
			160deg,
			var(--sift-accent-shade-1),
			var(--sift-accent-shade-2) 70%
		);
	}

	/* Lit and bare: the card it stands in is the lit ground already, so only the figure's tint.
	   And no corner: with no inset the rounded clip would cut the label's first letter at the top
	   left (a recap card's "Viewed" reading as "iewed"). */
	.lit.bare {
		margin: 0;
		padding: 0;
		clip-path: none;
		background-image: none;
	}

	.lit .value {
		color: var(--sift-accent-tint-1);
	}

	.lit .label,
	.lit .caption {
		color: var(--sift-ink);
	}

	/* The trend: little bars on one baseline along the card's foot, a hairline's gap apart. */
	.trend {
		display: flex;
		align-items: flex-end;
		gap: var(--chart-mark-gap);
		block-size: var(--space-8);
		margin-block-start: var(--space-2);
	}

	.spark {
		flex: 1;
		min-block-size: var(--chart-mark-gap);
		border-start-start-radius: var(--radius-sm);
		border-start-end-radius: var(--radius-sm);
		background: var(--sift-series-2);
		opacity: 0.85;
	}

	.lit .spark {
		background: var(--sift-accent-tint-2);
		opacity: 0.6;
	}

	.value::after {
		content: attr(data-final);
		visibility: hidden;
	}

	.count,
	.value::after {
		grid-area: 1 / 1;
	}

	.aside {
		display: flex;
		align-items: center;
		gap: var(--space-1);
		font: var(--text-label);
		color: var(--sift-ink-3);
	}

	.caption {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}
</style>
