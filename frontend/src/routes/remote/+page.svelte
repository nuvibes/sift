<script lang="ts">
	/* Remote: the phone as a remote for what is already open at the desk. */
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import RemoteScreen from '$lib/remote/RemoteScreen.svelte';
	import { COPY } from '$lib/remote/copy';
</script>

<PageFrame measure>
	{#snippet header()}
		<PageHeader title={COPY.title} icon="settings_remote" />
	{/snippet}

	<RemoteScreen />
</PageFrame>
