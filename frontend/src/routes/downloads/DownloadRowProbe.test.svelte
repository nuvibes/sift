<script lang="ts">
	/* A harness for the row: a download row lays its cells into the columns its list declares,
	   so it is drawn inside the list, the one arrangement the screen draws it in. */
	import type { ComponentProps } from 'svelte';
	import DataRows from '$lib/components/common/DataRows.svelte';
	import DownloadRow, { DOWNLOAD_ACTIONS, downloadColumns } from './DownloadRow.svelte';
	import type { DownloadItem } from './queue.svelte';

	let props: ComponentProps<typeof DownloadRow> = $props();
</script>

<DataRows
	items={[props.item]}
	key={(one: DownloadItem) => one.id}
	label="Downloads"
	columns={downloadColumns(props.narrow ?? false)}
	actions={DOWNLOAD_ACTIONS}
	folds
>
	{#snippet row()}
		<DownloadRow {...props} />
	{/snippet}
</DataRows>
