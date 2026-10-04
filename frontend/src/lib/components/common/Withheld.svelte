<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Withheld',
		category: 'primitive',
		role: 'the picture area of a thing Sift is keeping from view, drawn as a blurred ground with the Hidden mark',
		basis: 'composes:Icon',
		states: ['withheld']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/*
	 * WHY NOT BITS-UI: bits-ui has no such primitive: this is a blurred ground and a mark, shape and colour only.
	 * The face of a Hidden file or row while Hidden is shut: there is something here, and it is not
	 * being shown.
	 *
	 * No picture is behind it (the server sends none), so this is not a layer over anything. It is
	 * the picture area itself, drawn as the ground it always has, blurred, with the Hidden mark in
	 * the middle. A flat fill reads as a picture that failed to load; the blur reads as out of focus.
	 *
	 * Not `Veil`, and never wearing the `veil` class: that is the dimmed sheet behind a dialog, fixed
	 * to the window on a dialog's layer, and a card face named after it would wear that layer too.
	 */
	import Icon from '$lib/components/Icon.svelte';

	interface Props {
		/** What the mark is called, for somebody who cannot see it. Left off where the control
		 *  around it already says so. */
		label?: string;
	}

	let { label }: Props = $props();
</script>

<span class="withheld">
	<!-- 34: the fixed size nearest twice a corner mark. This mark is the picture, not a mark on one. -->
	<Icon name="visibility_off" size={34} filled {label} />
</span>

<style>
	.withheld {
		position: relative;
		display: grid;
		place-items: center;
		inline-size: 100%;
		block-size: 100%;
		overflow: hidden;
		color: var(--sift-ink-3);
	}

	/* The ground on a layer under the mark rather than a filter on the box, so the mark stays sharp.
	   A blur of a flat fill draws a flat fill, which is the "failed to load" this is not; so the
	   ground wears the frost (`--frost-picture`: made-up patches of light and shade, the same on
	   every face) and the blur is glass, which leaves none of them an edge. The layer reaches past
	   the box by the blur's own reach, so its edge does not fade to what is behind the face. */
	.withheld::before {
		content: '';
		position: absolute;
		inset: calc(var(--blur-glass) * -3);
		background: var(--frost-picture), var(--sift-surface-3);
		filter: blur(var(--blur-glass));
	}

	.withheld > :global(*) {
		position: relative;
	}
</style>
