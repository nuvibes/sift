<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'MarkedText',
		category: 'primitive',
		role: 'a name with the letters somebody typed picked out',
		basis: 'own',
		states: ['default']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: there is no behaviour here at all. It is a name, split into the letters that
	   matched and the letters that did not, and one rule saying what a matched run looks like. */

	/*
	 * A name with the letters somebody typed picked out.
	 *
	 * ## Why a component rather than three lines of markup wherever it is wanted
	 *
	 * The SPLIT was already shared: `$lib/search/search-marks` does it, and does it by producing runs
	 * rather than a string of markup, so a person in somebody's library called `<b>` stays text. This
	 * shares what a matched run LOOKS like, for the search dropdown and the settings search. Two copies of one
	 * appearance is how the same idea ends up two different colours on two screens, and "the letters
	 * that matched" is exactly the kind of thing a reader learns once and expects everywhere.
	 *
	 * ## `<b>` and not `<strong>`
	 *
	 * This is the part of a name that matched, which is a difference in appearance and not in
	 * emphasis. A screen reader announcing every marked run would read a name out in pieces.
	 */
	import { marks } from '$lib/search/search-marks';

	interface Props {
		/** The whole name, exactly as it is stored. */
		text: string;
		/** What was typed. An empty needle marks nothing, which is what an untouched box wants. */
		typed: string;
	}

	let { text, typed }: Props = $props();
</script>

<!-- No whitespace between the runs, and that is why this is written on one line: a name is one word
     to the reader, and a newline between two runs of it is a space on the screen. -->
{#each marks(text, typed) as part, at (at)}{#if part.hit}<b class="hit">{part.text}</b
		>{:else}{part.text}{/if}{/each}

<style>
	.hit {
		font-weight: 700;
		color: var(--sift-accent-text);
	}
</style>
