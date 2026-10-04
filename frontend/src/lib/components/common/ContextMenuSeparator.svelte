<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'ContextMenuSeparator',
		category: 'surface',
		role: 'the line between groups of menu rows',
		basis: 'bits-ui:ContextMenu',
		states: ['default']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/*
	 * A hairline between groups of menu items.
	 *
	 * NOT ON THE GALLERY: it is drawn at the head of every ContextMenuGroup, which the gallery's menus
	 * draw; a menu never places it by itself (check_menu_groups.js).
	 *
	 * It earns its place once a menu holds more than one kind of action: the benign ones you do
	 * often, and the destructive one you do rarely and want a beat of separation before. Grouping
	 * them with nothing between is how the dangerous item ends up next to the harmless one it looks
	 * like, which is the exact mistake `destructive` colouring exists to prevent and a line finishes.
	 *
	 * Drawn by `ContextMenuGroup` at the head of every group, never placed by a menu itself:
	 * `check_menu_groups.js` refuses one written anywhere outside the primitives.
	 */
	import { ContextMenu } from 'bits-ui';
</script>

<ContextMenu.Separator class="menu-separator" />

<style>
	/* Portalled with the menu, so the rule has to be global to reach it. */
	:global(.menu-separator) {
		block-size: 1px;
		margin: var(--space-1) var(--space-2);
		background: var(--sift-line);
	}
</style>
