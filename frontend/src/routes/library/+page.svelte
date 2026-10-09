<script lang="ts">
	/* Library: every kind of thing the files are grouped into, one row each. */
	import { session } from '$lib/shell/session.svelte';
	import DestinationList from '$lib/components/shell/DestinationList.svelte';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import { LIBRARY_ROWS } from '$lib/components/shell/nav';

	/* A guest is shown the rows a guest can open, for the rail's reason. */
	const rows = $derived(LIBRARY_ROWS.filter((item) => !item.admin || session.isAdmin));
</script>

<PageFrame measure>
	{#snippet header()}
		<PageHeader title="Library" icon="newsstand" />
	{/snippet}
	<DestinationList items={rows} label="Library" />
</PageFrame>
