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
	/* Rows that belong together, with the line before them drawn here alone; the stylesheet drops
	 * it where it separates nothing. `heading` names a group a line cannot tell apart. */
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
	/* Global, as the menu is portalled. No line before the first group or in an empty one. */
	:global(.menu-group:first-child > .menu-separator),
	:global(.menu-group > .menu-separator:only-child) {
		display: none;
	}

	/* A heading over no rows draws nothing. */
	:global(.menu-group:has(> .menu-heading):not(:has([role^='menuitem']))) {
		display: none;
	}

	/* Starts with the rows' words, quiet, so it never reads as a row. */
	:global(.menu-heading) {
		padding: var(--space-2) var(--space-2) var(--space-1);
		font: var(--text-label);
		color: var(--sift-ink-3);
		cursor: default;
		user-select: none;
	}
</style>
