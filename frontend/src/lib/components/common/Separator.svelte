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
	/*
	 * A line between two things that are not the same kind of thing.
	 *
	 * ## Why it is one component
	 *
	 * One line, one spelling. Drawn per caller (an `<hr>` on the rail, a span dressed from another
	 * file's stylesheet in the player's drawer and a theater cell) it would be several spellings of
	 * one line, dressed at a distance, free to disagree about the margin.
	 *
	 * ## What the library gives it
	 *
	 * The role. A decorative line is `role="none"` and a screen reader walks past it; a line that
	 * means something is `role="separator"` with its orientation announced. bits-ui's Separator
	 * writes both correctly for either orientation, which is the kind of thing a `<span>` never
	 * gets right by accident.
	 *
	 * ## What it does not decide
	 *
	 * Where it sits. A vertical line on a bar wants a little room either side; a horizontal one
	 * across a column wants room above and below; the rail wants both of those to be its own. The
	 * caller says so with a class, dressed in the caller's own file with an anchored `:global`.
	 */
	import { Separator } from 'bits-ui';

	interface Props {
		vertical?: boolean;
		/**
		 * Whether it is drawn only, or means something to a screen reader. Nearly always drawn only:
		 * the groups either side of it already announce themselves.
		 */
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
