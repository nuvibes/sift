<script lang="ts">
	/*
	 * A wall shape drawn from the SAME `grid-template` the wall uses, so it cannot disagree with
	 * it.
	 */
	import { area, template, type Layout } from '$lib/theater/layouts';

	interface Props {
		shape: Pick<Layout, 'shape' | 'strip'>;
	}

	let { shape }: Props = $props();

	const grid = $derived(template(shape.shape));
</script>

<!-- Set through the CSSOM: the policy refuses style attributes silently. -->
<span class="picture" aria-hidden="true">
	<span class="glyph" style:--shape={grid}>
		{#each shape.shape.slots as slot, at (at)}
			<span class="block" style:grid-area={area(slot)}></span>
		{/each}
	</span>
	<!-- The strip, or Center Stage would draw as a wall of one. -->
	{#if shape.strip}
		<span class="strip">
			{#each { length: shape.strip } as _, at (at)}
				<span class="block"></span>
			{/each}
		</span>
	{/if}
</span>

<style>
	.picture {
		--glyph-scale: 2;
		display: flex;
		flex-direction: column;
		gap: calc(2px * var(--glyph-scale));
	}

	.strip {
		display: flex;
		gap: calc(1px * var(--glyph-scale));
		block-size: calc(4px * var(--glyph-scale));
	}

	.strip .block {
		flex: 1;
	}

	.glyph {
		display: grid;
		/* The wall Sift ships with, the same fallback the wall takes. */
		grid-template: var(--shape, '. .' 1fr / 1fr 1fr);
		gap: calc(2px * var(--glyph-scale));
		inline-size: calc(22px * var(--glyph-scale));
		block-size: calc(16px * var(--glyph-scale));
	}

	.block {
		border-radius: calc(1px * var(--glyph-scale));
		background: currentColor;
	}
</style>
