<script lang="ts">
	/*
	 * Parts of a whole as one stack, the proportions themselves the drawing: each part a block as
	 * tall as its share, its name and its share written on it, the largest in the family's ink.
	 * Every block also says its figure under the pointer.
	 */
	import { shares } from '$lib/components/charts/series';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { KIND_WORDS } from '$lib/components/insights/words';

	interface Props {
		parts: readonly { kind: string; value: number }[];
		format: (value: number) => string;
	}

	let { parts, format }: Props = $props();

	const shown = $derived(parts.filter((part) => part.value > 0));
	const percent = $derived(shares(shown.map((part) => part.value)));
	const most = $derived(Math.max(0, ...shown.map((part) => part.value)));
</script>

{#if shown.length > 0}
	<ol class="shares">
		{#each shown as part, index (part.kind)}
			{#snippet detail()}
				<span class="figure">{format(part.value)}</span>
			{/snippet}
			<li class:most={part.value === most} style:flex-grow={part.value}>
				<Tooltip label={KIND_WORDS[part.kind] ?? part.kind} {detail} placement="right" stretch>
					<span class="block">
						<span class="name">{KIND_WORDS[part.kind] ?? part.kind}</span>
						<span class="share">{percent[index]}%</span>
					</span>
				</Tooltip>
			</li>
		{/each}
	</ol>
{/if}

<style>
	.shares {
		display: flex;
		flex: 1;
		flex-direction: column;
		gap: var(--chart-mark-gap);
		min-block-size: 0;
		margin: 0;
		padding: 0;
		list-style: none;
	}

	/* A part keeps a line's height however small its share, so its name is always read. */
	li {
		display: flex;
		flex-basis: 0;
		min-block-size: calc(var(--space-4) + var(--space-2));
		overflow: hidden;
		border-radius: var(--radius-sm);
	}

	.block {
		display: flex;
		flex: 1;
		align-items: flex-start;
		justify-content: space-between;
		gap: var(--space-2);
		padding: var(--space-1) var(--space-2);
		background-color: color-mix(in srgb, var(--sift-accent-tint-2) 22%, transparent);
		font: var(--text-label);
		color: var(--sift-ink);
	}

	.most .block {
		background-color: var(--sift-accent-tint-1);
		color: var(--sift-accent-shade-2);
	}

	.name {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.share {
		font-weight: 700;
		font-variant-numeric: tabular-nums;
	}

	.figure {
		color: var(--sift-ink);
		font-variant-numeric: tabular-nums;
	}
</style>
