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
	this file hands them; their rules are `:global`, which reaches a caller's snippet. */
	/* WHY NOT BITS-UI: there is no primitive left here for it to reach. Every row this draws is a
	ContextMenuItem, RatingChip or RatingChoices: the library one level down. */

	/* The declared verbs as right-click rows, VerbButtons' sibling; the groups and lines are the
	 * declaration's (`menuGroups`, `ContextMenuGroup`). */
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
		 * The one thing the menu was opened on, for the verbs that mean one thing (Open, Copy
		 * link).
		 */
		subjectId?: string;
		/** The surface's own rows, as a group before the destructive one. */
		extra?: Snippet;
		/** `extra` arrives already in its own groups, drawn as they come. */
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

	/* The destructive group is last, if any; the surface's rows go before it. */
	const destroying = $derived(groups.at(-1)?.[0]?.destructive ? groups.at(-1) : undefined);
	const benign = $derived(destroying ? groups.slice(0, -1) : groups);

	/* One group needs no fence or line. */
	const fenced = $derived(benign.length + (extra ? 1 : 0) + (destroying ? 1 : 0) > 1);

	/** What this verb's own files are already on, bound here where `actsOn` is answered. */
	async function askAlready(verb: Verb): Promise<Record<string, OnAlready>> {
		return (await verb.pick?.already?.(actsOn(verb))) ?? {};
	}
</script>

<!-- One verb as a row at any depth, so a flyout reaches the picking verbs under "Add to". A
refused row's line is its reason, otherwise its note. -->

{#snippet row(verb: Verb)}
	{#if verb.children}
		<!-- A row that holds others, at any depth, drawn by this same snippet. -->
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
		<!-- A picking verb opens into the list itself; the bar opens the sheet. -->
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
		{@render row(verb)}
	{:else if verb.stars && verb.flyout}
		<!-- The five answers out to the side; `fit`, as the rows are a glyph and a digit. -->
		<ContextMenuItem label={verb.label} icon={verb.icon} filled={verb.rating !== null} fit>
			<RatingChoices
				menu
				rating={verb.rating ?? null}
				onpick={(value) => verb.rate?.(actsOn(verb), value)}
			/>
		</ContextMenuItem>
	{:else if verb.stars}
		<!--
		A row of stars, not a menu row: it sets a value, and the menu stays open to correct it.
		-->
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
