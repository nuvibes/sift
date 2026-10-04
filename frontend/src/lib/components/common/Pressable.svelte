<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Pressable',
		category: 'primitive',
		role: 'a surface that is itself the control: a real button with none of the dressing',
		basis: 'site:<button>',
		states: ['default']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: the site element already does all of it. This is a real <button> with the
	   browser's own chrome taken off and Sift's focus ring put on: there is no behaviour here for
	   a library to own, and wrapping one would add a component between a press and its handler. */

	/*
	 * A whole thing that can be pressed: a tile, a face, a card.
	 *
	 * ## Why this is not `Button`
	 *
	 * `Button` is a control with a label on it. Everything that makes it one (padding, a minimum
	 * height, a fitted width, a border, `white-space: nowrap`) is wrong for a surface whose SHAPE
	 * is decided by the picture inside it. Passing `tone="ghost"` and then undoing six properties at
	 * the call site is not using the shared button; it is using its name.
	 *
	 * So the two are separate on purpose, and the line between them is: does the content define the
	 * size, or does the control? A row of words in a box is a Button. A 180-pixel still with a
	 * caption under it is this.
	 *
	 * ## What it actually contributes
	 *
	 * Three things, each of which would otherwise be written out per screen.
	 *
	 * The reset: a `<button>` arrives with the operating system's padding, border, background and
	 * font, and a card that forgets one of the four is a grey slab in the middle of the app.
	 *
	 * The focus ring: the app's, not the browser's, and on `:focus-visible` so a pointer press does
	 * not leave a ring behind.
	 *
	 * And the lift, which is what says a picture is pressable at all. A tile has no edge and no
	 * label; without something happening under the pointer it is indistinguishable from a picture.
	 */
	import type { Snippet } from 'svelte';
	import type { HTMLButtonAttributes } from 'svelte/elements';

	interface Props extends HTMLButtonAttributes {
		children: Snippet;
		/**
		 * Currently chosen, picked, or open. Draws the accent ring.
		 *
		 * Reported rather than held, because what is selected nearly always lives elsewhere (the
		 * address, a selection store, the server), and a control keeping its own copy is a second
		 * answer that cannot be corrected when the first changes.
		 */
		picked?: boolean;
		/**
		 * How much it moves under the pointer.
		 *
		 * `lift` scales it slightly, for something in a wall of its own kind where a small change is
		 * legible against its neighbours. `wash` only lightens the ground, for something in a list,
		 * where scaling one row shoves the rest around. `none` for a surface that is inside another
		 * pressable thing and must not answer twice.
		 */
		feedback?: 'lift' | 'wash' | 'none';
		/** The corner. Matches whatever it holds: a tile's is large, a row's is small. */
		radius?: 'sm' | 'md' | 'lg' | 'full';
		/**
		 * Room inside the edge. `none` is the reset (the content is the box); `sm` is one step, for a
		 * glyph that needs a hit area: the cross on a search box. A prop rather than a caller's
		 * rule because the reset here is scoped and beats any plain class a caller puts on it.
		 */
		pad?: 'none' | 'sm';
		/**
		 * Extra classes from the caller, for position and shape, never for the reset.
		 *
		 * Merged rather than spread, and that distinction is load-bearing. `{...rest}` is applied
		 * last, so a `class` arriving that way would replace `pressable wash r-md` outright: the
		 * surface would keep the caller's one class and lose every rule that makes it a pressable
		 * thing, including the reset, leaving the operating system's grey slab with the caller's
		 * layout on it. `Button` merges its classes for the same reason, in the same words, because
		 * the next component to take a `class` prop will need it too.
		 */
		class?: string;
	}

	let {
		children,
		picked = false,
		feedback = 'lift',
		radius = 'lg',
		pad = 'none',
		type = 'button',
		class: extra = '',
		...rest
	}: Props = $props();
</script>

<button
	{type}
	class="pressable {feedback} r-{radius} p-{pad} {extra}"
	class:picked
	aria-pressed={picked ? 'true' : undefined}
	{...rest}
>
	{@render children()}
</button>

<style>
	.pressable {
		position: relative;
		display: block;
		/* Everything the site puts on a button, off. A card that keeps any one of these is the
		   operating system's control sitting in the middle of Sift's. */
		margin: 0;
		padding: 0;
		border: 0;
		background: none;
		color: inherit;
		font: inherit;
		text-align: inherit;
		cursor: pointer;
		/*
		 * Two registers, two durations. A ground lighting up under the pointer is acknowledgement
		 * and gets the instant duration, since anything slower reads as lag; the scale and the ring
		 * are movement, which needs long enough to be seen.
		 */
		transition:
			transform var(--dur-fast) var(--ease),
			background-color var(--dur-instant) var(--ease),
			box-shadow var(--dur-fast) var(--ease);
	}

	.r-sm {
		border-radius: var(--radius-sm);
	}
	.r-md {
		border-radius: var(--radius-md);
	}
	.r-lg {
		border-radius: var(--radius-lg);
	}
	.r-full {
		border-radius: var(--radius-full);
	}

	.p-sm {
		padding: var(--space-1);
	}

	/*
	 * A FINGER'S REACH on a phone, without the mark growing. A tab's word, a chip, a swatch and a
	 * 22px badge are drawn at the size that reads; what a finger needs is the 44px square around
	 * it (`--touch-target`). The ring is invisible and belongs to this element, so a press on it IS
	 * a press here, and it reaches out only as far as the element falls short on each axis: a
	 * pressable already a finger's size draws none past its own edge. Nothing is laid out around
	 * it, so no row grows for a ring nobody sees.
	 */
	@media (max-width: 767px) {
		.pressable::after {
			content: '';
			position: absolute;
			inset-block: min(0px, calc((100% - var(--touch-target)) / 2));
			inset-inline: min(0px, calc((100% - var(--touch-target)) / 2));
		}
	}

	/* Outside the shape, not on it: a ring drawn inside the corner is clipped by whatever the card
	   holds, and a picture with `overflow: hidden` eats it entirely. */
	.pressable:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	.lift:hover:not(:disabled) {
		transform: scale(var(--lift-scale));
	}

	/* The state layer (see `--layer-hover`) over no ground of its own, so it answers on whatever
	   the surface is resting on, a card or a translucent panel alike. */
	.wash:hover:not(:disabled) {
		background-color: color-mix(in srgb, currentColor var(--layer-hover), transparent);
	}

	/* Pressed: a lifted surface gives under the press, a washed one takes the stronger layer. */
	.lift:active:not(:disabled) {
		transform: scale(var(--press-scale));
	}

	.wash:active:not(:disabled) {
		background-color: color-mix(in srgb, currentColor var(--layer-pressed), transparent);
	}

	.pressable:disabled {
		cursor: default;
		opacity: var(--disabled-opacity);
	}

	/*
	 * Chosen: the accent as a ring rather than a fill. A fill behind a picture is invisible, and
	 * one over it hides the thing being chosen.
	 *
	 * Inset, like `--focus-ring`: a shadow painted beyond an element is taken away by any ancestor
	 * that scrolls or clips (see where `app.css` defines the sheet's scroll gutter), so an outset
	 * ring would lose the edge nearest the boundary, as in the cover chooser, whose grid sits
	 * against the top of its viewport. Inset costs a few pixels of the picture, against losing the
	 * ring that says which one is chosen.
	 *
	 * The ring is the selected ring every picture wears (`--selected-ring`). The tick and the
	 * pulled-in picture are the holder's to draw, since this surface does not know whether what it
	 * holds is a picture or a row of words.
	 */
	.picked {
		box-shadow: var(--selected-ring);
	}

	.picked:focus-visible {
		box-shadow: var(--selected-ring), var(--focus-ring);
	}

	:global(:root[data-motion='reduce']) .pressable {
		transition: none;
	}

	:global(:root[data-motion='reduce']) .lift:hover:not(:disabled),
	:global(:root[data-motion='reduce']) .lift:active:not(:disabled) {
		transform: none;
	}
</style>
