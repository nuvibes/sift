<script lang="ts">
	/*
	 * Every key the application answers to, grouped by where it works.
	 *
	 * ## Why it is generated and not written
	 *
	 * A written list does not change when the thing it describes does: a hand-typed sheet beside
	 * the code that reads the keys will say the numbers go "1 to 4" while the code takes 1 to 9.
	 * Every row here comes out of the same declarations the key handlers match against, so a
	 * shortcut that is added, moved or removed changes this page and cannot fail to.
	 *
	 * `shortcutsByArea` leaves out an area with nothing in it, so there is no heading over an empty
	 * list. This file does not know a key exists.
	 *
	 * ## Read-only, and that is the whole of it today
	 *
	 * Nothing here is a preference: no shortcut can be changed. It is a section rather than a page
	 * because it is a thing you look up while using the app, and Settings is where people look for
	 * a list of what something can do. When a binding does become changeable, the row is where the
	 * control goes, and the grouping and the order are already right.
	 */
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
	/* The headings are `app.css`'s, like every other pane's. Only the room under the opening
	   sentence is this file's, and it is the same step the other panes leave. */
	header {
		margin-block-end: var(--space-6);
	}

	.lede {
		margin: var(--space-2) 0 0;
	}
</style>
