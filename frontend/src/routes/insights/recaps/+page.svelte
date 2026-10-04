<script lang="ts">
	/*
	 * EVERY RECAP THIS ACCOUNT HAS, newest first.
	 *
	 * The Recaps block on Insights names the newest few; this is where the rest are, so a recap from
	 * last March is still one press from the page that listed it. The same list the block draws
	 * (`RecapHeads`), with no limit.
	 */
	import type { Crumb } from '$lib/components/common';
	import RecapHeads from '$lib/components/insights/RecapHeads.svelte';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';

	const crumbs: Crumb[] = [{ label: 'Insights', href: '/insights' }, { label: 'Recaps' }];
</script>

<svelte:head><title>Recaps</title></svelte:head>

<PageFrame {crumbs}>
	{#snippet header()}
		<PageHeader title="Recaps" />
	{/snippet}

	<div class="recaps">
		<RecapHeads scope="page" />
	</div>
</PageFrame>

<style>
	.recaps {
		max-inline-size: var(--page-measure);
	}
</style>
