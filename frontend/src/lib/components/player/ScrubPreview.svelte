<script lang="ts">
	/*
	 * The frame being dragged to, one tile of the strip moved behind a window: one request a
	 * gesture. Nothing is drawn until the sheet has loaded, since its tile height is unknown till
	 * then.
	 */
	import { frameAt, frameBox, type SpriteSheet } from '$lib/player/trickplay';

	interface Props {
		sheet: SpriteSheet;
		url: string;
		seconds: number;
		duration: number;
		at: number;
		label: string;
	}

	let { sheet, url, seconds, duration, at, label }: Props = $props();

	/* Only the tile WIDTH is recorded; the loaded picture gives both. */
	let natural = $state({ width: 0, height: 0 });
	const loaded = $derived(natural.width > 0 && natural.height > 0);

	/* The width the tiles were built at. */
	const WIDTH = 160;

	const box = $derived(frameBox(sheet, frameAt(sheet, seconds, duration), WIDTH, natural));

	function measure(event: Event) {
		const image = event.currentTarget as HTMLImageElement;
		natural = { width: image.naturalWidth, height: image.naturalHeight };
	}
</script>

<!-- Loaded and measured, never shown: the browser's cache backs every later hover. -->
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
	.strip {
		position: absolute;
		inline-size: 0;
		block-size: 0;
		opacity: 0;
		pointer-events: none;
	}

	/* `--at` as a property (the policy refuses style attributes); it never takes the pointer. */
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
