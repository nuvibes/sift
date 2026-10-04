<script lang="ts">
	/*
	 * The frame you are dragging to, above the scrubber.
	 *
	 * Every frame is a tile in one picture that was built when the file was imported, so this seeks
	 * nothing and downloads nothing per frame: it moves that picture behind a window one tile wide.
	 * Dragging a long video therefore costs one request for the whole gesture.
	 *
	 * Nothing is drawn until the sheet has loaded. The tile's height is the sheet's height over its
	 * rows, so before the picture is there the shape of a frame is unknown, and a guess would show
	 * every preview at the wrong aspect for the first moment of the first drag.
	 */
	import { frameAt, frameBox, type SpriteSheet } from '$lib/player/trickplay';

	interface Props {
		/** How the sheet is cut up, as the server reported it. */
		sheet: SpriteSheet;
		/** Where the sheet is. */
		url: string;
		/** The moment being pointed at, in seconds. */
		seconds: number;
		/** How long the clip is, in seconds. */
		duration: number;
		/** How far along the timeline to sit, in pixels from its start. */
		at: number;
		/** That moment on the clock, spelled by whoever owns the clock. */
		label: string;
	}

	let { sheet, url, seconds, duration, at, label }: Props = $props();

	/* The sheet's own size. Read off the loaded picture rather than worked out from the video's
	 * shape: only the tile WIDTH is recorded, and dividing the picture by the grid gives both. */
	let natural = $state({ width: 0, height: 0 });
	const loaded = $derived(natural.width > 0 && natural.height > 0);

	/* The frame drawn at the width its tiles were built at. Larger would be a magnified thumbnail;
	 * this is the size the picture actually holds. */
	const WIDTH = 160;

	const box = $derived(frameBox(sheet, frameAt(sheet, seconds, duration), WIDTH, natural));

	function measure(event: Event) {
		const image = event.currentTarget as HTMLImageElement;
		natural = { width: image.naturalWidth, height: image.naturalHeight };
	}
</script>

<!--
	The sheet, fetched the ordinary way and never drawn as itself: what is shown is one tile of it,
	so this is here to be loaded and measured rather than to be looked at. A real `<img>` rather than
	one built in script, so the request is part of the markup and the browser's own cache is what
	backs the window below on every later hover.

	It keeps its size in the layout at nothing, which does not stop the fetch: an image's intrinsic
	size is what it was encoded at, whatever the box around it says.
-->
<img class="strip" src={url} alt="" aria-hidden="true" onload={measure} />

{#if loaded}
	<figure
		class="frame"
		style:--at="{at}px"
		style:width="{box.width}px"
		style:height="{box.height}px"
		style:background-image="url({url})"
		style:background-size={box.backgroundSize}
		style:background-position={box.backgroundPosition}
	>
		<figcaption class="at">{label}</figcaption>
	</figure>
{/if}

<style>
	/* Fetched, never shown. Out of the layout and out of the way of the pointer. */
	.strip {
		position: absolute;
		inline-size: 0;
		block-size: 0;
		opacity: 0;
		pointer-events: none;
	}

	/*
	 * Above the timeline, centred on the point being aimed at.
	 *
	 * `--at` is set on the element, which compiles to a property rather than a style attribute:
	 * the policy this app is served under refuses those silently, and the frame would sit at the
	 * start of the timeline whatever the pointer did.
	 *
	 * It never takes the pointer. The gesture belongs to the scrubber underneath, and a frame that
	 * swallowed a drag would end the drag the moment the preview appeared.
	 *
	 * The gap above the timeline's own top edge is clear air on every bar: the timeline is the top
	 * row of each (the popout player's, Theater's, the Audio player's), so the frame stands above
	 * the bar and over nothing somebody is reading as they drag, with no lift.
	 */
	.frame {
		position: absolute;
		inset-block-end: calc(100% + var(--space-2));
		inset-inline-start: var(--at, 0);
		translate: -50% 0;
		z-index: 4;
		margin: 0;
		overflow: hidden;
		border-radius: var(--radius-lg);
		background-color: var(--sift-bg);
		background-repeat: no-repeat;
		box-shadow: var(--elev-2);
		pointer-events: none;
	}

	/* The moment, on the frame rather than beside it: the frame is the thing being aimed at, and a
	   caption underneath would push it away from the timeline it belongs to. */
	.at {
		position: absolute;
		inset: auto 0 0 0;
		padding: 2px 0;
		background: var(--sift-scrim);
		color: var(--sift-ink);
		font: var(--text-data);
		font-variant-numeric: tabular-nums;
		text-align: center;
	}
</style>
