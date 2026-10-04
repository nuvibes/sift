<script lang="ts">
	/* Settings > Downloads: what is set ONCE about downloading: what gets downloaded, the
	 * limits, the sounds, where downloads go and what they are called, and More settings, in that
	 * order (`Downloads.svelte` draws all of it).
	 *
	 * The rule that shapes it: the Downloads page decides for THIS download, and this pane
	 * decides the default for EVERY download. So nothing here is about one paste, and nothing
	 * here is about anything but downloading.
	 *
	 * Its own section rather than a heading inside Sites: this one is about Sift's own behaviour,
	 * and that one is about the Sites: which tunnel each takes, whose cookies Sift holds, what
	 * each is known to support.
	 *
	 * WHAT IS NOT HERE, AND WHERE IT IS. Each lives where its question is asked:
	 *
	 *   - The tunnels: Settings, Sites is the full table, and the Downloads page keeps a one-line
	 *     glance under the paste box.
	 *   - The Sites Sift knows: on Sites, beside the tunnels and cookies of the same Sites.
	 *   - Where a file saved OUT of Sift goes: on Folders. It is a folder on this device, not a
	 *     library folder, and nothing is imported from it.
	 *   - The download tools and the yt-dlp check: on Updates, versions of what Sift runs.
	 *   - Detailed download log: on the log's own tab. It changes what the log records.
	 *
	 * Every old address still lands: `/settings/downloads` is this pane, and rows named at an
	 * older address are followed to their sections by the settings frame.
	 */
	import DownloadSettings from './Downloads.svelte';
</script>

<!-- One folder chooser on the pane, the "Default downloads folder" row `NamingTemplate` draws. A
     second would write the same default row of `/site-options`, and because each writes the whole
     row, whichever was touched last would decide what the other two answers were. -->
<DownloadSettings />

<!-- No `<style>` block at all. The frame draws every section's title (`SettingsTitle`), so a pane
     writing its own heading is writing a copy it can get wrong. -->
