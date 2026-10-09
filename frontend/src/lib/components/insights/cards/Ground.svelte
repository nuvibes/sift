<script lang="ts">
	/*
	 * A card's ground: the person's own pictures, blurred to colour and light, under the family's
	 * shade, deeper towards the foot where the words stand, so a card with nothing of its own to
	 * show is never an empty field. A picture that does not load is left out.
	 */
	import { GROUND_MOST } from '$lib/components/insights/cards/family';

	interface Props {
		pictures: readonly string[];
	}

	let { pictures }: Props = $props();

	let failed = $state<ReadonlySet<string>>(new Set());
	/* A full grid: five pictures are drawn as four, so no cell of it stands empty. */
	const loaded = $derived(pictures.filter((one) => !failed.has(one)).slice(0, GROUND_MOST));
	const shown = $derived(loaded.slice(0, loaded.length === 5 ? 4 : loaded.length));
</script>

{#if shown.length > 0}
	<div
		class="ground"
		class:one={shown.length === 1}
		class:two={shown.length === 2 || shown.length === 4}
		aria-hidden="true"
	>
		{#each shown as picture (picture)}
			<img
				src={picture}
				alt=""
				decoding="async"
				onerror={() => (failed = new Set([...failed, picture]))}
			/>
		{/each}
		<span class="shade"></span>
		<span class="foot-shade"></span>
	</div>
{/if}

<style>
	/* Past the card's edge by the blur, so the blur's soft rim falls outside it. */
	.ground {
		position: absolute;
		inset: calc(var(--story-blur) * -2);
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		grid-auto-rows: minmax(0, 1fr);
		overflow: hidden;
	}

	.ground.one {
		grid-template-columns: minmax(0, 1fr);
	}

	.ground.two {
		grid-template-columns: repeat(2, minmax(0, 1fr));
	}

	img {
		inline-size: 100%;
		block-size: 100%;
		object-fit: cover;
		filter: blur(var(--story-blur));
	}

	.shade,
	.foot-shade {
		position: absolute;
		inset-inline: 0;
	}

	.shade {
		inset-block: 0;
		background-color: color-mix(
			in srgb,
			var(--sift-accent-shade-2) var(--card-ground-scrim),
			transparent
		);
	}

	/* The family's deepest shade, clear at the middle and whole at the foot, with no seam. */
	.foot-shade {
		inset-block: 40% 0;
		background-image: linear-gradient(
			rgb(from var(--sift-accent-shade-2) r g b / 0),
			var(--sift-accent-shade-2)
		);
	}
</style>
