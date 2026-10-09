<script lang="ts">
	/*
	 * A figure's trend drawn small in its row: a bar per day, hour or month on one baseline, each
	 * with its tooltip saying when and how much. One tab stop; the arrows walk the bars (`Pointing`).
	 */
	import { Pointing } from '$lib/components/charts/pointing.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	interface Props {
		values: readonly number[];
		/** What each bar is: "Mon", "12 AM", "Oct". */
		labels: readonly string[];
		format: (value: number) => string;
		/** What the trend is of, to assistive technology. */
		label: string;
	}

	let { values, labels, format, label }: Props = $props();

	const top = $derived(Math.max(0, ...values));
	const pointing = new Pointing(() => values.length);
	const said = (index: number) => `${labels[index] ?? index + 1}, ${format(values[index])}`;
</script>

<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
<div
	class="spark"
	role="group"
	tabindex="0"
	aria-label={pointing.at === null ? label : `${label}: ${said(pointing.at)}`}
	onkeydown={pointing.keydown}
	onfocus={pointing.focus}
	onblur={pointing.blur}
>
	{#each values as value, index (index)}
		{#snippet detail()}<span class="figure">{format(value)}</span>{/snippet}
		<div
			class="column"
			class:pointed={pointing.at === index}
			aria-hidden="true"
			onpointerenter={() => pointing.point(index)}
			onpointerleave={() => pointing.point(null)}
		>
			<Tooltip
				label={labels[index] ?? String(index + 1)}
				{detail}
				placement="top"
				stretch
				shrinks
				held={pointing.held(index)}
			>
				<span class="room">
					<span
						class="bar"
						class:none={value <= 0}
						style:block-size={`${top > 0 ? (value / top) * 100 : 0}%`}
					></span>
				</span>
			</Tooltip>
		</div>
	{/each}
</div>

<style>
	.spark {
		display: grid;
		grid-auto-flow: column;
		grid-auto-columns: minmax(0, 1fr);
		block-size: var(--space-8);
		min-inline-size: var(--space-16);
		border-block-end: 1px solid var(--sift-line);
		border-radius: var(--radius-sm);
	}

	.spark:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	.column {
		display: flex;
		min-inline-size: 0;
	}

	.room {
		display: flex;
		flex: 1;
		align-items: flex-end;
		justify-content: center;
		block-size: 100%;
	}

	.bar {
		inline-size: calc(100% - var(--chart-mark-gap));
		max-inline-size: var(--chart-bar-width);
		border-start-start-radius: var(--radius-sm);
		border-start-end-radius: var(--radius-sm);
		background: var(--edge, var(--sift-series-2));
	}

	.none {
		visibility: hidden;
	}

	.pointed .bar {
		filter: brightness(1.15);
	}

	.figure {
		color: var(--sift-ink);
		font-variant-numeric: tabular-nums;
	}
</style>
