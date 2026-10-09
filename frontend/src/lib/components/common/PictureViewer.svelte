<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'PictureViewer',
		category: 'surface',
		role: 'one picture filling the screen with nothing else on it, magnified on demand',
		basis: 'composes:Modal',
		states: ['fit', 'magnified']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/*
	 * One picture filling the screen, with nothing else on it. Wheel to magnify, drag to move,
	 * double-press to fit; Escape, the corner or a press beside the picture to leave.
	 * WHY NOT BITS-UI: it is a flavour of `Modal`, the one file allowed to import the library's dialog parts.
	 */
	import Modal from './Modal.svelte';
	import { Zoomable } from './zoom.svelte';
	import Icon from '$lib/components/Icon.svelte';

	interface Props {
		open?: boolean;
		src: string;
		/** What it is of, for the heading and for anybody who cannot see it. */
		name: string;
	}

	let { open = $bindable(false), src, name }: Props = $props();

	const view = new Zoomable();
	/* This viewer is a magnifier the whole time it is open. */
	view.canMagnify = true;
	let picture = $state<HTMLImageElement | null>(null);

	$effect(() => {
		view.watch(picture);
	});

	/* A new picture, or closing, starts again at fit. */
	$effect(() => {
		void src;
		void open;
		view.reset();
	});

	/** A press ending on the space around the picture closes it, unless it panned. */
	function letGo(event: PointerEvent): void {
		const dragged = view.release(event);
		if (!dragged && event.target === event.currentTarget) open = false;
	}
</script>

<Modal bind:open bleed title={name} description="Press Escape to close." sheetClass="viewer">
	{#snippet children()}
		<!--
		The gestures are on the whole layer, so the empty width beside a portrait answers too.
		-->
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<!-- svelte-ignore a11y_click_events_have_key_events: magnifying is a POINTER affordance and
		has no keyboard meaning; the picture is open and Escape closes it. -->
		<div
			class="field"
			style:cursor={view.cursor}
			onwheel={(event) => view.wheel(event)}
			onpointerdown={(event) => view.grab(event)}
			onpointermove={(event) => view.drag(event)}
			onpointerup={letGo}
			onpointercancel={(event) => view.release(event)}
			onclick={(event) =>
				/* The second click of a double-click is not a press: without this a double-click
				   would step the zoom in and straight back out, which reads as the picture
				   flinching. A refusal rather than `=== 1`, because a click dispatched by a test
				   carries no count at all. */
				event.detail <= 1 && !view.panned && view.step(event)}
		>
			<img
				bind:this={picture}
				class="picture"
				{src}
				alt={name}
				draggable="false"
				style:scale={view.scale}
				style:translate={view.offset}
				style:transition={view.transition}
			/>
		</div>

		<button type="button" class="leave" aria-label="Close" onclick={() => (open = false)}>
			<Icon name="close" size={20} />
		</button>
	{/snippet}
</Modal>

<style>
	/* WHY NOT SHARED: button: the corner control on a full-bleed picture is a bare glyph on the picture
	itself, where a ground and padding would be chrome. */

	.field {
		inline-size: 100%;
		block-size: 100%;
		display: grid;
		place-items: center;
		overflow: hidden;
		touch-action: none;
	}

	/* Fitted, never cropped or enlarged. No transform transition here, or the wheel lags; a stepped
	zoom asks for one (`Zoomable.transition`). */

	.picture {
		max-inline-size: 100%;
		max-block-size: 100dvh;
		object-fit: contain;
		user-select: none;
		transform-origin: center;
	}

	.leave {
		position: absolute;
		inset-block-start: var(--space-4);
		inset-inline-end: var(--space-4);
		display: inline-flex;
		align-items: center;
		justify-content: center;
		inline-size: 2.5rem;
		block-size: 2.5rem;
		border: 0;
		border-radius: var(--radius-full);
		background: var(--sift-surface-2);
		color: var(--sift-ink);
		cursor: pointer;
	}

	.leave:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	/* A finger's size on a phone, clear of the notch. */
	@media (max-width: 767px) {
		.leave {
			inset-block-start: calc(var(--space-4) + var(--safe-top));
			inset-inline-end: calc(var(--space-4) + var(--safe-right));
			inline-size: var(--touch-target);
			block-size: var(--touch-target);
		}
	}
</style>
