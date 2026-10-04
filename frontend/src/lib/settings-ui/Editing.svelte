<script lang="ts">
	/* What the editor produces, and how big a smaller copy is made.
	 *
	 * ## Two headings, one section
	 *
	 * Nobody hunting for which format a GIF is saved in starts under a heading about
	 * making files smaller, so the two questions are two HEADINGS. They are not two sections:
	 * two doors for two questions somebody asks in the same breath is two places to look for one
	 * answer.
	 *
	 * The keys are named rather than taken from the section as it arrives: the response is sorted
	 * by key, which puts Large above Small and the format in the middle of the sizes. Correct for
	 * a machine, meaningless as a page.
	 *
	 * The two confirmations are on General: they are about the whole app, not about editing.
	 */
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
	   whose upload limit it starts at, so the rows keep theirs.
	 *
	 * NOT a `PresetGroup` even though it is four settings under one heading. That primitive needs a
	 * master whose value is a READING of the children; a master over four independent numbers would
	 * have to be three invented bundles of megabytes, which is a product decision dressed as a
	 * layout one. Four named rows under one sentence is already the right shape. */
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
