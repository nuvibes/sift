<script lang="ts">
	/*
	 * A harness for the picker drawn as a menu's whole content rather than as a row that opens out.
	 *
	 * It is the shape a person's or site's header wears (a Tag button opening straight onto the
	 * list), and it is the only arrangement where filtering can be tested: a `ContextMenu` submenu
	 * inside `RowMenu` closes itself ten to twenty milliseconds after opening in jsdom, while the
	 * box waits out a 120 ms pause before it asks. A menu with no submenu has no such timer.
	 *
	 * A snippet cannot be handed to `mount` from a test file, so this is a component, the same
	 * arrangement `ContextMenuItemProbe` uses.
	 */
	import MenuButton from './MenuButton.svelte';
	import PickMenu from './PickMenu.svelte';
	import type { PickAsk, PickChoice } from './verbs';

	interface Props {
		ask: PickAsk;
		onpick: (choice: PickChoice) => void;
		oncreate?: (name: string) => Promise<PickChoice | null>;
		/** Which end the box sits at. The faces screen's bar opens upwards and asks for 'bottom'. */
		filterAt?: 'top' | 'bottom';
	}

	let { ask, onpick, oncreate, filterAt = 'top' }: Props = $props();
</script>

<MenuButton label="Add a tag" words="Tag" scrolls={false}>
	<PickMenu
		label="Tag"
		icon="shoppingmode"
		kind="tag"
		plural="tags"
		inline
		{filterAt}
		{ask}
		{onpick}
		{oncreate}
	/>
</MenuButton>
