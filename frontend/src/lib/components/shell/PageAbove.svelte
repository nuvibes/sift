<script lang="ts">
	/*
	 * The band that names a page, above the row that heads what is shown of it.
	 *
	 * An entity's identity (its cover, its name, its facts) on every tab of its page, a strip of
	 * faces over a wall: whatever a screen puts in its frame's header above the heading row. One
	 * placement with one distance under it (`--above-gap`), so the tabs under a cover stand at the
	 * same height whichever tab is open. Every wall and History tab draws this box through it: one
	 * written as the frame's header over its tools row would put the strip 4px higher there than on
	 * every other tab.
	 *
	 * NOT ON THE GALLERY: it draws nothing of its own, only a box with a gap under it around what a
	 * screen hands in; the entity pages and the walls on the gallery are where it is seen.
	 */
	import type { Snippet } from 'svelte';

	let { children }: { children: Snippet } = $props();
</script>

<div class="above">{@render children()}</div>

<style>
	.above {
		/* The band may SHRINK below its content. Without this a wide child (a strip of faces, a long
		   row of tabs) stretches the whole header while it is settling, and the controls at the far
		   end of the heading are pushed off the side of the window for as long as that lasts. */
		min-inline-size: 0;
		margin-block-end: var(--above-gap);
	}
</style>
