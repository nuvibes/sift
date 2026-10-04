<script lang="ts">
	/* A harness for People Sift can recognize with its tab line: the panel, and the control it reports
	   drawn where the route draws it. */
	import type { Snippet } from 'svelte';
	import IdentifiedPanel from './IdentifiedPanel.svelte';

	let tools = $state<Snippet | null>(null);
</script>

<div class="tabline">
	{#if tools}{@render tools()}{/if}
</div>
<IdentifiedPanel ontools={(reported: Snippet | null) => (tools = reported)} />
