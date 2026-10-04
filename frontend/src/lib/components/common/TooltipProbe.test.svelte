<script lang="ts">
	/* A harness for `Tooltip`, because a tooltip's child is a SNIPPET.
	 *
	 * `mount` from a `.test.ts` hands props as values, and a snippet is not something a test file can
	 * write. So the control the label belongs to is drawn here, in a component, and the test drives
	 * the real `Tooltip` around it.
	 */
	import Tooltip from './Tooltip.svelte';

	/* Whether the wrapper is allowed to be squeezed below its content. Off by default here as it is
	   in the component, so a test that says nothing is testing the shape every caller gets. */
	let { shrinks = false }: { shrinks?: boolean } = $props();
</script>

<Tooltip label="Copy the filename" {shrinks}>
	<button type="button">a-picture.jpg</button>
</Tooltip>
