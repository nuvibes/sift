<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'RowMenu',
		category: 'surface',
		role: 'the three-dot door on a row, and the rows behind it',
		basis: 'composes:MenuButton',
		states: ['closed', 'open']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* DRESSED BY: .ui-menu, button.more (MenuButton is the door (the trigger, the portal and the
	surface) and dresses all three; this file is only what goes inside one) */
	/* WHY NOT BITS-UI: the door is bits-ui's DropdownMenu, one level down in `MenuButton`. What is
	left here is the rows. */

	import type { Snippet } from 'svelte';
	import MenuButton from '$lib/components/common/MenuButton.svelte';
	import VerbMenuItems from '$lib/components/common/VerbMenuItems.svelte';
	import type { Verb } from '$lib/components/common/verbs';

	interface Props {
		/** The row's verbs, declared once and drawn here and wherever else the row offers them. */
		verbs: readonly Verb[];
		ids: string[];
		/** The button's accessible name, saying whose row: "More for alice". */
		label: string;
		/** Nothing can be done just now: a request is in flight, or the row is being rebuilt. */
		disabled?: boolean;
		words?: string;
		/**
		 * Rows only this surface can offer (rename); anything wider is a verb in `$lib/grid/verbs`.
		 */
		extra?: Snippet;
		/** See `MenuButton.open`. */
		open?: boolean;
		/** Where the open menu is drawn. See `MenuButton.portalTo`. */
		portalTo?: Element | null;
	}

	let {
		verbs,
		ids,
		label,
		disabled = false,
		words,
		extra,
		open = $bindable(false),
		portalTo = null
	}: Props = $props();
</script>

<MenuButton {label} {words} {disabled} {portalTo} bind:open>
	<VerbMenuItems {verbs} {ids} />
	{@render extra?.()}
</MenuButton>
