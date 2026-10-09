<script lang="ts">
	/*
	 * The box an asset is shown in, whatever it is: a fixed shape, so the window never jumps; one
	 * element, so fullscreen survives Next onto a photograph; and Next and Previous live here, so a
	 * still has them too. The media and controls come in as snippets.
	 */
	import type { Snippet } from 'svelte';
	import { onMount } from 'svelte';
	import { Button } from '$lib/components/common';
	import { dragOut } from '$lib/capture/copy-out';
	import { setStage } from './stage.svelte';
	import { enterFullscreen } from './fullscreen';
	import { screenChanges } from './motion';

	interface Props {
		media: Snippet;
		/**
		 * On the bar's wake, resting longer: a sentence takes longer to read than a bar to aim at.
		 */
		notice?: Snippet;
		/**
		 * What can be dragged out to another application. Here, not on a tile, whose drag
		 * organises; absent means no drag-out.
		 */
		asset?: { id: string; filename: string };
	}

	let { media, notice, asset }: Props = $props();

	const IDLE_MS = 2500;

	const NOTICE_IDLE_MS = 5000;

	let stage = $state<HTMLElement | null>(null);
	let isFullscreen = $state(false);
	/** Fills the WINDOW where the browser lets only a video fill the screen (an iPhone). */
	let filling = $state(false);
	/** False once the pointer has gone idle; see `wake`. */
	let controlsUp = $state(true);
	let idleTimer: ReturnType<typeof setTimeout> | null = null;
	let noticeUp = $state(true);
	let noticeTimer: ReturnType<typeof setTimeout> | null = null;
	/* A touch screen has no pointer to rest, so its bar never hides. Read once on mount. */
	let hoverCapable = false;

	/*
	 * Put the controls back and restart the idle clock; except on touch, where the bar stays put.
	 */
	function wake() {
		if (leftTimer) clearTimeout(leftTimer);
		leftTimer = null;
		pointed = true;
		controlsUp = true;
		noticeUp = true;
		if (idleTimer) clearTimeout(idleTimer);
		if (noticeTimer) clearTimeout(noticeTimer);
		if (!(isFullscreen || filling) && !hoverCapable) return;
		idleTimer = setTimeout(() => (controlsUp = false), IDLE_MS);
		noticeTimer = setTimeout(() => (noticeUp = false), NOTICE_IDLE_MS);
	}

	/*
	 * The pointer has left the picture: the controls go a second later rather than on the idle
	 * clock.
	 */
	const LEFT_MS = 1000;

	let leftTimer: ReturnType<typeof setTimeout> | null = null;

	/* A class rather than `:hover`, so it can stay true for a second after leaving. */
	let pointed = $state(false);

	function left(event?: PointerEvent) {
		/* A finger lifting is not looking away: on a phone the bar stays. */
		if (event?.pointerType === 'touch' && !(isFullscreen || filling)) return;
		if (idleTimer) clearTimeout(idleTimer);
		if (leftTimer) clearTimeout(leftTimer);
		leftTimer = setTimeout(() => {
			pointed = false;
			controlsUp = false;
			noticeUp = false;
		}, LEFT_MS);
	}

	function hold(event?: PointerEvent) {
		/* Stopped here, or a move over the bar would restart the clock meant to stop. */
		event?.stopPropagation();
		if (leftTimer) clearTimeout(leftTimer);
		leftTimer = null;
		pointed = true;
		controlsUp = true;
		noticeUp = true;
		if (idleTimer) clearTimeout(idleTimer);
		if (noticeTimer) clearTimeout(noticeTimer);
	}

	/*
	 * `document.fullscreenElement` is asked: Escape, F11 and the browser leave without passing
	 * here.
	 */
	function toggleFullscreen() {
		if (filling) {
			filling = false;
			wake();
			return;
		}
		if (document.fullscreenElement) {
			void document.exitFullscreen?.();
			return;
		}
		// The stage, its video (an iPhone), or the window where neither may (`fullscreen.ts`).
		if (stage && !enterFullscreen(stage)) {
			filling = true;
			wake();
		}
	}

	setStage({
		get element() {
			return stage;
		},
		get isFullscreen() {
			return isFullscreen || filling;
		},
		get showing() {
			return controlsUp;
		},
		toggleFullscreen,
		wake,
		hold
	});

	onMount(() => {
		hoverCapable = window.matchMedia?.('(hover: hover)')?.matches ?? false;
		const onFullscreen = () => {
			isFullscreen = document.fullscreenElement === stage;
			// Leaving puts the bar back, so it never stays faded with no way to bring it back.
			wake();
		};
		document.addEventListener('fullscreenchange', onFullscreen);
		return () => {
			document.removeEventListener('fullscreenchange', onFullscreen);
			if (idleTimer) clearTimeout(idleTimer);
		};
	});
</script>

<!--
`onpointermove` here so moving over the controls counts as activity. The stage is not itself
draggable, so `ondragstart` only runs for a drag that began on the media, never the scrub bar.
-->
<div
	class="stage"
	class:fullscreen={isFullscreen || filling}
	class:filling
	class:resting={!controlsUp}
	class:pointed
	bind:this={stage}
	use:screenChanges={{ key: filling }}
	onpointermove={wake}
	onpointerenter={wake}
	onpointerleave={left}
	ondragstart={(event) => {
		if (asset) dragOut(event, asset);
	}}
	role="group"
	aria-label="Viewer"
>
	{@render media()}

	{#if notice}
		<!--
		Held up while the pointer is on it, so it cannot fade from under somebody reading it.
		-->
		<div
			class="notice"
			class:up={noticeUp}
			onpointerenter={hold}
			onpointermove={hold}
			onpointerleave={wake}
			role="status"
		>
			{@render notice()}
		</div>
	{/if}
</div>

<style>
	/* A fixed height: a tall clip letterboxes rather than resizing the window. */
	.stage {
		position: relative;
		block-size: var(--stage-height, min(60vh, 520px));
		background: var(--sift-bg);
		/*
		 * The frame's corner, published for the rounding and the progress line (`Player.svelte`).
		 */
		--frame-corner: var(--stage-radius, var(--radius-xl));
		border-radius: var(--frame-corner);
		overflow: hidden;
		/*
		 * One clip for the picture and the frost: Chromium lets a backdrop filter escape `overflow:
		 * hidden`.
		 */
		clip-path: inset(0 round var(--frame-corner));
	}

	.stage.filling {
		position: fixed;
		inset: 0;
		z-index: var(--z-bar);
		block-size: auto;
		border-radius: 0;
		clip-path: none;
	}

	/*
	 * Everything a stage holds fits the box, `contain`, never cropped: a video, an image, and a GIF
	 * drawn on a `<canvas>` (`$lib/player/animation`) at its own pixel size. `MiniPlayer` names the
	 * same three.
	 */
	.stage :global(:is(video, img, canvas)) {
		inline-size: 100%;
		block-size: 100%;
		object-fit: contain;
		background: var(--sift-bg);
	}

	/*
	 * A global class: the stage does not know its media, only where a bar goes and when it fades.
	 */
	.stage :global(.player-bar) {
		position: absolute;
		inset: auto 0 0 0;
		z-index: 3;
	}

	/* Chrome for the WHOLE picture rather than the strip along its bottom. */
	.stage :global(.player-edge) {
		position: absolute;
		inset: 0;
		z-index: 3;
		/*
		 * NEVER interactive: this layer covers the whole picture above the bar, and the moment it
		 * takes a pointer the bar stops answering. The fades below move the CHILDREN's pointers,
		 * never this one's.
		 */
		pointer-events: none;
	}

	.stage :global(.player-edge) > :global(*) {
		pointer-events: auto;
	}

	/* Drawn whenever the bar is not, never both; under it, so the two cross cleanly. */
	.stage :global(.player-progress) {
		position: absolute;
		/* On the bottom edge, stopping where it meets each curve (`Player.svelte`). */
		inset-block-end: 0;
		z-index: 2;
		opacity: 1;
		transition: opacity var(--dur-fast) var(--ease);
	}

	.stage:fullscreen:not(.resting) :global(.player-progress),
	.stage.fullscreen:not(.resting) :global(.player-progress) {
		opacity: 0;
	}

	@media (hover: hover) {
		.stage:not(.fullscreen):not(.resting).pointed :global(.player-progress),
		.stage:not(.fullscreen):has(:focus-visible) :global(.player-progress) {
			opacity: 0;
		}
	}

	@media (hover: none) {
		.stage:not(.fullscreen) :global(.player-progress) {
			opacity: 0;
		}
	}

	/*
	 * A still's one control, in the corner: a full-width strip over every image would be a
	 * permanent band.
	 */
	.stage :global(.still-control) {
		position: absolute;
		inset: auto var(--space-3) var(--space-3) auto;
		z-index: 3;
		opacity: 0;
		transition: opacity var(--dur-fast) var(--ease);
	}

	/* Inset past the corner radius, or the clip makes it unhoverable. */
	.notice {
		position: absolute;
		inset-block-start: var(--space-5);
		inset-inline-end: var(--space-5);
		z-index: 2;
		opacity: 0;
		pointer-events: none;
		transition: opacity var(--dur-fast) var(--ease);
	}

	/* Interactive only while up. */
	.notice.up {
		opacity: 1;
		pointer-events: auto;
	}

	/* Focus as well as hover; `:has(:focus-visible)`, since `:focus-within` matches a click. */
	.stage.pointed :global(.still-control),
	.stage:has(:focus-visible) :global(.still-control) {
		opacity: 1;
	}

	/*
	 * The bar in a window: up while somebody looks, down on the idle clock (`:not(.resting)` lets
	 * the clock win over `:hover`; keyboard focus is not clocked). `pointer-events` goes with the
	 * opacity, or the invisible bar swallows the double-click. Only where hovering is possible.
	 */
	@media (hover: hover) {
		.stage:not(.fullscreen) :global(.player-bar) {
			opacity: 0;
			pointer-events: none;
		}

		/* The layer fades; only the CHILDREN give up their pointers. */
		.stage:not(.fullscreen) :global(.player-edge) {
			opacity: 0;
		}

		.stage:not(.fullscreen) :global(.player-edge) > :global(*) {
			pointer-events: none;
		}

		/*
		 * `:has(:focus-visible)`: `:focus-within` matches a mouse click and would pin the bar up.
		 */
		.stage:not(.fullscreen):not(.resting).pointed :global(.player-bar),
		.stage:not(.fullscreen):has(:focus-visible) :global(.player-bar) {
			opacity: 1;
			pointer-events: auto;
		}

		.stage:not(.fullscreen):not(.resting).pointed :global(.player-edge),
		.stage:not(.fullscreen):has(:focus-visible) :global(.player-edge) {
			opacity: 1;
		}

		.stage:not(.fullscreen):not(.resting).pointed :global(.player-edge) > :global(*),
		.stage:not(.fullscreen):has(:focus-visible) :global(.player-edge) > :global(*) {
			pointer-events: auto;
		}
	}

	.stage:fullscreen,
	.stage.fullscreen {
		block-size: 100%;
		inline-size: 100%;
		border-radius: 0;
		clip-path: none;
	}

	/*
	 * After the hover rule: in fullscreen `:hover` is always true, so resting wins by source order.
	 */
	.stage.resting :global(.player-bar),
	.stage.resting :global(.still-control) {
		opacity: 0;
		pointer-events: none;
	}

	.stage.resting :global(.player-edge) {
		opacity: 0;
	}

	.stage.resting :global(.player-edge) > :global(*) {
		pointer-events: none;
	}

	.stage.resting {
		cursor: none;
	}

	/* The picture's own `cursor: pointer` is more specific, so it is hidden here too. */
	.stage.resting :global(:is(video, img, canvas)) {
		cursor: none;
	}

	.stage :global(.player-bar),
	.stage :global(.player-edge) {
		transition: opacity var(--dur-fast) var(--ease);
	}

	:global(:root[data-motion='reduce']) .stage :global(.player-bar),
	:global(:root[data-motion='reduce']) .stage :global(.player-edge),
	:global(:root[data-motion='reduce']) .stage :global(.player-progress),
	:global(:root[data-motion='reduce']) .stage :global(.still-control) {
		transition: none;
	}
</style>
