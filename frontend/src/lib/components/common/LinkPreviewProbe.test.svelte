<script lang="ts">
	/* A harness for the card that opens on a hovered link. Both halves of it are snippets (the
	   trigger and the card) and a snippet cannot be handed to `mount` from a test file. The same
	   arrangement `ContextMenuItemProbe` uses.

	   `openDelay` is nought here so the test does not have to wait out the deliberate pause; the
	   pause is the library's timer and is not this component's to prove. */
	import LinkPreview from './LinkPreview.svelte';

	let { onOpenChange }: { onOpenChange?: (open: boolean) => void } = $props();
</script>

<LinkPreview openDelay={0} closeDelay={0} {onOpenChange}>
	{#snippet trigger({ props })}
		<a {...props} class="who" href="/people/example">Marisol Vane</a>
	{/snippet}
	{#snippet card()}
		<p class="said">Four files</p>
	{/snippet}
</LinkPreview>
