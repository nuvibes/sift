<script lang="ts">
	/* Every key the application answers to, grouped by where it works. */
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import KeyRows from '$lib/components/KeyRows.svelte';
	import { shortcutsByArea } from '$lib/shell/shortcuts';
	import SettingGroup from './SettingGroup.svelte';

	const AREAS = shortcutsByArea();
	/* Every key on the page, so each group's list sizes its key column by the widest of all of
	   them and the sentences start on one line down the whole page. */
	const KEYS = AREAS.flatMap((group) => group.shortcuts.map((one) => one.shown));
</script>

<header>
	<!-- The title is the frame's; the search result for the keys lands on this sentence. -->
	<p class="lede" id="shortcuts.keys">
		What each key does, and where. A shortcut only answers on the screen it belongs to, and none
		works while you type in a box.
	</p>
</header>

{#each AREAS.filter((group) => !(phoneWidth.yes && group.area === 'Theater')) as group (group.area)}
	<!-- Theater is cut on a phone, so its keys are not listed there. -->
	<SettingGroup heading={group.area}>
		<KeyRows shortcuts={group.shortcuts} widest={KEYS} />
	</SettingGroup>
{/each}

<style>
	/* The headings are `app.css`'s, like every other pane's. */
	header {
		margin-block-end: var(--space-6);
	}

	.lede {
		margin: var(--space-2) 0 0;
	}
</style>
