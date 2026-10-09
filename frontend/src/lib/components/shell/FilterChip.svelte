<script lang="ts">
	/* WHY NOT BITS-UI: bits-ui has no chip, and neither does this: the shape is `Chip`, the control
	   inside it is `Button`, the mark is `Checkbox`. */

	/*
	 * One filter as a chip, the only drawing of one. The dimension reads as its query token
	 * (`tags:`). With no handlers it is a plain span: a tooltip cannot hold controls.
	 */
	import { Chip, Pressable } from '$lib/components/common';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { valuesLabel } from '$lib/search/search.svelte';
	import { keptValueLabel } from '$lib/search/saved-searches.svelte';
	import { facetValueLabel } from './facet-labels';

	interface Props {
		/** The dimension's token; null for a clause across two dimensions. */
		field?: string | null;
		/** The word before the colon when it is not the field: an entity wall's facet label. */
		shown?: string | null;
		/** What it filters to; a presence question passes its word as the only value. */
		values: string[];
		/** Whether the values combine as ALL of them rather than any. */
		all?: boolean;
		/** How many values the filter holds when this chip draws one; any-or-all rides on each. */
		of?: number;
		/** Draw how the values combine as a mark nobody can press, where a chip only describes. */
		matchShown?: boolean;
		/** Whether the filter refuses rather than includes. Colours the chip and bars the box. */
		excluded?: boolean;
		/** Whether the words are struck; follows `excluded` except on a presence question. */
		struck?: boolean;
		/** Flip included and excluded, or open its editor. */
		onselect?: () => void;
		/** Where the body goes instead of `onselect` (the handle chip opens that person). */
		href?: string;
		/** Take the whole filter off. Omitted where a chip only describes. */
		onremove?: () => void;
		/** Swap between any-of and all-of; never offered on a refused filter. */
		onswitch?: () => void;
		/** Why the value could not be read: drawn in the state tone, matching nothing. */
		problem?: string;
	}

	let {
		field = null,
		shown = null,
		values,
		all = false,
		of,
		matchShown = false,
		excluded = false,
		struck = excluded,
		onselect,
		href,
		onremove,
		onswitch,
		problem
	}: Props = $props();

	// As they read, not as spelled in the address; a kept filter's own words first.
	const joined = $derived(
		valuesLabel(
			values.map((one) =>
				field === null ? one : (keptValueLabel(field, one) ?? facetValueLabel(field, one))
			),
			{ all }
		)
	);

	// Not on one value, nor on a refusal, where any-of and all-of are the same files.
	const combines = $derived((of ?? values.length) > 1 && !excluded);
	const switchable = $derived(Boolean(onswitch) && combines);
	const marked = $derived(matchShown && combines);

	// No dimension glyph: the token already names it, and the pill is crowded.
</script>

<!-- Passed as `aside`: a snippet written inside an `{#if}` in children is never passed. -->
{#snippet matchGlyph()}
	{#if all}<span class="amp">&amp;</span>{:else}<Icon name="arrow_split" size={16} />{/if}
{/snippet}

{#snippet matchMark()}
	<span class="match" role="img" aria-label={all ? 'All of these' : 'Any of these'}>
		{@render matchGlyph()}
	</span>
{/snippet}

{#snippet anyOrAll()}
	<Tooltip label={all ? 'All of these' : 'Any of these'}>
		<!-- `&` for all (the icon set has no ampersand), the splitting arrow for any. -->
		<!-- `Pressable`, not `Button`: a Button's own box would resize the mark per glyph. -->
		<Pressable
			class="in-chip"
			feedback="none"
			radius="sm"
			aria-label={all ? `All of these ${field ?? 'values'}` : `Any of these ${field ?? 'values'}`}
			onclick={() => onswitch?.()}
		>
			{@render matchGlyph()}
		</Pressable>
	</Tooltip>
{/snippet}

<Chip
	aside={switchable ? anyOrAll : marked ? matchMark : undefined}
	tone={problem ? 'state' : undefined}
	icon={problem ? 'error' : undefined}
	selected={!excluded}
	refused={excluded}
	{onselect}
	{href}
	{onremove}
	removeLabel="Remove the filter {field ?? 'query'}{field ? `: ${joined}` : ''}"
>
	<!-- The facet row's box; not separately clickable, the whole chip is the target. -->
	{#snippet lead()}
		<span class="box"><Checkbox state={excluded ? 'out' : 'on'} mark /></span>
		{#if field}<span class="field">{shown ?? field}:</span>{/if}
	{/snippet}
	<!-- "not" for a screen reader, which does not announce a strike; it follows the strike. -->
	{#if struck}<span class="said">not</span>{/if}
	<span class="value" class:struck>{joined}</span>
</Chip>

<style>
	/* The facet panel's refusal treatment: a row and its chip are the same fact. */
	.value.struck {
		color: var(--sift-bad-text);
		text-decoration: line-through;
		text-decoration-thickness: 1px;
	}

	/*
	 * `:global` because the class compiles in `Pressable`; `.pressable.in-chip` outranks its colour
	 * reset. The margin takes back the cross's leading padding so the ink gaps match.
	 */
	:global(.pressable.in-chip),
	.match {
		display: grid;
		place-items: center;
		inline-size: 20px;
		block-size: var(--chip-height);
		margin-inline: 0 calc(var(--chip-pad) * -1);
		color: var(--sift-ink-2);
		/* The ink steps over `--dur-instant` rather than changing between frames. */
		transition: color var(--dur-instant) var(--ease);
	}

	/* Its own hover, a different act from the chip's; no ground, which would patch the pill. */
	:global(.pressable.in-chip:hover:not(:disabled)) {
		color: var(--sift-ink);
	}

	/* A letter standing in for a glyph: weighted to match the arrow's 1.5px stroke. */
	.amp {
		font-size: 14px;
		font-weight: 600;
		line-height: 1;
	}

	/* The box the chip leads with. The chip owns the press; this is a mark. */
	.box {
		display: inline-flex;
		pointer-events: none;
	}

	/* Read out, never drawn. Not `display: none`, which takes it from a screen reader as well. */
	.said {
		position: absolute;
		inline-size: 1px;
		block-size: 1px;
		overflow: hidden;
		clip-path: inset(50%);
		white-space: nowrap;
	}

	.field {
		font: var(--text-data);
		opacity: 0.72;
	}
</style>
