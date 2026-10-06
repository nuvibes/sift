<script lang="ts">
	/* WHY NOT BITS-UI: bits-ui has no chip, and neither does this: the shape is `Chip`, the control
	   inside it is `Button`, the mark is `Checkbox`. What is here is the ARRANGEMENT of those: which
	   dimension, which values, and the three marks a refusal wears. */

	/*
	 * One filter, drawn as a chip: the only drawing of one there is.
	 *
	 * The same fact is on screen in four places (the chips saying what filters this screen, one for
	 * a clicked filter and one for a typed one; the tooltip on a kept filter saying what it holds;
	 * and the list under "Editing" saying what Save would keep), so it is one component: a filter
	 * reads the same wherever it appears.
	 *
	 * The dimension is spelled with the query language's own token (`tags:`) rather than the
	 * panel's heading ("Tags"), because this is what somebody would type, and two vocabularies for
	 * one dimension on one screen is what the filters gate refuses.
	 *
	 * The three marks a refusal wears, and why two props say it. `excluded` colours the chip and
	 * puts a bar in the box; `struck` draws a line through the words. They are almost always the
	 * same but not the same thing: a filter asking whether a dimension is empty reads as a refusal
	 * and wears its colour, but its word is "none", and striking that through says the opposite. So
	 * `struck` follows `excluded` unless a caller says otherwise, and the one caller that does is
	 * that one.
	 *
	 * Read-only unless handed something to do. A tooltip is portalled, not focusable, and vanishes
	 * when the pointer leaves its owner, so a control in one is unreachable. Given no handlers this
	 * renders a plain `<span>` chip with no press, no cross and no any-or-all button, which is what
	 * the bubble and the edit list need.
	 */
	import { Chip, Pressable } from '$lib/components/common';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { valuesLabel } from '$lib/search/search.svelte';
	import { keptValueLabel } from '$lib/search/saved-searches.svelte';
	import { facetValueLabel } from './facet-labels';

	interface Props {
		/**
		 * The dimension's own token, which is what the chip says and what somebody would type.
		 *
		 * Null for the one clause that has no single dimension: a choice made ACROSS two of them,
		 * which the query language allows and which no column could produce.
		 */
		field?: string | null;
		/** The word drawn before the colon, when it is not the field itself. A file wall's chips
		 *  read the TOKEN the language takes (`tags:`), because the chip is what the address says; an
		 *  entity wall has no language, so its chips read the facet's label ("Hair colour:") rather
		 *  than a key with an underscore in it. */
		shown?: string | null;
		/** What it filters to. A presence question has no values and passes its word as the only one. */
		values: string[];
		/** Whether the values combine as ALL of them rather than any. */
		all?: boolean;
		/**
		 * How many values the filter holds, where this chip draws ONE of them. The bar and a saved
		 * filter's editor draw a chip per value so each can be refused on its own; the any-or-all
		 * control belongs to the filter, so each of its chips carries it while it has several.
		 */
		of?: number;
		/** Draw how the values combine as a mark nobody can press, where a chip only describes. */
		matchShown?: boolean;
		/** Whether the filter refuses rather than includes. Colours the chip and bars the box. */
		excluded?: boolean;
		/** Whether the words themselves are struck through. Follows `excluded`; see the head. */
		struck?: boolean;
		/** Flip it between included and excluded, or open its editor. Omitted where a chip only
		 *  describes. */
		onselect?: () => void;
		/**
		 * Where the chip's body GOES, in place of `onselect`, for a filter that names something with
		 * a page of its own. The one caller is the handle chip: a handle somebody is behind opens
		 * that person. Never both: `Chip` refuses a body that navigates and acts.
		 */
		href?: string;
		/** Take the whole filter off. Omitted where a chip only describes. */
		onremove?: () => void;
		/** Swap between any-of and all-of. Omitted where a chip only describes, and never offered on
		 *  a refused filter. See below. */
		onswitch?: () => void;
		/** Why this filter's value could not be read, when it could not. The chip is drawn in the
		 *  state tone with a mark, and the reason is on hover; the filter matches nothing. */
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

	/*
	 * The values as they read, never as they are spelled in the address: a chip showing `-Ada,Bea`
	 * would say the minus belongs to the first name only.
	 *
	 * A kept filter's own words first. It names things by id and reads back under today's names, and
	 * where the address still holds an id (a name two things share, a thing deleted since) the list
	 * of kept filters says what to call it; see `keptValueLabel`.
	 */
	const joined = $derived(
		valuesLabel(
			values.map((one) =>
				field === null ? one : (keptValueLabel(field, one) ?? facetValueLabel(field, one))
			),
			{ all }
		)
	);

	/*
	 * Whether the any-or-all control is drawn.
	 *
	 * Not on a single value, where there is nothing to combine. Not while the filter is REFUSED,
	 * where it would do nothing: "not any of these" and "not all of these" are the same set of files,
	 * and a control that changes the address without changing the results is worse than no control.
	 */
	const combines = $derived((of ?? values.length) > 1 && !excluded);
	const switchable = $derived(Boolean(onswitch) && combines);
	const marked = $derived(matchShown && combines);

	/*
	 * The dimension's glyph is not drawn here. `facetIcon` names a glyph per dimension, which suits
	 * the chooser, where a glyph sits beside one long word on its own row. A chip is already four
	 * things in a pill a few characters wide (the state box, the token, the values and the cross),
	 * and the token already names the dimension, so the glyph would say it twice. The box stays: it
	 * is the chip's state, which nothing else says.
	 */
</script>

<!--
	Declared here and handed in as `aside` rather than written inside the chip: a snippet is a PROP,
	and one written inside an `{#if}` in a component's children is never passed at all: it compiles,
	renders nothing, and looks like a styling fault. Passing it conditionally is how the condition is
	expressed.
-->
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
		<!--
			`&` for all of them, the splitting arrow for any of them.

			The ampersand is a CHARACTER rather than a glyph from the icon set, and that is a fact about
			the set rather than a preference: Material Symbols ships no ampersand, checked against the
			very font this app subsets, where the name shapes as its own eight letters instead of one
			glyph. It is also the clearest mark there is for "and".

			## Why `Pressable` and not `Button`

			Because `Button` owns its own box and says so: its `class` prop is documented as being for
			POSITION only, never for the button's own look. Sized from here it fights back twice:
			`.btn.icon-only.small` beats the width outright, and on the labelled branch `.small`'s padding
			ties on specificity and wins on source order, so the mark would come out 32px wide as an
			arrow and 37px as an ampersand and CHANGE SIZE with the answer it was giving.

			`Pressable` is the primitive for exactly this: a thing that can be pressed, with no box of
			its own, whose `class` is documented as being for position AND SHAPE. `feedback="none"`
			because the chip is already the ground: what this has to say is only that it is the thing
			under the pointer, which the ink step below says on its own.
		-->
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
	<!--
		The same box the facet row carries, in the same two states: a tick for included, a bar for
		refused. Not separately clickable: the whole chip is the target, and two controls a pixel
		apart are two answers to one click.
	-->
	{#snippet lead()}
		<span class="box"><Checkbox state={excluded ? 'out' : 'on'} mark /></span>
		{#if field}<span class="field">{shown ?? field}:</span>{/if}
	{/snippet}
	<!--
		The word "not" is there for anybody who is not looking at it: a line through some text is not
		announced, and a chip that reads "tags: runway" out loud while meaning the opposite is worse
		than one that is merely plain. It follows the STRIKE rather than the colour, because the two
		part company on a presence question and the word must not contradict what is drawn.
	-->
	{#if struck}<span class="said">not</span>{/if}
	<span class="value" class:struck>{joined}</span>
</Chip>

<style>
	/*
	 * The same treatment the value wears in the facet panel: the refusal ink and a strike. A row
	 * there and the chip it produced are the same fact, and they should look like it.
	 */
	.value.struck {
		color: var(--sift-bad-text);
		text-decoration: line-through;
		text-decoration-thickness: 1px;
	}

	/*
	 * The any-or-all control: a glyph the width of its own box, pulling its own gaps back.
	 *
	 * `:global` because the class is handed to `Pressable`, which compiles it in its own file; an
	 * unscoped rule here would silently match nothing.
	 *
	 * `.pressable.in-chip`, and the extra class is load-bearing: `Pressable`'s reset sets `color:
	 * inherit` at exactly the specificity of a bare `.in-chip`, so source order would decide, and
	 * the reset wins; the mark would take the chip's colour and light up whenever the pointer
	 * crossed the chip.
	 *
	 * The two gaps are written separately. The chip's `aside` adds a `--chip-pad` after a square
	 * box, and the cross carries a `--space-1` of leading padding this side has no counterpart for,
	 * so the margins take back what the body's trailing padding, the aside's own and the cross's
	 * leading padding put there, and they are not the same number.
	 *
	 * Measure the ink, not the box: a glyph is centred inside its box, so box-to-box gaps can match
	 * while the ink is visibly uneven. Ink to ink, the gap after the mark would be four pixels
	 * wider than before it for both glyphs, which is the cross's padding, and the trailing margin
	 * takes it. The arrow's ink is 16px wide in its box and the ampersand's 11, so the air differs
	 * between states while the two sides of each match, which is what an eye reads.
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

	/*
	 * It lights up on its own, and it does not get a ground.
	 *
	 * On its own, because it is a different act from pressing the chip: the chip flips the filter
	 * between included and refused, while this swaps how its values combine. `Chip` scopes its own
	 * hover to its body, so pointing at this does not light the words.
	 *
	 * No ground, because the chip is already one: a hover surface would draw a patch across the
	 * pill's colour, a grey box in the middle of a refused chip's red. The ink step says what needs
	 * saying, in the register the chip's own cross uses.
	 */
	:global(.pressable.in-chip:hover:not(:disabled)) {
		color: var(--sift-ink);
	}

	/* The ampersand is a LETTER standing in for a glyph, so it needs enough weight to read as a mark
	   rather than as a typo in the chip, and not so much that it is the heaviest thing on the row.
	   At 700 and 15px it would outweigh the values it sits beside; the forking arrow next to it is a
	   1.5px stroke, and this is what matches it. */
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
