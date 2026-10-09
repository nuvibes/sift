<script lang="ts">
	/*
	 * A card's figures side by side, each its number over its label: what a band's tile counts
	 * beside its lead figure. A figure of nothing is left out, as everywhere on Insights.
	 */
	import type { components } from '$lib/api/schema';
	import { saidOf, worthACard } from '$lib/components/insights/figures';

	type Figure = components['schemas']['Figure'];

	interface Props {
		figures: readonly Figure[];
	}

	let { figures }: Props = $props();

	const shown = $derived(figures.filter(worthACard).slice(0, 4));
</script>

{#if shown.length > 0}
	<dl class="tally">
		{#each shown as figure (figure.label)}
			<div class="one">
				<dt>{figure.label}</dt>
				<dd>{saidOf(figure.said, figure.value, figure.unit)}</dd>
			</div>
		{/each}
	</dl>
{/if}

<style>
	.tally {
		display: grid;
		grid-auto-columns: minmax(0, 1fr);
		grid-auto-flow: column;
		gap: var(--space-3);
		margin: 0;
	}

	.one {
		display: flex;
		flex-direction: column-reverse;
		gap: var(--space-1);
		min-inline-size: 0;
		padding-inline-start: var(--space-2);
		border-inline-start: 2px solid var(--sift-accent-tint-2);
	}

	dt {
		overflow: hidden;
		font: var(--text-micro);
		letter-spacing: var(--tracking-micro);
		text-transform: uppercase;
		text-overflow: ellipsis;
		white-space: nowrap;
		color: var(--sift-ink-2);
	}

	dd {
		margin: 0;
		font: var(--text-h2);
		color: var(--sift-ink);
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}
</style>
