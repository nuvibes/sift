<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'ChoiceCard',
		category: 'control',
		role: 'one of a few large choices that are picked like radio buttons, drawn as a card',
		basis: 'bits-ui:RadioGroup',
		states: ['resting', 'chosen', 'disabled']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* THE GROUP IS `ChoiceGroup`, bits-ui's RadioGroup; a card is one of its items: put these inside one and
	   arrow keys, roving focus and the checked semantics all come from it. What is here is only the
	   card: a preview, a name and a sentence, in a box. There is no primitive for that shape and
	   there is nothing for one to do. */

	/*
	 * One of several things to choose between, shown rather than described.
	 *
	 * ## Why a card and not a row of radio buttons
	 *
	 * The three pickers under Appearance choose a background, an accent and a typeface, and every
	 * one of them is a choice about how something LOOKS. A radio button beside the word "Charcoal"
	 * asks somebody to imagine a colour they have never seen; a miniature of the app in that colour
	 * does not. So the preview is the control's main content and the words are underneath it.
	 *
	 * ## What each part is for, and why all three
	 *
	 * The preview says what it looks like. The name is what it is called, so it can be talked about
	 * and searched for. The note says when you would want it, which is the part that is usually
	 * missing, and the reason a picker with good previews still leaves people guessing between two
	 * options that both look fine.
	 *
	 * ## The chosen one is a border and a tint, not a tick
	 *
	 * A tick in the corner is a second thing to look at on a card whose whole job is to be looked
	 * at. Lifting the whole card says the same thing without adding anything to it. The state is
	 * still announced properly, by whatever radio group this sits in.
	 */
	import type { Snippet } from 'svelte';
	import { RadioGroup } from 'bits-ui';
	import Icon from '$lib/components/Icon.svelte';
	import type { IconName } from '$lib/design/icons';

	interface Props {
		/** What it is called. */
		name: string;
		/**
		 * A glyph beside the name, for a card whose choice is an ACTION rather than a look.
		 *
		 * The preview below is a miniature of the thing being chosen, and it is right where the
		 * choice is between two appearances. A card offering "remove it from Sift" against "delete
		 * it off the disk" has nothing to show a miniature of: what separates those two is what
		 * they DO, and the app already draws each of those two things with a glyph everywhere else.
		 * So the same glyph goes on the card, and the destructive one is recognised before a word of
		 * it is read.
		 *
		 * Beside the name rather than above it, unlike the preview: it is a mark on a label, not the
		 * content of the card. Nothing is added to the accessible name (the name is right there in
		 * words) so the glyph is hidden from a screen reader, which is `Icon`'s behaviour with no
		 * label given.
		 */
		icon?: IconName;
		/** Draw that glyph solid. For the ones whose outline is mostly empty space. */
		iconFilled?: boolean;
		/** When you would want this one. The part pickers usually leave out. */
		note?: string;
		/** A short aside after the name, in the quiet ink: "(default)", "(recommended)". */
		aside?: string;
		/**
		 * A closing line under the note, on its own row.
		 *
		 * For the parenthetical that tells somebody whether this card is the one for them: "select
		 * this if it is your first time". Its own row rather than the tail of `note`, because that is
		 * a different KIND of sentence: the note describes the option, this one addresses the reader,
		 * and run together at the end of a paragraph it is read as more description and skipped.
		 */
		footnote?: string;
		/** Whether this is the current choice. */
		/** Which choice this card is, inside a `ChoiceGroup`. The group says whether it is chosen. */
		value?: string;
		/** For a card that is a BUTTON (`role="button"`): whether it is the pressed one. A radio card
		 *  reads its state from the group instead. */
		chosen?: boolean;
		/** A miniature of the thing being chosen. Drawn by the caller, which is the only part that
		 *  knows what it looks like. */
		preview?: Snippet;
		onchoose?: () => void;
		/** Passed through to the element, so a caller can put this inside a radio group. */
		role?: 'radio' | 'button';
		/**
		 * A row rather than a card, and the chosen one said in the ground rather than in the accent.
		 *
		 * For a list of small pictures sitting inside a raised panel: the theater's seven wall
		 * shapes. Two things are wrong there for the full card and both are worth naming.
		 *
		 * The card's ground is one step up from the page, which is the same step the panel around it
		 * is already on, so a card inside a panel has no edge and reads as a black box.
		 *
		 * And the accent is the wrong signal for a set of seven. One coloured item among seven
		 * pictures reads as a warning about that item rather than as "this is the current one",
		 * so the chosen shape is lifted in the ground instead, which is what saying it quietly looks
		 * like. On Appearance there are two or three options and the accent is right; the number in
		 * the set is what decides it.
		 */
		dense?: boolean;
	}

	let {
		name,
		icon,
		iconFilled = false,
		note,
		aside,
		footnote,
		value = '',
		chosen = false,
		preview,
		onchoose,
		role = 'radio',
		dense = false
	}: Props = $props();
</script>

{#snippet inside()}
	{#if preview}<span class="preview">{@render preview()}</span>{/if}
	<span class="name">
		{#if icon}<Icon name={icon} filled={iconFilled} size={16} />{/if}
		{name}
		{#if aside}<span class="aside">{aside}</span>{/if}
	</span>
	{#if note}<span class="note">{note}</span>{/if}
	{#if footnote}<span class="footnote">{footnote}</span>{/if}
{/snippet}

{#if role === 'radio'}
	<!-- The library's item, drawn by this file's own button so the rules below reach it. `checked`
	     arrives from the group, which is the one place the value is held. -->
	<RadioGroup.Item {value}>
		{#snippet child({ props })}
			<button {...props} class="card" class:dense>
				{@render inside()}
			</button>
		{/snippet}
	</RadioGroup.Item>
{:else}
	<button type="button" class="card" class:dense aria-pressed={chosen} onclick={onchoose}>
		{@render inside()}
	</button>
{/if}

<style>
	.card {
		display: grid;
		/* From the top: a button centres what it holds, so in a row of cards stretched to one
		   height a card whose note is one line would stand its name lower than its neighbour's. */
		align-content: start;
		gap: var(--space-1);
		/* The card's own ink, so a picture inside it that takes the text colour is not black on the
		   dark ground. */
		color: var(--sift-ink-2);
		padding: var(--space-3);
		/* The card's light, its edge a layer of the ground under this transparent border (see
		   `--sift-card`). A hover or a pick paints the border itself, over that layer. */
		border: 1px solid transparent;
		border-radius: var(--radius-md);
		background: var(--sift-card);
		text-align: start;
		cursor: pointer;
		transition:
			border-color var(--dur-instant) var(--ease),
			background var(--dur-instant) var(--ease);
	}

	.card:hover {
		border-color: var(--sift-ink-3);
	}

	.card[aria-checked='true'],
	.card[aria-pressed='true'] {
		border-color: var(--sift-accent-text);
		background: var(--sift-accent-bg);
	}

	/* The row form: a picture and a name on one line, on the recessed ground so it has an edge
	   against the panel it sits in. See `dense` for why the chosen one is not the accent here. */
	.card.dense {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		padding: var(--space-2) var(--space-3);
		border-color: var(--sift-line);
		background: var(--sift-surface-1);
		color: var(--sift-ink-2);
	}

	.card.dense:hover {
		border-color: var(--sift-line);
		background: var(--sift-surface-3);
		color: var(--sift-ink);
	}

	.card.dense[aria-checked='true'],
	.card.dense[aria-pressed='true'] {
		border-color: var(--sift-ink-3);
		background: var(--sift-surface-3);
		color: var(--sift-ink);
	}

	/* The picture sits beside the name rather than above it, so it takes no bottom margin. */
	.card.dense .preview {
		margin-block-end: 0;
	}

	.card.dense .name {
		font: var(--text-body);
		color: inherit;
		white-space: nowrap;
	}

	.card:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	/* The caller's miniature, given a box and left alone inside it. */
	.preview {
		display: block;
		margin-block-end: var(--space-2);
	}

	/* The glyph and the words on one line, aligned on their middles. A flex row rather than the
	   glyph flowing as ordinary text: a Material glyph sits in a box taller than the label's own
	   line, which drops the words a pixel or two against the note under them, read as the lines of
	   one card not being level with the card beside it. Costs nothing on a card with no glyph. */
	.name {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		font: var(--text-label);
		color: var(--sift-ink);
	}

	.aside {
		color: var(--sift-ink-3);
		font-weight: 400;
	}

	.note {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	/* Its own row, and a step further back than the note: it is guidance about choosing rather than
	   a fact about the option. */
	.footnote {
		margin-block-start: var(--space-1);
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	:global(:root[data-motion='reduce']) .card {
		transition: none;
	}
	/* A finger's width at a phone's width: a card with only a name is shorter than the touch target
	   on its own, and every picker draws it, so the floor is the card's, not each page's. */
	@media (max-width: 767px) {
		.card {
			min-block-size: var(--touch-target);
		}
	}
</style>
