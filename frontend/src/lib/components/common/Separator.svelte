<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Separator',
		category: 'primitive',
		role: 'the hairline between two groups of controls or rows, across or down',
		basis: 'bits-ui:Separator',
		states: ['horizontal', 'vertical']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* A line between two different kinds of thing, in one spelling; bits-ui writes the role. Where
	   it sits is the caller's, by a class. */
	import { Separator } from 'bits-ui';

	interface Props {
		vertical?: boolean;
		/** Announced to a screen reader; nearly always drawn only. */
		meaningful?: boolean;
		class?: string;
	}

	let { vertical = false, meaningful = false, class: caller = '' }: Props = $props();
</script>

<Separator.Root
	orientation={vertical ? 'vertical' : 'horizontal'}
	decorative={!meaningful}
	class="separator {vertical ? 'vertical' : 'horizontal'} {caller}"
/>

<style>
	/* DRESSED BY: .separator, .vertical, .horizontal (bits-ui renders the element; these are the
	   classes it is handed, dressed here through :global as every bits-ui class in this app is). */
	:global(.separator.horizontal) {
		flex: none;
		block-size: 1px;
		inline-size: 100%;
		background: var(--sift-line);
	}

	:global(.separator.vertical) {
		flex: none;
		align-self: center;
		inline-size: 1px;
		block-size: 20px;
		margin-inline: var(--space-2);
		background: var(--sift-line);
	}
</style>
