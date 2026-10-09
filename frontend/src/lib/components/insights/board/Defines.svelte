<script lang="ts">
	/*
	 * What a tile's figures count, one hover away and held on a press: the server's sentence for
	 * each (`defines`), the same mark the Stats panels and the figures of Insights wear.
	 */
	import { Button, HistorySentence, Tooltip } from '$lib/components/common';
	import type { components } from '$lib/api/schema';
	import { INSIGHTS_WORDS } from '$lib/components/insights/words';

	type Figure = components['schemas']['Figure'];

	let { figures }: { figures: readonly Figure[] } = $props();

	const defined = $derived(figures.filter((figure) => figure.defines.length > 0));
	let held = $state(false);
</script>

{#if defined.length > 0}
	<span class="defines">
		<Tooltip label={INSIGHTS_WORDS.defines} {held}>
			{#snippet detail()}
				{#each defined as one, index (index)}
					<span class="defined">
						{#if defined.length > 1}<strong>{one.label}</strong>{/if}
						<HistorySentence pieces={one.defines} />
					</span>
				{/each}
			{/snippet}
			<Button
				tone="ghost"
				size="small"
				icon="info"
				aria-label={INSIGHTS_WORDS.defines}
				onclick={() => (held = !held)}
				onkeydown={(event: KeyboardEvent) => {
					if (event.key === 'Escape') held = false;
				}}
				onblur={() => (held = false)}
			/>
		</Tooltip>
	</span>
{/if}

<style>
	/* At the tile's top end, over the card: a press here is the mark's, not the tile's. */
	.defines {
		position: absolute;
		inset-block-start: var(--space-2);
		inset-inline-end: var(--space-2);
		z-index: 1;
	}

	.defined {
		display: block;
	}
</style>
