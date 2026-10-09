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
	 * down. One tag, the same chip everywhere: the hash, the count, the sharing mark and the pill
	 * are a tag's; the rest is Chip's. No colour: a tag is a word.
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
		<!-- The hash, quieter: punctuation. -->
		<span class="hash" aria-hidden="true">#</span>
	{/snippet}

	{name}

	{#snippet trail()}
		{#if count !== null}<span class="count">{counted(count)}</span>{/if}
	{/snippet}

	{#snippet aside()}
		{#if marked}
			<!--
			`aside`, not `trail`: the mark is a button, which cannot sit inside the chip's button.
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
