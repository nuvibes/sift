<script lang="ts">
	/* NOT ON THE GALLERY: it is one sentence and a button, drawn only when a live server has changed
	   underneath a running window: a condition the gallery cannot produce and should not fake. The
	   `Button` it is made of is on the gallery. */

	/*
	 * "Sift has been updated. This window is still showing the older version."
	 *
	 * The sibling of `UpdateBanner`, and deliberately a separate component, because the two say
	 * different things to different people: that one tells an admin a newer Sift exists and that
	 * installing it is their job; this tells anybody that the server they are connected to has
	 * already changed, and the only thing left to do takes one press.
	 *
	 * Everybody sees it. A guest whose window is a version behind is looking at screens the server
	 * no longer serves, and reloading is something they can do.
	 *
	 * It cannot be dismissed, which is the difference from the banner beside it. Dismissing that
	 * one hides a fact until the next release; here the page is out of date until it is reloaded,
	 * and a banner that could be waved away would leave somebody looking at a stale application
	 * with nothing on screen saying so.
	 */
	import { Button, ShellBanner } from '$lib/components/common';
	import { build } from '$lib/shell/build.svelte';
</script>

<!-- The same shape `UpdateBanner` draws, through `ShellBanner`, because they are the same kind of
     object, and one looking different would read as one being more serious. -->
{#if build.stale}
	<ShellBanner>
		Sift has been updated. This window is still showing the older version.
		{#snippet action()}
			<Button tone="primary" onclick={() => build.reload()}>Reload</Button>
		{/snippet}
	</ShellBanner>
{/if}
