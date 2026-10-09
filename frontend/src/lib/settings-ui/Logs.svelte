<script lang="ts">
	/* `Tasks and Activity > Logs`: what Sift writes down, how much of it, and the end of both
	 * logs. */
	import { onMount } from 'svelte';
	import { tellShell } from '$lib/desktop/shell-log-detail';
	import SettingGroup from './SettingGroup.svelte';
	import SettingsList, { type SettingBlock } from './SettingsList.svelte';
	import ApplicationLog from './ApplicationLog.svelte';
	import { SettingsPanel } from './panel.svelte';
	import { COPY } from './Logs.search';

	/* Named individually rather than taken as a whole section. */
	const LOG_SETTINGS: SettingBlock[] = [
		{ keys: ['logs.detail', 'logs.keep_mb', 'logs.hide_personal', 'download.verbose'] }
	];

	const panel = new SettingsPanel();

	// On mount, like every other pane: the panel registers a watcher during setup and the read is a
	// request, which does not belong in the body that runs while the component is being built.
	onMount(() => {
		void panel.load();
	});

	/* The shell's own log follows the library's Detail and `Hide personal details in the log`,
	   told what the pane reads, once per pair of answers (`tellShell`). */
	$effect(() => {
		tellShell(panel.value('logs.detail'), panel.value('logs.hide_personal'));
	});
</script>

<!-- One group carrying its rows: the heading and its sentence over the settings they introduce. -->
<SettingGroup id="activity.log" heading={COPY.heading} help={COPY.help}>
	<SettingsList {panel} blocks={LOG_SETTINGS} />
</SettingGroup>

<ApplicationLog />

<!-- No title and no `<style>` block. The frame draws the section's title, and the tab strip above
     this says which of the section's tabs is showing. -->
