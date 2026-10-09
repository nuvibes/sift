<script lang="ts">
	/* Settings > Downloads: what is set ONCE about downloading: what gets downloaded, the
	 * limits, the sounds, where downloads go and what they are called, and More settings, in
	 * that order (`Downloads.svelte` draws all of it). */
	import DownloadSettings from './Downloads.svelte';
</script>

<!-- One folder chooser on the pane, the "Default downloads folder" row `NamingTemplate` draws. -->
<DownloadSettings />

<!-- No `<style>` block at all. The frame draws every section's title (`SettingsTitle`), so a pane
     writing its own heading is writing a copy it can get wrong. -->
