<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'ContextMenuGroup',
		category: 'surface',
		role: 'a run of menu rows that belong together, fenced from the run before it by a line',
		basis: 'bits-ui:ContextMenu',
		states: ['the first group', 'a later group', 'with its heading']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/*
	 * Rows that belong together, with the line between this run and the one before it.
	 *
	 * A menu is read in parts (go there, file it, keep a copy, change it, who sees it, and the one
	 * that destroys something), and the line is what makes the parts visible. It is drawn HERE and
	 * nowhere else, so no menu places a line by hand and no line can land inside a group.
	 *
	 * The line leads the group, and two rules take it away where it would separate nothing: the
	 * first group in a list has nothing above it, and a group whose rows are all conditional and
	 * all absent has nothing below it. Both are decided by the stylesheet from what is actually on
	 * screen, so a caller never has to work out which of its groups comes first.
	 *
	 * The library's group underneath, so a screen reader hears the parts as parts: `role="group"`
	 * around the rows, which the line alone would not say.
	 *
	 * Where the rows are the same kind of thing as the group after them (a task's parts, then the
	 * library folders), a line alone does not say where one list ends, so `heading` draws the label
	 * over the rows: the caption size in the quiet ink, not a row, never pressed. The heading then
	 * names the group for a screen reader too.
	 */
	import type { Snippet } from 'svelte';
	import { ContextMenu } from 'bits-ui';
	import ContextMenuSeparator from './ContextMenuSeparator.svelte';

	interface Props {
		/** The rows. `ContextMenuItem`s, or a `VerbMenuItems` drawing one group. */
		children: Snippet;
		/** What the rows have in common, for a screen reader. Optional: the rows usually say it. */
		label?: string;
		/** Draw `label` as a heading over the rows, for a group the line alone cannot tell apart. */
		heading?: boolean;
	}

	let { children, label, heading = false }: Props = $props();
	const drawn = $derived(heading && Boolean(label));
</script>

<ContextMenu.Group class="menu-group" aria-label={drawn ? undefined : label}>
	<ContextMenuSeparator />
	{#if drawn}
		<ContextMenu.GroupHeading class="menu-heading">{label}</ContextMenu.GroupHeading>
	{/if}
	{@render children()}
</ContextMenu.Group>

<style>
	/*
	 * Portalled with the menu, so the rules have to be global to reach it; each one begins with the
	 * class this file writes.
	 *
	 * No line in front of the first group, which has nothing above it to be separated from, and
	 * none in a group with no rows, which would otherwise stack its line on the next group's.
	 */
	:global(.menu-group:first-child > .menu-separator),
	:global(.menu-group > .menu-separator:only-child) {
		display: none;
	}

	/* A heading over no rows would name an empty list, so a group whose rows are all absent draws
	   nothing at all. */
	:global(.menu-group:has(> .menu-heading):not(:has([role^='menuitem']))) {
		display: none;
	}

	/* Starts where the rows' words start, in the caption size and the quiet ink, so it reads as the
	   name of the list under it and never as one more row to press. */
	:global(.menu-heading) {
		padding: var(--space-2) var(--space-2) var(--space-1);
		font: var(--text-label);
		color: var(--sift-ink-3);
		cursor: default;
		user-select: none;
	}
</style>
