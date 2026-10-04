<script lang="ts">
	/*
	 * A shape, drawn as the shape.
	 *
	 * Words do not say what a wall will look like. "One above two" and "Two above one" are one word
	 * apart and describe opposite pictures, and a list of seven of them is seven things to read before
	 * choosing between pictures.
	 *
	 * Drawn from the SAME `grid-template` the wall itself uses, and with the same number of blocks, so
	 * this cannot come to disagree with what clicking it produces. A second table of little pictures
	 * would be exactly the fault the layout list's own comment already warns about, one step further
	 * along.
	 */
	import { area, template, type Layout } from '$lib/theater/layouts';

	interface Props {
		/* The shape and its strip, which is all a picture of it needs. */
		shape: Pick<Layout, 'shape' | 'strip'>;
	}

	let { shape }: Props = $props();

	const grid = $derived(template(shape.shape));
</script>

<!-- The property is set through the CSSOM rather than written as a style attribute: the policy this
     app is served under refuses those, and it refuses them silently. -->
<span class="picture" aria-hidden="true">
	<span class="glyph" style:--shape={grid}>
		{#each shape.shape.slots as slot, at (at)}
			<span class="block" style:grid-area={area(slot)}></span>
		{/each}
	</span>
	<!-- The strip, if this layout opens one. Drawn as what it is (a row of small blocks under
	     the wall) because Center Stage's focus half is a single feed, and without the strip its
	     picture is one square: the same picture a wall of one would have. -->
	{#if shape.strip}
		<span class="strip">
			{#each { length: shape.strip } as _, at (at)}
				<span class="block"></span>
			{/each}
		</span>
	{/if}
</span>

<style>
	/* The wall, and under it the strip: one picture of what choosing this produces. */
	.picture {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}

	.strip {
		display: flex;
		gap: 1px;
		block-size: 4px;
	}

	.strip .block {
		flex: 1;
	}

	.glyph {
		display: grid;
		/* The fallback is the wall Sift ships with, so a property that somehow did not arrive draws
		   two blocks side by side rather than one column: a chosen degradation, and the same one
		   the wall itself takes. */
		grid-template: var(--shape, '. .' 1fr / 1fr 1fr);
		gap: 2px;
		inline-size: 22px;
		block-size: 16px;
	}

	.block {
		border-radius: 1px;
		background: currentColor;
	}
</style>
