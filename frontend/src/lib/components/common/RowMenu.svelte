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
	   left here is the rows, and they come from the shared verb declaration rather than from
	   markup. */

	/*
	 * The three dots at the end of a row, opening the row's own verbs: the same menu as the
	 * right-click, by another door, with the verbs' icons and the separator before the destructive
	 * one, off one declaration.
	 *
	 * If you draw this, draw the right-click too, off the same list. The three dots are the
	 * discoverable way into a row's verbs and the right-click the fast one; a surface with this and
	 * no context menu is one where the gesture everybody tries first does nothing. `DataRow`
	 * guarantees both, and is the right door for anything that is a row.
	 *
	 * Anything that is not a row (a saved filter's pill) has to say so itself: wrap it in
	 * `ContextMenu` and render the same declared verbs into both through `VerbMenuItems`. One list,
	 * two ways in.
	 *
	 * They are literally the same components. In bits-ui 2.18, `DropdownMenu.Item`, `.Sub`,
	 * `.SubTrigger`, `.SubContent` and `.Separator` are re-exported from the same `menu/` internals
	 * `ContextMenu` re-exports; only `Root`, `Content` and `Trigger` differ, which is how the menu
	 * is opened. So the shared `VerbMenuItems` renders unchanged inside this, and a verb reaches
	 * the right-click menu, the selection bar and this door from one declaration.
	 */
	import type { Snippet } from 'svelte';
	import MenuButton from '$lib/components/common/MenuButton.svelte';
	import VerbMenuItems from '$lib/components/common/VerbMenuItems.svelte';
	import type { Verb } from '$lib/components/common/verbs';

	interface Props {
		/** The row's verbs, declared once and drawn here and wherever else the row offers them. */
		verbs: readonly Verb[];
		/** What they act on. One row's id, nearly always. */
		ids: string[];
		/**
		 * The accessible name of the button, which has no words on it. Says whose row it belongs to:
		 * "More for alice". `MenuButton` says why.
		 */
		label: string;
		/** Nothing can be done just now: a request is in flight, or the row is being rebuilt. */
		disabled?: boolean;
		/** Words on the button, instead of the three dots. Handed straight to the door, which owns
		 *  the rule about when each shape is right. */
		words?: string;
		/**
		 * Rows that are not shared verbs, drawn under the ones that are.
		 *
		 * Deliberately narrow, and it is not a way round the declaration. A file verb is offered by
		 * every surface showing files, so it is declared once and rendered by `VerbMenuItems`; what
		 * goes here is the opposite: something only ONE surface can offer, because only that
		 * surface has asked the question it depends on. Renaming a file is the case it exists for:
		 * whether it may happen at all is a per-FILE answer from the server, and a wall of forty
		 * tiles cannot ask it forty times to decide whether to draw a row.
		 *
		 * If what you are about to put here would make sense on a second surface, it is a verb and
		 * belongs in `$lib/grid/verbs` with the rest of them.
		 */
		extra?: Snippet;
		/** Whether the menu is open. See `MenuButton.open`. For a row that opens it on a press. */
		open?: boolean;
		/**
		 * Where the open menu is drawn. See `MenuButton.portalTo`, which owns the whole of why.
		 *
		 * Handed straight down rather than answered here: this file knows what the rows are and the
		 * door knows where it lands, and a row that is drawn inside a box filling the screen is the
		 * door's problem in every one of the four places one of these is drawn.
		 */
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
