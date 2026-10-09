<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'LabelledRow',
		category: 'composition',
		role: 'the two-column row a settings pane is made of: the label, then the control',
		basis: 'own',
		states: ['default', 'with help', 'wide control column', 'with a foot line', 'stacked']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: there is nothing to behave. This is the two-column grid a settings pane is
	made of; the controls in the right column are the library's. */

	/* A name, a sentence explaining it and the thing itself: the row behind SettingRow and FactRow,
	   with a fixed control column. DataRow is one of many in a keyed list; this is one of a few. */
	import type { Snippet } from 'svelte';
	import MoreAbout from './MoreAbout.svelte';
	import PathCopy from '$lib/settings-ui/PathCopy.svelte';
	import { givePressSize } from './press-size';
	import { paneHeld } from '$lib/settings-ui/read-only';

	interface Props {
		/** What the row is about. Plain text; the caller owns whether it is also a `<label for>`. */
		label?: string;
		/**
		 * An address for this row, on the row so a link lands on its name (settings-anchor rings
		 * it).
		 */
		id?: string;
		/** The label as markup, for a row that has to bind it to a control with `for`. */
		name?: Snippet;
		/** One sentence saying what this is. The part that is usually missing. */
		help?: string;
		/** The help's `id`, so a control in the right column can point at it. */
		helpId?: string;
		/** Reference material folded away: licence terms, sizes, figures. */
		disclosure?: string;
		/** The right column: a control, a fact, a badge, a button. */
		children: Snippet;
		/** Dim the whole row, for a setting hanging off one switched off. */
		inert?: boolean;
		/** Let the right column size itself, for a row whose right side is a short fact. */
		looseColumn?: boolean;
		/** A wider control column, for two controls side by side. */
		wide?: boolean;
		/** A line under the help about the row's state, on the left with the words. */
		foot?: Snippet;
		/** A field with a press beside it: the press takes the field's height. */
		besideField?: boolean;
		/** The control under the name, the row's whole width (a text area). */
		stacked?: boolean;
	}

	let {
		label,
		id,
		name,
		help,
		helpId,
		disclosure,
		children,
		inert = false,
		looseColumn = false,
		wide = false,
		foot,
		besideField = false,
		stacked = false
	}: Props = $props();

	// svelte-ignore state_referenced_locally
	givePressSize(besideField ? 'medium' : 'small');

	/* A read-only pane holds the control in a disabled fieldset, whatever it is made of. */
	const held = paneHeld();

	/* The row's name as drawn (a caller's `name` is its own markup), for its settings path. */
	let named = $state<HTMLElement | null>(null);
	const said = () => label ?? named?.querySelector('.name')?.textContent?.trim() ?? '';
</script>

<div
	{id}
	class="row ruled-row"
	class:name-alone={!help && !(foot && !wide)}
	class:inert
	class:loose={looseColumn}
	class:wide
	class:beside-field={besideField}
	class:stacked
>
	<div class="named path-host" bind:this={named}>
		<!-- Inside Settings only: elsewhere there is no settings path and it draws nothing. -->
		<PathCopy name={said} />
		{#if name}{@render name()}{:else}<span class="name">{label}</span>{/if}

		{#if help}<p class="help" id={helpId}>{help}</p>{/if}

		{#if foot && !wide}<div class="foot">{@render foot()}</div>{/if}

		{#if disclosure}<MoreAbout text={disclosure} />{/if}
	</div>

	<div class="control">
		{#if held()}
			<fieldset class="held" disabled>{@render children()}</fieldset>
		{:else}
			{@render children()}
		{/if}
	</div>

	<!-- A wide row's foot takes a line of its own under both columns. -->
	{#if foot && wide}<div class="foot spans">{@render foot()}</div>{/if}
</div>

<style>
	/* A fixed control column, so every control sits the same distance from the edge. */
	.row {
		display: grid;
		grid-template-columns: minmax(0, 1fr) var(--settings-control-col, 15rem);
		align-items: start;
		column-gap: var(--space-6);
		padding: var(--space-4) 0;
	}

	/* The line between rows is the lower row's top edge, drawn only after a row (`ruled-row`). */
	:global(:is(.ruled-row, :has(> .ruled-row))) + .row,
	:global(:is(.ruled-row, :has(> .ruled-row)) + *) > .row:first-child {
		border-block-start: 1px solid var(--sift-line);
	}

	/*
	 * A group of choices (`choices`) draws no line between its rows; after, and as heavy, so it
	 * wins.
	 */
	:global(.choices) > .row,
	:global(.choices) > :global(*) > .row:first-child {
		border-block-start: 0;
	}

	/* Wider, and never more than most of the row, so a narrow window still leaves the name room. */
	.row.wide {
		grid-template-columns: minmax(0, 1fr) var(--settings-control-col-wide, min(26rem, 60%));
	}

	.row.stacked {
		grid-template-columns: minmax(0, 1fr);
		row-gap: var(--space-3);
	}

	.row.loose {
		grid-template-columns: minmax(0, 1fr) auto;
		align-items: baseline;
	}

	/* A name alone stands level with its control. */
	.row.name-alone:not(.stacked) {
		align-items: center;
	}

	.row.inert .named,
	.row.inert .foot.spans {
		opacity: 0.5;
	}

	.named {
		position: relative;
		min-width: 0;
	}

	/* The name carries weight as well as size and ink; global too, for a caller's own `name`. */
	.name,
	.named :global(.name) {
		display: block;
		font: var(--text-body);
		font-weight: 600;
		color: var(--sift-ink);
		line-height: 1.3;
	}

	.help {
		margin: var(--space-1) 0 0;
		max-width: var(--reading-measure);
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
		line-height: 1.5;
	}

	.foot {
		margin: var(--space-2) 0 0;
		max-width: var(--reading-measure);
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
		line-height: 1.5;
	}

	/* Every foot line of every row at the one reading measure, whatever the control column took. */
	.foot.spans {
		grid-column: 1 / -1;
	}

	/* Packed to the column's end, so every control ends on one edge. */
	.control {
		/* Published as --row-pack, so a multi-part control follows the row onto a phone. */
		--row-pack: flex-end;
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: var(--space-2);
		min-inline-size: 0;
	}

	/* A field takes what its press leaves, so the press stays on the line. */
	.row.beside-field .control > :global(.text-input) {
		flex: 1 1 0;
	}

	/* A held column on the same line, without the fieldset's frame. */
	.held {
		display: flex;
		align-items: inherit;
		justify-content: inherit;
		gap: inherit;
		min-inline-size: 0;
		margin: 0;
		padding: 0;
		border: 0;
	}

	.row.loose .control {
		justify-content: flex-end;
		align-items: baseline;
		text-align: end;
	}

	/* On a phone the control goes under the name, the row's whole width. */
	@media (max-width: 767px) {
		.row,
		.row.wide,
		.row.loose {
			grid-template-columns: minmax(0, 1fr);
			row-gap: var(--space-3);
		}

		.control,
		.row.loose .control {
			--row-pack: flex-start;
			justify-content: flex-start;
			text-align: start;
		}
	}
	/* PathCopy places itself in the gutter of whatever carries this class. */
	.path-host {
		position: relative;
	}
</style>
