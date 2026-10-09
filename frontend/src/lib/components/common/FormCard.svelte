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

	/* A short form on a settings pane, drawn as the pane's rows (label left, box in the control
	 * column, submit at the right edge); a real <form>, so Enter submits. */
	import type { Snippet } from 'svelte';
	import { givePressSize } from './press-size';
	import PressSize from './PressSize.svelte';

	interface Props {
		/** What the form is for, as a heading; left out where the section's heading says it. */
		title?: string;
		/** One sentence under the heading, where the form needs one. */
		help?: string;
		/** What submitting does; absent, a plain block. */
		onsubmit?: (event: SubmitEvent) => void;
		/** A file is being dragged over this card, the drop zone. */
		active?: boolean;
		children: Snippet;
		/** The submit, with a way out before it, inside the form. */
		actions?: Snippet;
		/** Anything else for the element, spread before `class` so it cannot replace it. */
		[key: string]: unknown;
	}

	let { title, help, onsubmit, active = false, children, actions, ...rest }: Props = $props();

	/* A press inside is a row's small one; its answers are the default size. */
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
		/* The drop state steps, both ways. */
		transition:
			box-shadow var(--dur-instant) var(--ease),
			background var(--dur-instant) var(--ease);
	}

	/* The line above the form after a row; it wears `ruled-row` for the row after. */
	:global(:is(.ruled-row, :has(> .ruled-row))) + .card:not(:has(> :global(.row:first-child))) {
		border-block-start: 1px solid var(--sift-line);
	}

	/* A form of settings rows adds no gap or band; its first row draws the line above. */
	.card:has(> :global(.row)) {
		gap: 0;
		padding-block: 0;
	}

	/* Such a form spaces its own title and what follows that is not a row. */
	.card:has(> :global(.row)) > h3 {
		padding-block-start: var(--space-4);
	}

	.card:has(> :global(.row)) > .help {
		margin-block-start: var(--space-1);
	}

	.card:has(> :global(.row)) > :is(h3, .help) + :not(:global(.row), .help) {
		margin-block-start: var(--space-3);
	}

	/* A file over it: the accent ring, so nothing moves. */
	.card.active {
		box-shadow: inset 0 0 0 1px var(--sift-accent);
		background: var(--sift-surface-2);
	}

	/* Each field as a row, reached through its classes under this form only. */
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

	/* A control with its own width ends at the column's right edge. */
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

	/* An h3 under the section's h2, on the subheading rung. */
	h3 {
		margin: 0;
		font: var(--text-h3);
		letter-spacing: var(--tracking-h2);
		color: var(--sift-ink);
		/* "New key for" a stash-box names it, and a name can be one unbroken token. */
		overflow-wrap: anywhere;
	}

	/* At the pane's right edge under the last field. */
	.actions {
		display: flex;
		justify-content: flex-end;
		gap: var(--space-2);
		padding-block-end: var(--space-2);
	}

	/* On a phone the box goes under its label. */
	@media (max-width: 767px) {
		/* A multi-part control packs from the start, as LabelledRow publishes. */
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
