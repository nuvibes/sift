<script lang="ts">
	/* What the editor produces, and how big a smaller copy is made. */
	import { onMount } from 'svelte';
	import { Problem, Skeleton } from '$lib/components/common';
	import SettingGroup from './SettingGroup.svelte';
	import SettingRow from './SettingRow.svelte';
	import { SettingsPanel } from './panel.svelte';
	import { COPY } from './Editing.search';

	const panel = new SettingsPanel();

	const GIF_KEYS = ['edit.gif_format'];
	const SIZE_KEYS = [
		'compress.target_small_mb',
		'compress.target_standard_mb',
		'compress.target_large_mb',
		'compress.target_very_large_mb'
	];

	const gifs = $derived(panel.pick(...GIF_KEYS));
	const sizes = $derived(panel.pick(...SIZE_KEYS));

	/* One sentence for the group, saying what the four sizes are for; each size's own help says
	   whose upload limit it starts at, so the rows keep theirs. */
	const lede = COPY.compressionHelp;

	onMount(() => {
		void panel.load();
	});
</script>

{#if panel.loading}
	<Skeleton lines={3} />
{:else if panel.failed}
	<Problem message={COPY.cannotLoad} />
{:else}
	<SettingGroup heading={COPY.gifs}>
		{#each gifs as entry (entry.key)}
			<SettingRow
				{entry}
				value={panel.value(entry.key)}
				onchange={(next) => panel.save(entry.key, next)}
			/>
		{/each}
	</SettingGroup>

	<SettingGroup heading={COPY.compression} help={lede}>
		{#each sizes as entry (entry.key)}
			<SettingRow
				{entry}
				value={panel.value(entry.key)}
				onchange={(next) => panel.save(entry.key, next)}
			/>
		{/each}
	</SettingGroup>
{/if}
