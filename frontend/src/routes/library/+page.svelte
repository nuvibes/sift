<script lang="ts">
	/*
	 * Library: every kind of thing the files are grouped into, one row each.
	 *
	 * The phone's way to every screen the rail lists, since a phone has no rail. The rows are the
	 * rail's own destinations (`LIBRARY_ROWS`), so a destination has one name, one glyph and one
	 * address on every device, and a new one reaches this list by saying where it lives on a phone.
	 *
	 * A real screen at a real address on every width. A desktop has the rail and never needs it,
	 * and it is still a list that works there rather than a page that only makes sense on a phone.
	 */
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
