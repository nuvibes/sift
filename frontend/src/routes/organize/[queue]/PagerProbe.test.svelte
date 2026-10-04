<script lang="ts">
	/* A panel that reports a pager over as many rows as the test names, and nothing else: the
	   `OnPaging` contract alone, so the route's half can be tested without a real queue. */
	import { onDestroy } from 'svelte';

	import type { OnPaging } from '$lib/components/common/Pager.svelte';

	let { onpaging }: { onpaging?: OnPaging } = $props();

	$effect(() => {
		const total = Number(document.body.dataset.probeTotal ?? '0');
		onpaging?.({
			offset: 0,
			shown: Math.min(total, 24),
			total,
			noun: 'matches',
			onfirst: () => {},
			onprevious: () => {},
			onnext: () => {},
			onlast: () => {},
			onjump: () => {}
		});
	});
	onDestroy(() => onpaging?.(null));
</script>

<p class="probe-body">The work</p>
