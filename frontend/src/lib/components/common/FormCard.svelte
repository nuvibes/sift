<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'FormCard',
		category: 'composition',
		role: "a short form on a settings pane, its fields drawn as the pane's own rows",
		basis: 'own',
		states: ['default', 'with actions']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: there is nothing to behave. This is the site's own `<form>` with the
	   library's `Field`s in it, laid out as a settings pane's rows. */

	/*
	 * A short form standing on a settings pane: add cookies, add a stash-box, import a tunnel, add
	 * a guest, set a PIN.
	 *
	 * A settings pane is a column of rows (a name and its help on the left, the control in the
	 * control column on the right) and the form is drawn the same way: each `Field` is a row, its
	 * label and help on the left and its box in the wide control column, a hairline between two
	 * fields, and the submit at the pane's right edge under the last one. A bordered card inside a
	 * pane of rows is a second shape for the same kind of thing. A field holding a text area keeps
	 * the whole width under its label, because what is pasted there is long.
	 *
	 * It is the form itself, not a box around one: handing the submit in makes the element a real
	 * `<form>`, so Enter in any field submits, with one column gap and margin for every pane.
	 */
	import type { Snippet } from 'svelte';
	import { givePressSize } from './press-size';
	import PressSize from './PressSize.svelte';

	interface Props {
		/**
		 * What the form is for: "Add cookies", "Add a stash-box". A heading, so a verb phrase.
		 *
		 * Optional, because several of these already sit under a `SettingGroup` whose heading says
		 * exactly this. A card with the same words twice, three lines apart, is worse than a card
		 * with none, so where the section names the form, this is left out and the card is only
		 * the container.
		 */
		title?: string;
		/** One sentence under the heading, where the form needs one. */
		help?: string;
		/**
		 * What submitting does.
		 *
		 * Given, this is a `<form>` and Enter in any field submits it. Absent, it is a plain block,
		 * for the handful of cards whose content is a list of buttons rather than fields to fill in.
		 */
		onsubmit?: (event: SubmitEvent) => void;
		/**
		 * Something is being dragged over this card.
		 *
		 * One of these is a drop target (importing a tunnel takes a file) and the target has to
		 * say so while a file is over it. A prop rather than a second card component: the card is
		 * the drop zone, and a copy of it that only differed by a border colour is a copy.
		 */
		active?: boolean;
		/** The fields. */
		children: Snippet;
		/**
		 * The button that submits, and a way out beside it where there is one.
		 *
		 * Drawn in a row at the end of the card, the way out first and the act last, like every
		 * sheet's foot, so a form on a pane ends on the same side as every dialog. Rendered inside
		 * the same `<form>`, so `type="submit"` still submits.
		 */
		actions?: Snippet;
		/**
		 * Anything else the element needs: `data-drop-zone`, the drag handlers, an `aria-label`.
		 *
		 * Spread BEFORE `class` on the element below, deliberately. A spread that lands after it
		 * replaces the class attribute outright and the card loses its own styling.
		 */
		[key: string]: unknown;
	}

	let { title, help, onsubmit, active = false, children, actions, ...rest }: Props = $props();

	/* A form on a pane is the pane's rows, so a press inside it is a row's small one. Its answers
	   (the submit, and a Cancel beside it) are the default size, level with each other. */
	givePressSize('small');
</script>

{#snippet inside()}
	{#if title}<h3>{title}</h3>{/if}
	{#if help}<p class="help">{help}</p>{/if}
	{@render children()}
	{#if actions}<div class="actions">
			<PressSize size="medium">{@render actions()}</PressSize>
		</div>{/if}
{/snippet}

{#if onsubmit}
	<form {...rest} class="card ruled-row" class:active {onsubmit}>{@render inside()}</form>
{:else}
	<div {...rest} class="card ruled-row" class:active>{@render inside()}</div>
{/if}

<style>
	.card {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		margin: 0;
		padding-block: var(--space-2);
		border-radius: var(--radius-lg);
		/* The drop state steps over --dur-instant rather than appearing between frames.
		   Declared on the resting rule so it animates in both directions. */
		transition:
			box-shadow var(--dur-instant) var(--ease),
			background var(--dur-instant) var(--ease);
	}

	/* The line above the form where a row stands right before it, as a row draws its own. A row
	   after the form draws its line through `ruled-row`, which the form wears. */
	:global(:is(.ruled-row, :has(> .ruled-row))) + .card:not(:has(> :global(.row:first-child))) {
		border-block-start: 1px solid var(--sift-line);
	}

	/* A form made of settings rows (`FieldRow`): the rows are the form's whole shape, their own
	   padding and their own lines, so the card adds no gap between them and no band around them.
	   Its first row draws the line above the form itself, as the first row held after a row does. */
	.card:has(> :global(.row)) {
		gap: 0;
		padding-block: 0;
	}

	/* With no gap between its parts, such a form spaces its own title: the title stands a row's
	   top padding under whatever is above it, the sentence under it keeps its own line instead of
	   being pulled up into the title, and what follows them that is not a row (the tunnel's drop
	   zone, the stash-box presets) stands clear of them. */
	.card:has(> :global(.row)) > h3 {
		padding-block-start: var(--space-4);
	}

	.card:has(> :global(.row)) > .help {
		margin-block-start: var(--space-1);
	}

	.card:has(> :global(.row)) > :is(h3, .help) + :not(:global(.row), .help) {
		margin-block-start: var(--space-3);
	}

	/* A file is over it. The accent, because that is what "this will take it" is drawn in
	   everywhere else in Sift; a ring rather than a border, so nothing moves while it shows. */
	.card.active {
		box-shadow: inset 0 0 0 1px var(--sift-accent);
		background: var(--sift-surface-2);
	}

	/*
	 * Each field is a row: the label and its help in the first column, the box in the wide
	 * control column, the error under the box. Reached through the field's own classes, bounded
	 * by this form's element and a child combinator, so no field anywhere else is touched.
	 */
	.card > :global(.field:not(:has(textarea))) {
		display: grid;
		grid-template-columns: minmax(0, 1fr) var(--settings-control-col-wide, min(26rem, 60%));
		column-gap: var(--space-6);
		row-gap: var(--space-1);
		align-items: start;
		padding-block: var(--space-4);
	}

	.card > :global(.field:not(:has(textarea)) > .label) {
		grid-column: 1;
		grid-row: 1;
		font: var(--text-body);
		font-weight: 600;
		color: var(--sift-ink);
		line-height: 1.3;
	}

	.card > :global(.field:not(:has(textarea)) > .help) {
		grid-column: 1;
		grid-row: 2;
		max-width: var(--reading-measure);
		line-height: 1.5;
	}

	.card > :global(.field:not(:has(textarea)) > .control) {
		grid-column: 2;
		grid-row: 1 / span 2;
		display: grid;
	}

	/* A box fills the column; a control with a width of its own (the PIN's six cells) ends at
	   its right edge, where every control on the pane ends. */
	.card > :global(.field > .control > .pin-box) {
		justify-self: end;
	}

	.card > :global(.field:not(:has(textarea)) > .error) {
		grid-column: 2;
	}

	/* A text area keeps the width under its label: a pasted file does not fit a column. */
	.card > :global(.field:has(textarea)) {
		padding-block: var(--space-4);
	}

	.card > :global(.field:has(textarea) > .label) {
		font: var(--text-body);
		font-weight: 600;
		color: var(--sift-ink);
	}

	/* The line between two fields belongs to the lower one, as between two rows. */
	.card > :global(.field ~ .field) {
		border-block-start: 1px solid var(--sift-line);
	}

	/* The strength meter reads the box above it, so it stands under that box. */
	.card > :global(.strength) {
		align-self: flex-end;
		inline-size: var(--settings-control-col-wide, min(26rem, 60%));
		margin-block-start: calc(-1 * var(--space-3));
	}

	/*
	 * An h3, below the h2 a `SettingGroup` heading is: a form on a pane sits inside the thing its
	 * section is about ("Add cookies" under "Cookies"), so a heading at the same level would read
	 * as a sibling section to anybody reading by heading. At the body's size, because nothing
	 * under a title is smaller than the rows it heads, and on the subheading's rung (`--text-h3`)
	 * that `SectionHeading` draws a heading inside a group on: the row's own face at a heavier
	 * weight would be the same size in a second face, two headings that read as two kinds.
	 */
	h3 {
		margin: 0;
		font: var(--text-h3);
		letter-spacing: var(--tracking-h2);
		color: var(--sift-ink);
		/* "New key for" a stash-box names it, and a name can be one unbroken token. */
		overflow-wrap: anywhere;
	}

	/* At the pane's right edge, under the last field, where every row's control ends. See
	   `actions` above. */
	.actions {
		display: flex;
		justify-content: flex-end;
		gap: var(--space-2);
		padding-block-end: var(--space-2);
	}

	/* A phone's width has no room for a control column: the box goes under its label, the whole
	   width of the row, as a row's control does there. */
	@media (max-width: 767px) {
		/* Where a row of this form packs a control of several parts: the start, where the stacked
		   box starts, as a settings row publishes it (`LabelledRow`). */
		.card {
			--row-pack: flex-start;
		}

		.card > :global(.field:not(:has(textarea))) {
			grid-template-columns: minmax(0, 1fr);
		}

		.card > :global(.field:not(:has(textarea)) > .control),
		.card > :global(.field:not(:has(textarea)) > .error) {
			grid-column: 1;
			grid-row: auto;
		}

		.card > :global(.field > .control > .pin-box) {
			justify-self: start;
		}

		.card > :global(.strength) {
			inline-size: 100%;
		}
	}

	.help {
		margin: calc(-1 * var(--space-2)) 0 0;
		max-width: var(--reading-measure);
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
		line-height: 1.5;
	}
</style>
