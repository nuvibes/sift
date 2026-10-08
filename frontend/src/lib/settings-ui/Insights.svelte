<script lang="ts">
	/*
	 * Insights: which recaps Sift creates, a switch for each period, and the way to the two
	 * settings Insights leans on that live elsewhere (the history it counts from, and the folder a
	 * saved card goes to).
	 *
	 * The switches are registry rows, drawn from the server's declarations of them.
	 */
	import { onMount } from 'svelte';
	import { LabelledRow, SettingLink } from '$lib/components/common';
	import SettingGroup from './SettingGroup.svelte';
	import SettingRow from './SettingRow.svelte';
	import { SettingsPanel } from './panel.svelte';
	import { COPY, RECAP_KEYS } from './Insights.search';

	const panel = new SettingsPanel();
	onMount(() => void panel.load());
</script>

<p class="lede">{COPY.lede}</p>

<SettingGroup heading={COPY.recaps.heading}>
	{#each panel.pick(...RECAP_KEYS) as entry (entry.key)}
		<SettingRow
			{entry}
			value={panel.value(entry.key)}
			onchange={(next: unknown) => void panel.save(entry.key, next)}
		/>
	{/each}
	<LabelledRow id="insights.history" label={COPY.history.label} help={COPY.history.help}>
		<SettingLink section="privacy" setting="privacy.your_history">{COPY.history.link}</SettingLink>
	</LabelledRow>
	<LabelledRow id="insights.pictures" label={COPY.pictures.label} help={COPY.pictures.help}>
		<SettingLink section="playback" setting="playback.screenshot_folder"
			>{COPY.pictures.link}</SettingLink
		>
	</LabelledRow>
</SettingGroup>

<style>
	.lede {
		margin: 0 0 var(--space-6);
	}
</style>
