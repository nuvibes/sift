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
	browser's chrome off and Sift's focus ring on; there is no behaviour to own. */

	/* A whole thing that can be pressed (a tile, a face, a card) whose content sets its size, unlike
	 * Button: the reset, the app's focus ring, and a lift that says a picture can be pressed. */
	import type { Snippet } from 'svelte';
	import type { HTMLButtonAttributes } from 'svelte/elements';

	interface Props extends HTMLButtonAttributes {
		children: Snippet;
		/** Chosen, picked or open: the accent ring. Reported, never held. */
		picked?: boolean;
		/**
		 * How it answers the pointer: `lift` in a wall, `wash` in a list, `none` inside another.
		 */
		feedback?: 'lift' | 'wash' | 'none';
		/** The corner. Matches whatever it holds: a tile's is large, a row's is small. */
		radius?: 'sm' | 'md' | 'lg' | 'full';
		/** Room inside the edge: `sm` for a glyph needing a hit area. */
		pad?: 'none' | 'sm';
		/** Extra classes for position and shape, merged so the reset is never replaced. */
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
		/* Everything the browser puts on a button, off. */
		margin: 0;
		padding: 0;
		border: 0;
		background: none;
		color: inherit;
		font: inherit;
		text-align: inherit;
		cursor: pointer;
		/* The ground answers instantly; the scale and ring move slower, to be seen. */
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

	/* A finger's 44px reach on a phone, by an invisible ring that lays nothing out. */
	@media (max-width: 767px) {
		.pressable::after {
			content: '';
			position: absolute;
			inset-block: min(0px, calc((100% - var(--touch-target)) / 2));
			inset-inline: min(0px, calc((100% - var(--touch-target)) / 2));
		}
	}

	/* Outside the shape, so a clipped picture cannot eat it. */
	.pressable:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	.lift:hover:not(:disabled) {
		transform: scale(var(--lift-scale));
	}

	/* The state layer over no ground, so it answers on any surface. */
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

	/* Chosen: the selected ring, inset so a clipping ancestor cannot cut it. */
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
