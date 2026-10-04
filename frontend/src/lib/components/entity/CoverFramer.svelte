<script lang="ts">
	/*
	 * Framing a cover: the whole picture, with the window the cover is drawn as laid over it.
	 *
	 * The window is the picture editor's crop rectangle (`components/edit/CropStage`, the one crop
	 * control in the app) held to the cover box's shape: the same stage, dimming, eight grips and
	 * moving middle, and arrow keys. Resizing it from a corner is the zoom; there is no slider,
	 * wheel or pinch of its own.
	 *
	 * What this file owns is the cover's side of the handshake, none of the drawing: the picture's
	 * shape (measured from the picture when it is an address, handed in when it is drawn another
	 * way, such as a moment of a clip; see `picture`), the units the control works in
	 * (`coverSpace`), and turning its box back into a `Frame` (`windowOf`) no smaller than
	 * `atLeast` allows. It does not know which entity this is or how the frame is saved: `frame` is
	 * bound, and the sheet around it saves it through the cover's own PUT.
	 */
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
		/**
		 * The whole picture drawn some other way, at the size given: a tile of a clip's strip,
		 * which has no address of its own. `aspect` must come with it.
		 */
		picture?: Snippet<[{ width: number; height: number }]>;
		/** The picture's width over its height, where it cannot be measured from `src`. */
		aspect?: number;
		/** The cover box's width over its height. Every cover Sift draws is a 3:4 portrait. */
		ratio?: number;
		/** Told the picture's shape once it is known, so the sheet can tell an unmoved window
		 *  (saved as no frame) from a moved one. */
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

	/* Once the shape is known, the window is ALWAYS a window of the right shape: a stored one is
	   re-made to it, and none opens on the middle. Written back to `frame` so the sheet saves exactly
	   what is drawn. Once per shape, and reading `frame` untracked: every drag writes it, and this is
	   about the picture arriving, not about the window moving. A new picture is a new editor (the
	   sheet keys it) so there is no second picture to wait for here. */
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

	/* The control's box is the window, read in the control's units; what it hands back is turned
	   into a window again. Derived rather than held beside `frame`, so there is one value: an
	   untouched window is never round-tripped through the box, and Save on an untouched editor
	   still writes nothing. */
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
		<!-- Fetched to be measured as well as shown; until it loads there is no shape to hold the
		     window to, so the stage waits for it. -->
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

	/* Filling the stage exactly, which is the shape of the picture: the control reads every pointer
	   as a fraction of the stage, so a letterboxed picture would be framed in one place and cut in
	   another. */
	.whole {
		display: block;
		inline-size: 100%;
		block-size: 100%;
		object-fit: fill;
		pointer-events: none;
	}
</style>
