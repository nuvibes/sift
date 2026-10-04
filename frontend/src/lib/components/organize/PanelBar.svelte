<script lang="ts">
	/* WHY NOT BITS-UI: there is no toolbar surface in it. This is a strip with a border; what sits in
	   it (selects, buttons, a sentence) is the library's already. */
	/*
	 * The band of controls over an Organize wall: the dials, the sentence about the whole queue, and
	 * the one press that settles the page.
	 *
	 * Under the shared header and above the cards, on every wall that has controls of its own. Two
	 * walls drawing their controls two ways, at two heights, read as two kinds of screen. One
	 * strip, and a wall that has nothing to put in it draws none.
	 *
	 * Wraps rather than scrolls: at a narrow width the dials fold under the sentence, which is the
	 * same thing the header's tabs do.
	 */
	import type { Snippet } from 'svelte';

	interface Props {
		children: Snippet;
	}

	let { children }: Props = $props();
</script>

<div class="bar">
	{@render children()}
</div>

<style>
	.bar {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2) var(--space-4);
		margin-block-end: var(--space-4);
		padding: var(--space-3);
		border: 1px solid var(--sift-line);
		border-radius: var(--radius-md);
		background: var(--sift-surface-1);
	}

	/*
	 * The sentence about the whole queue reads at the one reading measure, and what that leaves
	 * goes after it, so the presses still end at the bar's far edge. Said here once for every wall
	 * that wears the bar: left to each wall, a sentence runs the width of a wide window, or is
	 * capped by a count of characters and leaves its press stranded mid-bar. Bounded by the bar this
	 * file draws.
	 */
	.bar > :global(p) {
		max-inline-size: var(--reading-measure);
		margin-inline-end: auto;
	}
</style>
