<script lang="ts">
	/*
	 * `Tasks and Activity > Logs`: what Sift writes down, how much of it, and the end of both logs.
	 *
	 * ## Why it is a tab of Tasks and Activity
	 *
	 * It is one of three answers to "what has this thing been doing" (Activity says what it is
	 * doing now, History what happened to the library, and this what the program wrote down while
	 * doing it), and three places for one question would be the fault, so the three are one
	 * screen's three tabs. Each still reads its own store; they are side by side rather than merged
	 * because they answer different questions.
	 *
	 * ## The controls come before the reader
	 *
	 * They decide what is in it. Somebody who has turned the detail up wants the next thing that
	 * happens recorded more fully, and reading the file first and finding the controls under it is
	 * the wrong way round. The download switch and the hiding are here because what they change is
	 * what this log records.
	 */
	import { onMount } from 'svelte';
	import { tellShell } from '$lib/desktop/shell-log-detail';
	import SettingGroup from './SettingGroup.svelte';
	import SettingsList, { type SettingBlock } from './SettingsList.svelte';
	import ApplicationLog from './ApplicationLog.svelte';
	import { SettingsPanel } from './panel.svelte';
	import { COPY } from './Logs.search';

	/* Named individually rather than taken as a whole section. `test_every_setting_reaches_a_screen`
	   allows either; naming them says which pane owns them, and a setting added to the group later
	   has to be placed deliberately rather than appearing here unread. */
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

<!-- One group carrying its rows: the heading and its sentence over the settings they introduce. A
     heading-only group followed by an unheaded one continues it, and the unheaded one would draw
     itself up over the sentence. Inside, the rows keep their own help and the heading keeps its anchor. -->
<SettingGroup id="activity.log" heading={COPY.heading} help={COPY.help}>
	<SettingsList {panel} blocks={LOG_SETTINGS} />
</SettingGroup>

<ApplicationLog />

<!-- No title and no `<style>` block. The frame draws the section's title, and the tab strip above
     this says which of the section's tabs is showing. -->
