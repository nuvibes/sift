<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'TagChip',
		category: 'primitive',
		role: 'a tag drawn as a chip',
		basis: 'composes:Chip',
		states: ['default', 'removable']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/*
	 * WHY NOT BITS-UI: a tag drawn as a Chip, which it composes. Everything a chip is, one layer
	 * down.
	 *
	 * One tag, drawn as a chip: the same object on a tile, in the detail view, in the editor and as
	 * a drop target on the tag screen, so it takes what it should look like rather than deciding
	 * from where it is used.
	 *
	 * The chip itself (shape, height, padding, hover, focus, the remove button) is `Chip`'s; what
	 * is here is only what is true of a tag: the hash before the name, the count after it, the
	 * sharing mark, and the pill shape, because a pill means a label somebody applied. Everything
	 * left here is a decision about tags, and the interface's decisions change in one place without
	 * this file knowing.
	 *
	 * No colour: a colour helps tell things apart only up to about six, and the two most useful
	 * hues cannot be spent (the failure red and the accent). A tag is a word, and the reader gets
	 * the word.
	 */
	import Chip from '$lib/components/common/Chip.svelte';
	import { counted } from '$lib/entity/entity-counts';
	import SharingMark from '$lib/components/common/SharingMark.svelte';

	interface Props {
		name: string;
		/** Shown after the name where there is one. Absent means do not show a number at all. */
		count?: number | null;
		/** Turns the chip into a button and calls back when it is chosen. */
		onselect?: () => void;
		/** Adds a remove affordance. Separate from `onselect` so a chip can do either or both. */
		onremove?: () => void;
		/** Drawn as the drop target it currently is. Set by whoever owns the drag. */
		dropping?: boolean;
		selected?: boolean;
		/** Whether anybody has been given this tag or refused it. Admin only. */
		shared?: boolean;
		restricted?: boolean;
		/** In the vault, and listed anyway, which only happens with the vault open. */
		hidden?: boolean;
		/** Open the sharing panel on this tag, from its mark. Absent leaves the mark as plain text. */
		onsharing?: () => void;
	}

	let {
		name,
		count = null,
		onselect,
		onremove,
		dropping = false,
		selected = false,
		shared = false,
		restricted = false,
		hidden = false,
		onsharing
	}: Props = $props();

	const marked = $derived(shared || restricted || hidden);
</script>

<Chip
	{onselect}
	{onremove}
	{dropping}
	{selected}
	removeLabel="Remove {name}"
	shape="pill"
	size="sm"
>
	{#snippet lead()}
		<!-- Quieter than the word it introduces. It is punctuation, not content: at the same weight
		     it reads as the first letter of the tag. -->
		<span class="hash" aria-hidden="true">#</span>
	{/snippet}

	{name}

	{#snippet trail()}
		{#if count !== null}<span class="count">{counted(count)}</span>{/if}
	{/snippet}

	{#snippet aside()}
		{#if marked}
			<!--
				`aside` and not `trail`, and the difference is not cosmetic.

				`trail` renders inside the chip's pressable body. The mark is a button in its own right
				(pressing it opens the sharing panel) and a button inside a button is not markup a
				browser keeps: it tears the inner one out and the press lands on the wrong thing.
				`aside` is the slot that sits outside the body for exactly this case.
			-->
			<SharingMark {shared} {restricted} {hidden} onopen={onsharing} />
		{/if}
	{/snippet}
</Chip>

<style>
	.hash {
		flex: none;
		color: var(--sift-ink-3);
	}

	.count {
		color: var(--sift-ink-3);
		font-variant-numeric: tabular-nums;
	}
</style>
