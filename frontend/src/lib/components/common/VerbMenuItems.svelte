<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'VerbMenuItems',
		category: 'composition',
		role: 'a declared list of verbs drawn as menu rows',
		basis: 'composes:ContextMenuItem',
		states: ['default']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* DRESSED BY: .item, .ui-menu. ContextMenuItem and ContextMenu style the rows and the surface
	   this file hands them. A menu row is a snippet written HERE and rendered inside the shared
	   menu, so its scope is this file's and the shared menu's own rules cannot be scoped; they are
	   `:global` there, which is the only mechanism that reaches a caller's snippet. */
	/* WHY NOT BITS-UI: there is no primitive left here for it to reach. Every row this draws is a
	   ContextMenuItem, every group is the same row opening out, and the rating is RatingChip and
	   RatingChoices, all of which are the library one level down. What is left is a loop over a
	   declared list. */

	/*
	 * The same declared list of verbs, drawn as the rows of a right-click menu.
	 *
	 * The sibling of `VerbButtons`, and deliberately as thin: between them they are the only two
	 * places a verb becomes markup, so the bar and the menu render the same list or neither does.
	 *
	 * The lines are the declaration's, never placed here: each verb names its group, `menuGroups`
	 * puts the groups in their one order (and anything destructive last, alone), and every group
	 * is drawn through `ContextMenuGroup`, which owns the line in front of it.
	 */
	import type { Snippet } from 'svelte';
	import RatingChoices from '$lib/components/common/RatingChoices.svelte';
	import RatingChip from '$lib/components/common/RatingChip.svelte';
	import ContextMenuItem from '$lib/components/common/ContextMenuItem.svelte';
	import PickMenu from '$lib/components/common/PickMenu.svelte';
	import ContextMenuGroup from '$lib/components/common/ContextMenuGroup.svelte';
	import { menuGroups, type OnAlready, type Verb } from '$lib/components/common/verbs';

	interface Props {
		verbs: readonly Verb[];
		/** What every row here acts on, resolved once by the screen. */
		ids: string[];
		/**
		 * The ONE thing this menu was opened on.
		 *
		 * A menu opened on a row that is part of a selection acts on the whole selection, but the
		 * two verbs that only make sense pointed at one thing do not: opening acts on the row under
		 * the pointer, not on whichever of forty happens to be first, and copying a link copies that
		 * row's. Without this they would silently address the wrong one, which is the sort of wrong
		 * that looks like the menu working.
		 *
		 * Absent leaves them acting on `ids`, which is right for a menu that is only ever opened on
		 * one thing anyway.
		 */
		subjectId?: string;
		/**
		 * Rows the surface writes itself, drawn as a group of their own after the declared groups
		 * and before the one that destroys something.
		 *
		 * For the few rows a screen has that no declaration can (making this picture a person's
		 * cover, renaming a loop), so they join the menu's parts instead of trailing after its
		 * Delete. Never a destructive row: that one is declared, so it is always last.
		 */
		extra?: Snippet;
		/**
		 * `extra` draws its own parts: its rows arrive already in `ContextMenuGroup`s, one per part
		 * of the menu, because they span parts (a loop's Rename is changing it, Save as a clip keeps
		 * a copy, Tag files it). Drawn as they come, never inside a group of this file's: a group
		 * inside a group is a line inside one part.
		 */
		extraGrouped?: boolean;
	}

	let { verbs, ids, subjectId, extra, extraGrouped = false }: Props = $props();

	/** The small line under one row: its reason while it is refused, its note otherwise. */
	function noteOf(verb: Verb): { note?: string } {
		const said = verb.disabled ? (verb.why ?? verb.note) : verb.note;
		return said ? { note: said } : {};
	}

	/** What one verb acts on: the row it was opened on, or everything picked. */
	function actsOn(verb: Verb): string[] {
		return verb.singleOnly && subjectId !== undefined ? [subjectId] : ids;
	}

	const groups = $derived(menuGroups(verbs));

	/* The last group is the destructive one when it holds a destructive verb; `menuGroups` puts it
	   nowhere else. The surface's own rows go in front of it. */
	const destroying = $derived(groups.at(-1)?.[0]?.destructive ? groups.at(-1) : undefined);
	const benign = $derived(destroying ? groups.slice(0, -1) : groups);

	/* One group needs no fence and no `role="group"` around it. Drawn bare, it also sits inside a
	   caller's own group (one saved verb among a cell's rows) without a line of its own. */
	const fenced = $derived(benign.length + (extra ? 1 : 0) + (destroying ? 1 : 0) > 1);

	/**
	 * What the files this verb acts on are already on.
	 *
	 * Bound to the verb's own set HERE rather than in the picker, for the reason `actsOn` exists at
	 * all: which files a row is about is this file's question, and a picker that worked it out for
	 * itself would be a second answer to it, free to disagree on exactly the menu where it matters
	 * most, one opened on a tile that is part of a selection.
	 *
	 * An empty answer where the verb declares none, rather than a thrown error: `already` is passed
	 * only where the verb has one, so this is reached only where it does.
	 */
	async function askAlready(verb: Verb): Promise<Record<string, OnAlready>> {
		return (await verb.pick?.already?.(actsOn(verb))) ?? {};
	}
</script>

<!--
	ONE verb as a row, wherever it sits: at the top of the menu, or inside a row that opens out.

	A snippet rather than the same six lines written twice, and a second copy would be a real cost
	rather than an untidiness. Every one of the four picking verbs lives under "Add to", so a
	property that only the top-level copy read would be a property none of them ever got: a flyout
	added here would be invisible on the only rows it was built for.
-->
<!--
	WHAT THE SECOND LINE UNDER A ROW SAYS, which depends on whether the row can be pressed.

	A menu row has one small line under it. A disabled control has to say why, and the
	bar already does that with a tooltip: a menu has no tooltip to hang it on, and a greyed row
	with no reason under it is the silence that reads as a broken app. So on a refused row the reason
	takes the line, and on a row that can be pressed the note does: what a row cannot do is more
	urgent than when it last happened, and "Last: 16 Sep" under a row that will not run is a fact
	about the wrong question.
-->
{#snippet row(verb: Verb)}
	{#if verb.children}
		<!-- A row that holds others, at ANY depth: "Run task" opens onto its three stages and each
		     stage onto its passes, the same row opening out again. The snippet draws itself for
		     each child, so a group two levels down is drawn exactly as one at the top. -->
		<ContextMenuItem
			label={verb.label}
			icon={verb.icon}
			filled={verb.filled ?? false}
			note={verb.note}
		>
			{#each verb.children as child (child.id)}
				{@render row(child)}
			{/each}
		</ContextMenuItem>
	{:else if verb.pick}
		<!-- Answered by picking one thing out of a list, so the row opens out into the list itself
		     rather than into a sheet over the whole screen. The bar still opens the sheet: see
		     `VerbPick`, which is beside `run` and never instead of it. -->
		<PickMenu
			label={verb.label}
			icon={verb.icon}
			filled={verb.filled ?? false}
			kind={verb.pick.kind}
			plural={verb.pick.plural}
			ask={verb.pick.ask}
			onpick={(choice) => verb.pick?.pick(actsOn(verb), choice)}
			onunpick={verb.pick.unpick
				? (choice) => verb.pick?.unpick?.(actsOn(verb), choice)
				: undefined}
			already={verb.pick.already ? () => askAlready(verb) : undefined}
			oncreate={verb.pick.create}
		/>
	{:else}
		<ContextMenuItem
			label={verb.label}
			icon={verb.icon}
			filled={verb.filled ?? false}
			destructive={verb.destructive ?? false}
			disabled={verb.disabled ?? false}
			{...noteOf(verb)}
			onselect={() => verb.run?.(actsOn(verb))}
		/>
	{/if}
{/snippet}

<!-- One verb at the top of the menu: a group opening out, the rating, or a plain row. -->
{#snippet top(verb: Verb)}
	{#if verb.children}
		<!-- A row that holds others, which is the menu row opening out to the side, drawn by the
		     same snippet as every other row, so a group inside a group opens the same way. -->
		{@render row(verb)}
	{:else if verb.stars && verb.flyout}
		<!-- The five answers out to the side, where a menu has room. The same row-that-holds-rows
		     the grouped verbs above use: a rating is one row that opens out, and so are they.
		     `fit`, because these rows are a glyph and a digit: a menu's width floor would draw the
		     flyout at nearly twice the width of anything in it. -->
		<ContextMenuItem label={verb.label} icon={verb.icon} filled={verb.rating !== null} fit>
			<RatingChoices
				menu
				rating={verb.rating ?? null}
				onpick={(value) => verb.rate?.(actsOn(verb), value)}
			/>
		</ContextMenuItem>
	{:else if verb.stars}
		<!-- Not a menu row: a row of stars inside one. It sets a value rather than doing a thing, so
		     it must not be something the arrow keys land on and Enter chooses. The menu stays open
		     after a star is picked, which is what lets somebody correct a mis-click without
		     right-clicking again. -->
		<div class="rating" role="group" aria-label={verb.label}>
			<span class="what">{verb.label}</span>
			<RatingChip
				rating={verb.rating ?? null}
				onchange={(value) => verb.rate?.(actsOn(verb), value)}
				label={verb.label}
			/>
		</div>
	{:else}
		{@render row(verb)}
	{/if}
{/snippet}

<!-- One run of rows: fenced by its line where the menu has more than one run, bare where not. -->
{#snippet run(rows: readonly Verb[])}
	{#if fenced}
		<ContextMenuGroup>
			{#each rows as verb (verb.id)}
				{@render top(verb)}
			{/each}
		</ContextMenuGroup>
	{:else}
		{#each rows as verb (verb.id)}
			{@render top(verb)}
		{/each}
	{/if}
{/snippet}

{#each benign as rows (rows[0].id)}
	{@render run(rows)}
{/each}
{#if extra}
	{#if extraGrouped}
		{@render extra()}
	{:else if fenced}
		<ContextMenuGroup>{@render extra()}</ContextMenuGroup>
	{:else}
		{@render extra()}
	{/if}
{/if}
{#if destroying}
	{@render run(destroying)}
{/if}

<style>
	.rating {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-3);
		padding: var(--space-2) var(--space-3);
	}

	.what {
		font: var(--text-body);
		color: var(--sift-ink);
	}
</style>
