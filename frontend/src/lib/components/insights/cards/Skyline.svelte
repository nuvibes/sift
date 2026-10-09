<script lang="ts">
	/*
	 * The period's bars as a skyline: no scale, no key, no grid, the tallest bar lit in the
	 * family's ink and named over it. Every bar still says its time and its figure, under the
	 * pointer or the keyboard (`Pointing`: one tab stop, the arrows along the bars), the way every
	 * chart of Insights does.
	 */
	import { Pointing } from '$lib/components/charts/pointing.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	export interface Tower {
		/** When the bar is: "10 PM", "Tuesday", "March". */
		when: string;
		value: number;
	}

	interface Props {
		bars: readonly Tower[];
		format: (value: number) => string;
		/** What the bars are of, to assistive technology. */
		label: string;
	}

	let { bars, format, label }: Props = $props();

	const top = $derived(Math.max(0, ...bars.map((bar) => bar.value)));
	/* The peak: the first of the tallest, so a tie is named the same way every read. */
	const peak = $derived(top > 0 ? bars.findIndex((bar) => bar.value === top) : -1);
	const pointing = new Pointing(() => bars.length);
	const said = (index: number) => `${bars[index].when}, ${format(bars[index].value)}`;
</script>

<figure class="skyline">
	<!-- The peak's name over it, slid so it never passes the card's edge: at the left end it starts
	     at the peak, at the right end it ends there, between them it is centred. -->
	<p class="over" aria-hidden="true">
		{#if peak >= 0}
			<span class="named" style:--at={bars.length > 1 ? peak / (bars.length - 1) : 0.5}
				>{bars[peak].when}</span
			>
		{/if}
	</p>
	<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
	<div
		class="plot"
		role="group"
		tabindex="0"
		aria-label={pointing.at === null ? label : `${label}: ${said(pointing.at)}`}
		onkeydown={pointing.keydown}
		onfocus={pointing.focus}
		onblur={pointing.blur}
	>
		{#each bars as bar, index (index)}
			{#snippet detail()}
				<span class="figure">{format(bar.value)}</span>
			{/snippet}
			<div
				class="column"
				class:peak={index === peak}
				class:pointed={pointing.at === index}
				aria-hidden="true"
				onpointerenter={() => pointing.point(index)}
				onpointerleave={() => pointing.point(null)}
			>
				<Tooltip
					label={bar.when}
					{detail}
					placement="top"
					stretch
					shrinks
					held={pointing.held(index)}
				>
					<div class="room">
						{#if top > 0 && bar.value > 0}
							<span class="tower" style:block-size={`${(bar.value / top) * 100}%`}></span>
						{/if}
					</div>
				</Tooltip>
			</div>
		{/each}
	</div>
</figure>

<style>
	.skyline {
		display: flex;
		flex: 1;
		flex-direction: column;
		min-block-size: 0;
		margin: 0;
	}

	.plot {
		display: grid;
		flex: 1;
		grid-auto-flow: column;
		grid-auto-columns: minmax(0, 1fr);
		min-block-size: 0;
		border-block-end: 1px solid var(--sift-accent-tint-2);
		border-radius: var(--radius-sm);
	}

	.plot:focus-visible {
		box-shadow: var(--focus-ring);
		outline: none;
	}

	.column {
		display: flex;
		min-inline-size: 0;
	}

	/* The room above the baseline: the bar stands at its foot, the peak's name over the peak. */
	.room {
		display: flex;
		flex: 1;
		flex-direction: column;
		justify-content: flex-end;
		align-items: center;
		min-block-size: 0;
		block-size: 100%;
	}

	.tower {
		display: block;
		inline-size: calc(100% - 2 * var(--chart-mark-gap));
		max-inline-size: var(--chart-bar-width);
		border-start-start-radius: var(--radius-sm);
		border-start-end-radius: var(--radius-sm);
		background-color: color-mix(in srgb, var(--sift-accent-tint-2) 34%, transparent);
	}

	.pointed .tower {
		background-color: color-mix(in srgb, var(--sift-accent-tint-2) 62%, transparent);
	}

	.peak .tower {
		background-color: var(--sift-accent-tint-1);
	}

	.over {
		position: relative;
		min-block-size: 1lh;
		margin: 0 0 var(--space-1);
		font: var(--text-label);
	}

	/* Each column is a share of the width, so the peak's middle is its share along it. */
	.named {
		position: absolute;
		inset-inline-start: calc(var(--at) * 100%);
		translate: calc(var(--at) * -100%) 0;
		font: var(--text-label);
		font-weight: 700;
		color: var(--sift-accent-tint-1);
		white-space: nowrap;
	}

	.figure {
		color: var(--sift-ink);
		font-variant-numeric: tabular-nums;
	}
</style>
