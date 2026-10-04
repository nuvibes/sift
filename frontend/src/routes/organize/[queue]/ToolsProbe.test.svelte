<script lang="ts">
	/* A panel that reports one control to the tab line and takes it back as it goes: the whole of
	   the `OnTools` contract, and nothing else, so the route's half can be tested without dragging a
	   real queue into it. See `page.svelte.test.ts`. */
	import { onDestroy } from 'svelte';

	import Button from '$lib/components/common/Button.svelte';
	import type { OnTools } from '$lib/components/organize/OrganizeHeader.svelte';

	let { ontools }: { ontools?: OnTools } = $props();

	$effect(() => {
		ontools?.(tool);
	});
	onDestroy(() => ontools?.(null));
</script>

{#snippet tool()}
	<Button class="probe-tool">Probe tool</Button>
{/snippet}

<p class="probe-body">The work</p>
