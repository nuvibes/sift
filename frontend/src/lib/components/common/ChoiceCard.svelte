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
	arrow keys, roving focus and checked semantics come from it; this is only the card. */

	/* One of several things to choose between, shown rather than described: a preview, a name and
	 * when you would want it. The chosen one is a border and a tint, not a tick. */
	import type { Snippet } from 'svelte';
	import { RadioGroup } from 'bits-ui';
	import Icon from '$lib/components/Icon.svelte';
	import type { IconName } from '$lib/design/icons';

	interface Props {
		name: string;
		/** A glyph beside the name, for a choice that is an action rather than a look. */
		icon?: IconName;
		/** Draw that glyph solid. For the ones whose outline is mostly empty space. */
		iconFilled?: boolean;
		/** When you would want this one. The part pickers usually leave out. */
		note?: string;
		/** A short aside after the name, in the quiet ink: "(default)", "(recommended)". */
		aside?: string;
		/** A closing line on its own row, addressing the reader. */
		footnote?: string;
		/** Which choice this card is, inside a `ChoiceGroup`. The group says whether it is chosen. */
		value?: string;
		/** For a button card: whether it is the pressed one. */
		chosen?: boolean;
		/** A miniature of the thing being chosen, drawn by the caller. */
		preview?: Snippet;
		onchoose?: () => void;
		/** Passed through to the element, so a caller can put this inside a radio group. */
		role?: 'radio' | 'button';
		/**
		 * A row on the recessed ground, chosen by the ground not the accent, for a set inside a
		 * panel.
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
	<!-- The library's item, drawn by this file's button; `checked` comes from the group. -->
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
		/* From the top, so names line up across a row of cards. */
		align-content: start;
		gap: var(--space-1);
		/* The card's own ink, for a picture that takes the text colour. */
		color: var(--sift-ink-2);
		padding: var(--space-3);
		/* The edge is a layer under this transparent border (see `--sift-card`). */
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

	/* The row form, on the recessed ground. See `dense`. */
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

	/* Glyph and words on one line by their middles, so a glyph does not drop the words. */
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

	/* Its own row, a step quieter than the note. */
	.footnote {
		margin-block-start: var(--space-1);
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	:global(:root[data-motion='reduce']) .card {
		transition: none;
	}
	/* A finger's height on a phone. */
	@media (max-width: 767px) {
		.card {
			min-block-size: var(--touch-target);
		}
	}
</style>
