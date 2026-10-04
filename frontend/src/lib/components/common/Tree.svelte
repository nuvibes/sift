<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Tree',
		category: 'composition',
		role: 'a folder tree that expands, collapses and holds a pick',
		basis: 'own',
		states: ['shut', 'open', 'picked']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: bits-ui has no tree. The behaviour that matters (expand, collapse, and holding a
	   selection across a re-sort) is Sift's own and has no primitive to borrow. */
	import SharingMark from '$lib/components/common/SharingMark.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { counted } from '$lib/entity/entity-counts';
	import { SvelteSet } from 'svelte/reactivity';
	import type { Snippet } from 'svelte';
	import ContextMenu from './ContextMenu.svelte';
	import MenuButton from './MenuButton.svelte';
	import { INSIDE_CONTROL } from './DataRow.svelte';
	import { flattenTree, type TreeNode } from './tree';
	import type { Selection } from './selection.svelte';

	interface Props {
		nodes: readonly TreeNode[];
		/** The folder last chosen: the one whose menu is open, or was. */
		selectedId?: string;
		/** Names the tree for a screen reader. */
		label: string;
		onselect?: (id: string) => void;
		/**
		 * The menu for one row, given its id and its name.
		 *
		 * Optional. It is how a folder gets shared: a folder is a grantable object like a file, and
		 * this is the place in the app to say so. Given as a snippet per row so this stays a tree
		 * that knows nothing about
		 * what can be done to a folder.
		 */
		rowMenu?: Snippet<[string, string]>;
		/**
		 * Somebody pressed a row's sharing mark, given the folder's id and its name.
		 *
		 * Optional, and the mark is drawn either way: a tree with nowhere to send this draws the
		 * mark as plain text rather than as a button that says click and does nothing.
		 */
		onsharing?: (id: string, label: string) => void;
		/**
		 * Picking several folders at once.
		 *
		 * Every wall of tiles and every wall of entities can select, and the folders are the one
		 * place where "share all of these" is the obvious thing to want.
		 *
		 * Given a `Selection` it grows the same three gestures everything else has, resolved
		 * against the rows as they are drawn, so a shift-run follows what is on screen rather than
		 * some other order. Left out, the tree picks one row at a time.
		 */
		selection?: Selection;
	}

	let {
		selectedId = $bindable(),
		nodes,
		label,
		onselect,
		rowMenu,
		onsharing,
		selection
	}: Props = $props();

	let expanded = $state(new SvelteSet<string>());

	const rows = $derived(flattenTree(nodes, expanded));

	// Which row the tab key lands on. A tree is one stop, not one stop per folder: tabbing through a
	// library with four hundred folders to reach whatever is after it is not navigation.
	//
	// Checked against what is on screen rather than remembered, because the remembered row can stop
	// being a row: close a folder and everything inside it is gone. Trusting the memory then leaves
	// the stop on a row that does not exist, no row carries it, and the keyboard cannot reach the
	// tree at all, while the folders sit there looking fine and the mouse still works.
	let focusedId = $state<string | null>(null);
	const focused = $derived(
		rows.some((row) => row.node.id === focusedId) ? focusedId : (rows[0]?.node.id ?? null)
	);

	function toggle(id: string) {
		if (expanded.has(id)) expanded.delete(id);
		else expanded.add(id);
	}

	/*
	 * Which row's menu the dots have open: one at a time, by id, so the rows share one piece of
	 * state.
	 *
	 * A plain press on a row opens the folder out, the same as the chevron and Right, because that
	 * is the gesture everybody reaches for first; the menu keeps the right button and the dots.
	 */
	let menuFor = $state<string | null>(null);

	function select(id: string) {
		selectedId = id;
		focusedId = id;
		// Looked up rather than passed in: `select` is reached from the press, from the sheet over
		// the row and from Enter, and only one of those three has the row in its hand. A folder with
		// nothing inside it is still picked; there is simply nothing to open.
		if (rows.find((row) => row.node.id === id)?.hasChildren) toggle(id);
		onselect?.(id);
	}

	/*
	 * A click on a row, with the picking rules applied first.
	 *
	 * The same three the rest of the application uses (plain click, ctrl-click adds one,
	 * shift-click takes the run) and the same rule about when they apply: once something is
	 * picked, a plain click adds and removes, because something being selected IS the mode. With
	 * nothing picked and no modifier held, a click still just opens the folder, which is what every
	 * click in this tree has always done.
	 *
	 * Resolved against `rows`, which is what is on screen with the closed folders left out. A run
	 * from a row to another one three levels down takes exactly what a hand would draw over.
	 */
	function clicked(id: string, event: MouseEvent) {
		if (!selection) {
			select(id);
			return;
		}
		const picking = selection.count > 0 || event.ctrlKey || event.metaKey || event.shiftKey;
		if (!picking) {
			select(id);
			return;
		}
		event.preventDefault();
		event.stopPropagation();
		focusedId = id;
		selection.pick(
			id,
			event,
			rows.map((row) => row.node.id)
		);
	}

	function move(to: number) {
		const next = rows[to];
		if (!next) return;
		focusedId = next.node.id;
		document.getElementById(`tree-${next.node.id}`)?.focus();
	}

	function onkeydown(event: KeyboardEvent) {
		const index = rows.findIndex((row) => row.node.id === focused);
		if (index === -1) return;
		const row = rows[index];

		switch (event.key) {
			case 'ArrowDown':
				move(index + 1);
				break;
			case 'ArrowUp':
				move(index - 1);
				break;
			// Right opens a closed folder and steps into an open one; left closes an open folder and
			// steps out of a closed one. That pairing is what makes a tree navigable without a mouse.
			case 'ArrowRight':
				if (row.hasChildren && !row.expanded) toggle(row.node.id);
				else if (row.expanded) move(index + 1);
				break;
			case 'ArrowLeft':
				if (row.expanded) toggle(row.node.id);
				else {
					const parent = rows.findLastIndex((r, i) => i < index && r.level < row.level);
					if (parent !== -1) move(parent);
				}
				break;
			case 'Enter':
			case ' ':
				select(row.node.id);
				break;
			default:
				return;
		}
		event.preventDefault();
	}
</script>

<ul class="tree" role="tree" aria-label={label} {onkeydown}>
	{#each rows as row (row.node.id)}
		{@const id = row.node.id}
		<!--
			The treeitem is this element and nothing inside it. Focus, the role, the level and the
			handlers have to be on one element: a role on a wrapper with the focus on a child means a
			screen reader announces one thing and the keyboard is somewhere else.

			The keyboard is handled once, by the tree, because the arrow keys are about moving between
			rows rather than doing anything to one, so it is the list that knows what is next.

			Which is also why the check below is waved off rather than answered: it wants the key
			handler on the same element as the click, and for a tree that is the wrong place for it.
			Enter and Space do select a row, from the handler on the tree.
		-->
		<!-- svelte-ignore a11y_click_events_have_key_events -->
		<li
			id="tree-{id}"
			role="treeitem"
			class="row"
			class:selected={selectedId === id}
			class:picked={selection?.has(id) ?? false}
			aria-level={row.level + 1}
			aria-selected={selectedId === id}
			aria-expanded={row.hasChildren ? row.expanded : undefined}
			tabindex={focused === id ? 0 : -1}
			onclick={(event) => {
				/* A press on a control inside the row (the chevron, the dots, the sharing mark)
				   is that control's; the menu-zone sheet forwards its own. */
				if ((event.target as Element | null)?.closest(INSIDE_CONTROL)) return;
				clicked(id, event);
			}}
			onfocus={() => (focusedId = id)}
		>
			{#each Array.from({ length: row.level }) as _, level (level)}
				<span class="guide"></span>
			{/each}

			{#if row.hasChildren}
				<button
					class="chevron"
					class:open={row.expanded}
					tabindex="-1"
					aria-label={row.expanded ? 'Collapse' : 'Expand'}
					onclick={(event) => {
						event.stopPropagation();
						toggle(id);
					}}
				>
					<Icon name="chevron_right" size={16} />
				</button>
			{:else}
				<span class="chevron"></span>
			{/if}

			<Icon name="folder" size={16} filled={selectedId === id} />
			<span class="label">{row.node.label}</span>
			{#if rowMenu}
				<!--
					An invisible sheet over the row, so a right-click anywhere on it opens the menu.

					Over rather than around: the treeitem has to stay the single element carrying the
					role, the focus and the arrow keys, and a wrapper between the tree and its items
					breaks the structure a screen reader walks. The sheet forwards a plain click to the
					same select the row does, so it is only ever in the way of the right button. The
					chevron is lifted above it so it stays its own control rather than a patch of sheet.
				-->
				<ContextMenu triggerClass="row-menu" label={`Actions for ${row.node.label}`}>
					<!-- svelte-ignore a11y_click_events_have_key_events -->
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<span class="menu-zone" onclick={(event) => clicked(id, event)}></span>
					{#snippet items()}{@render rowMenu(id, row.node.label)}{/snippet}
				</ContextMenu>
				<!-- The discoverable door to the same rows, beside the right button's. One open at a
				     time, by id: opening this one names the row, and the menu closing lets go of it. -->
				<span class="dots">
					<MenuButton
						label={`Actions for ${row.node.label}`}
						open={menuFor === id}
						onOpenChange={(next) => (menuFor = next ? id : null)}
					>
						{@render rowMenu(id, row.node.label)}
					</MenuButton>
				</span>
			{/if}

			<!-- The same mark the tiles and the entity walls carry, so one glance means one thing
			     wherever it is. A folder inside a shared folder is reachable and says so.

			     Lifted out from under the right-click sheet, the same as the chevron and for the same
			     reason: the sheet covers the whole row, so anything underneath it can be looked at and
			     not pressed. Opening the sharing panel is its own action, not a way of selecting the
			     folder. -->
			{#if row.node.shared || row.node.restricted || row.node.hidden || row.node.refused}
				<span class="mark-slot">
					<SharingMark
						shared={row.node.shared}
						restricted={row.node.restricted}
						shared_here={row.node.shared_here}
						restricted_here={row.node.restricted_here}
						hidden={row.node.hidden}
						refused={row.node.refused}
						onopen={onsharing ? () => onsharing?.(id, row.node.label) : undefined}
					/>
				</span>
			{/if}

			{#if row.node.count !== undefined}
				<span class="count data">{counted(row.node.count)}</span>
			{/if}
		</li>
	{/each}
</ul>

<style>
	.tree {
		list-style: none;
		margin: 0;
		padding: 0;
	}

	/* The right-click sheet, and the two things lifted out from under it. */
	:global(.row-menu) {
		display: contents;
	}

	.menu-zone {
		position: absolute;
		inset: 0;
		border-radius: inherit;
	}

	/* The dots sit at the end of the row, above the sheet, the same place `DataRow` puts them. */
	.dots {
		position: relative;
		margin-inline-start: auto;
		display: inline-flex;
	}

	.chevron,
	.mark-slot {
		position: relative;
		z-index: 1;
	}

	.mark-slot {
		display: inline-flex;
		align-items: center;
	}

	.row {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		block-size: 32px;
		padding-inline: var(--space-2);
		/* Square-ish on purpose. The folders are the physical world (the actual files on the actual
		   disk) and they should not look like the pills that stand for tags and people. */
		border-radius: var(--radius-sm);
		color: var(--sift-ink-2);
		cursor: pointer;
		position: relative;
		transition: background-color var(--dur-instant) var(--ease);
	}

	.row:hover {
		background: var(--sift-surface-3);
	}

	.row.selected {
		background: var(--sift-accent-bg);
		color: var(--sift-ink);
	}

	/* Picked, as against being the folder currently open. The ring is what "this is what the next
	   action happens to" means everywhere else in the application, so it means it here too. */
	.row.picked {
		box-shadow: var(--focus-ring);
	}

	.row.selected::before {
		content: '';
		position: absolute;
		left: 0;
		top: 50%;
		translate: 0 -50%;
		inline-size: 3px;
		block-size: 16px;
		border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
		background: var(--sift-accent);
	}

	.row:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	/* One per level of depth, each carrying the line that shows which parent a row hangs from. */
	.guide {
		inline-size: 16px;
		block-size: 100%;
		flex: none;
		border-inline-start: 1px solid var(--sift-line);
	}

	.chevron {
		display: flex;
		align-items: center;
		justify-content: center;
		inline-size: 16px;
		block-size: 16px;
		flex: none;
		border: 0;
		padding: 0;
		background: none;
		color: inherit;
		cursor: pointer;
		transition: rotate var(--dur-fast) var(--ease);
	}

	.chevron.open {
		rotate: 90deg;
	}

	.label {
		flex: 1;
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.count {
		flex: none;
		color: var(--sift-ink-3);
		font-variant-numeric: tabular-nums;
	}
</style>
