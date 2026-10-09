<script lang="ts">
	/* Framing a cover: the whole picture under the editor's crop window (`edit/CropStage`), held to
	 * the cover's shape; this owns the shape, the units and turning the box back into a `Frame`. */
	import { untrack, type Snippet } from 'svelte';
	import CropStage from '$lib/components/edit/CropStage.svelte';
	import {
		COVER_RATIO,
		atLeast,
		boxOf,
		coverSpace,
		opening,
		windowOf,
		type Frame
	} from '$lib/entity/cover-frame';
	import type { Box } from '$lib/edit/geometry';

	interface Props {
		/** The window. Null opens on the middle; it is always a window once the picture is known. */
		frame?: Frame | null;
		/** The whole picture, by address. Measured when it loads. */
		src?: string;
		/** The picture drawn another way (a strip tile), with `aspect`. */
		picture?: Snippet<[{ width: number; height: number }]>;
		/** The picture's width over its height, where it cannot be measured from `src`. */
		aspect?: number;
		/** The cover box's width over its height. Every cover Sift draws is a 3:4 portrait. */
		ratio?: number;
		/** Told the picture's shape, so an unmoved window is saved as no frame. */
		onmeasured?: (aspect: number) => void;
	}

	let {
		frame = $bindable(null),
		src,
		picture: drawn,
		aspect: given,
		ratio = COVER_RATIO,
		onmeasured
	}: Props = $props();

	let measured = $state<number | null>(null);
	const aspect = $derived(given ?? measured);
	const space = $derived(aspect ? coverSpace(aspect) : null);

	/* Once the shape is known the window is always of the right shape, written back to `frame`;
	   once per shape, `frame` read untracked. */
	let shapedFor: number | null = null;
	$effect(() => {
		if (!aspect || aspect === shapedFor) return;
		shapedFor = aspect;
		onmeasured?.(aspect);
		frame = opening(
			untrack(() => frame),
			aspect,
			ratio
		);
	});

	/* The box derived from `frame`, so an untouched window writes nothing. */
	function readBox(): Box {
		return frame && space ? boxOf(frame, space) : { left: 0, top: 0, width: 0, height: 0 };
	}

	function writeBox(next: Box): void {
		if (!frame || !space || !aspect) return;
		frame = atLeast(windowOf(next, space), frame, aspect, ratio);
	}

	function measure(event: Event) {
		const image = event.currentTarget as HTMLImageElement;
		if (image.naturalWidth > 0 && image.naturalHeight > 0) {
			measured = image.naturalWidth / image.naturalHeight;
		}
	}
</script>

<div class="framer">
	{#if src && !given}
		<!-- Fetched to be measured; the stage waits for its shape. -->
		<img class="measure" {src} alt="" aria-hidden="true" onload={measure} />
	{/if}

	{#if space && frame}
		<CropStage frame={space} bind:box={readBox, writeBox} {ratio} called="frame">
			{#snippet picture(size)}
				{#if drawn}
					{@render drawn(size)}
				{:else if src}
					<img class="whole" {src} alt="" draggable="false" />
				{/if}
			{/snippet}
		</CropStage>
	{:else}
		<p class="quiet">Reading the picture.</p>
	{/if}
</div>

<style>
	.framer {
		display: grid;
		justify-items: center;
		gap: var(--space-3);
		inline-size: 100%;
	}

	/* Fetched to be measured; the stage draws its own copy once the shape is known. */
	.measure {
		position: absolute;
		inline-size: 0;
		block-size: 0;
		opacity: 0;
		pointer-events: none;
	}

	/* Filling the stage exactly, as the control reads pointers as fractions of it. */
	.whole {
		display: block;
		inline-size: 100%;
		block-size: 100%;
		object-fit: fill;
		pointer-events: none;
	}
</style>
