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
	 * WHY NOT BITS-UI: bits-ui has no such primitive: this is a blurred ground and a mark, shape
	 * and colour only. The face of a Hidden thing while Hidden is shut: the picture area blurred,
	 * the Hidden mark on it. Never `Veil`, whose class brings a dialog's layer.
	 */
	import Icon from '$lib/components/Icon.svelte';

	interface Props {
		/** The mark's name, left off where the control around it says it. */
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

	/* The frosted ground on a layer under the mark, reaching past the box by the blur's reach. */
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
