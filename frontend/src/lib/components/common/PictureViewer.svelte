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
	 * One picture, filling the screen, with nothing else on it.
	 *
	 * ## Why this is not a dialog with a picture in it
	 *
	 * A sheet with a heading, a sentence and the page dimmed behind it gives a photograph about
	 * four hundred pixels wide, in a box, with the thing being looked at still half-visible around
	 * it: every part of pressing a picture except the part somebody wanted. A picture opened is a
	 * picture looked at, so this covers everything and draws one thing.
	 *
	 * ## Why it is still `Modal`
	 *
	 * Because what a layer over the whole application needs is not `position: fixed`. It is the
	 * focus trap, Escape, the return of focus to whatever was pressed, and the rest of the page
	 * being hidden from a screen reader, none of which is visible and all of which is missing from
	 * a hand-written overlay. There is one door to the dialog primitives and a contract test that
	 * keeps it that way, so filling the screen is a flavour of that door rather than a second one.
	 *
	 * ## The gestures
	 *
	 * Wheel to magnify, drag to move, double-press to go back to fitting. Escape, the corner, or a
	 * press on the space beside the picture to leave. The arithmetic comes from `Zoomable`, which
	 * the player's still uses as well: keeping the point under the pointer belongs to neither
	 * surface.
	 *
	 * WHY NOT BITS-UI: it is a flavour of `Modal`, which is the one file allowed to import the
	 * library's dialog parts. Reaching for them here would be a second door to the thing that
	 * exists to have one.
	 */
	import Modal from './Modal.svelte';
	import { Zoomable } from './zoom.svelte';
	import Icon from '$lib/components/Icon.svelte';

	interface Props {
		open?: boolean;
		/** Where the picture is. */
		src: string;
		/** What it is of, for the heading and for anybody who cannot see it. */
		name: string;
	}

	let { open = $bindable(false), src, name }: Props = $props();

	const view = new Zoomable();
	/* This viewer IS a magnifier (it exists because somebody pressed a picture to look closer)
	   so the offer stands the whole time it is open, and the pointer says so. Unlike the still in
	   the player, there is no windowed state here for it to be wrong in. */
	view.canMagnify = true;
	let picture = $state<HTMLImageElement | null>(null);

	$effect(() => {
		view.watch(picture);
	});

	/* A new picture, or being closed, starts again at fit-to-screen. Somebody who magnified a face
	   and came back later is not still looking at that face; they are opening a picture. */
	$effect(() => {
		void src;
		void open;
		view.reset();
	});

	/**
	 * Letting go: a press that ends on the empty space AROUND the picture closes this.
	 *
	 * Every other sheet in the app is dismissed by clicking beside it, and the library gives that
	 * away free, but only where there is a beside. This one fills the screen, so the gesture
	 * surface IS the dialog and an outside press is something the library can never see. Without
	 * this the viewer would have exactly one way out, the corner button, on a layer whose whole
	 * point is that it covers everything else.
	 *
	 * Two things stop it firing when it should not. A press that ends on the PICTURE is somebody
	 * looking at the picture: `event.target` is the image and not this surface. And a press that
	 * PANNED a magnified one ends wherever the pointer got to, which is often the empty space; the
	 * release says whether a drag was in progress, so that answer is asked for rather than guessed
	 * at from how far anything moved.
	 */
	function letGo(event: PointerEvent): void {
		const dragged = view.release(event);
		if (!dragged && event.target === event.currentTarget) open = false;
	}
</script>

<Modal bind:open bleed title={name} description="Press Escape to close." sheetClass="viewer">
	{#snippet children()}
		<!-- The surface the gestures are on is the whole layer, not the picture: at fit-to-screen a
		     portrait leaves most of the width empty, and a wheel over that emptiness meaning nothing
		     is a magnifier that works in some places. -->
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<!-- svelte-ignore a11y_click_events_have_key_events: magnifying is a POINTER affordance and
		     has no keyboard meaning: a press steps the zoom about the point pressed, and there is no
		     point without a pointer. Nothing is reachable only this way: the picture is already
		     open and Escape already closes it, so this adds a gesture for people who have one
		     rather than taking anything from people who do not. The wheel and the drag beside it are
		     exempt for the same reason. -->
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
	   itself: the shared button draws a ground, a padding and a label slot, every one of which is
	   chrome over the thing being looked at. Position and shape only; the focus ring is below. */

	.field {
		inline-size: 100%;
		block-size: 100%;
		display: grid;
		place-items: center;
		overflow: hidden;
		touch-action: none;
	}

	/*
	 * The cursor over a magnified picture is `Zoomable`'s answer, not a rule here (see the getter),
	 * shared by every surface that magnifies a picture.
	 */

	/* Fitted, never cropped and never blown up past the window: this is the picture itself rather
	   than a decorative fill, and a portrait stretched to a landscape screen is a different picture.

	   No transition on the transform FROM HERE. A wheel produces a stream of them, and an eased
	   transform turns a magnifier into something that lags a frame behind the pointer. A stepped
	   zoom is the exception and asks for one itself, for the length of that one jump. See
	   `Zoomable.transition`. */
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
