<script lang="ts">
	/*
	 * A row that does something, rather than one that holds a value.
	 *
	 * About half of Settings is not settings. Accounts is a list of people, Maintenance is buttons
	 * that do things, Backup is a schedule and a Save now, Privacy is a PIN. A `SettingRow` does not
	 * fit any of them (there is no value to bind and no metadata to derive a control from), so
	 * they get this instead, and the two together are why the panes line up with each other.
	 *
	 * Same geometry as a setting row on purpose: name and help on the left, the thing you press in
	 * the same right-hand column. A button that sat somewhere else would be the visible seam between
	 * the parts of Settings that are settings and the parts that are not.
	 */
	import type { Snippet } from 'svelte';
	import { Button, SplitButton } from '$lib/components/common';
	import { actGlyph } from '$lib/design/button-glyphs';
	import type { IconName } from '$lib/design/icons';
	import PathCopy from './PathCopy.svelte';
	import { paneHeld } from './read-only';

	interface Props {
		label: string;
		help?: string;
		/** What the button says. A verb: what pressing it does, not what the row is about. */
		action: string;
		/**
		 * The button's accessible name, where its visible verb repeats down a list.
		 *
		 * A row of buttons each called "Edit" to a screen reader, one per folder, is a list of
		 * identical names with nothing to tell them apart, which is the same thing as no names. The
		 * row's own label sits beside the button on screen and is not part of its name. Give the verb
		 * AND what it acts on ("Edit 2024", for a folder of that name); the visible word stays inside
		 * the name, so what is heard and what is seen agree. Plain buttons only: a split button names
		 * its own halves.
		 */
		actionLabel?: string;
		onclick: () => void;
		disabled?: boolean;
		/**
		 * Marks a button that removes something. This is the one way a settings pane draws a
		 * destructive act: the name and its sentence on the left, a quiet danger button on the
		 * right, and a confirm naming the consequence behind the press.
		 */
		destructive?: boolean;
		busy?: boolean;
		/**
		 * The glyph for a verb that is not an act. An act's glyph follows the verb (`actGlyph`), so
		 * Delete wears the bin and Edit the pencil on every row whoever draws it.
		 */
		icon?: IconName;
		/** A figure shown beside the button: how many, how much. Right-aligned and tabular, so a
		 *  column of them reads as a column. */
		note?: string;
		/**
		 * A second, adjacent act joined to the button's end: "now", and "tonight".
		 *
		 * Given, the button is a split one: the main half does `onclick` and the trailing half does
		 * `ontrailing`. Absent, the row is a plain button. The trailing half is
		 * icon-only by the split button's own rule, so `trailingLabel` is what names it.
		 */
		trailingIcon?: IconName;
		trailingLabel?: string;
		ontrailing?: () => void;
		/** A menu behind the trailing half, for a row whose second act is several (a library's Open at start, Delete). */
		trailingMenu?: Snippet;
		/** The act alone is off (the library is already open) while the trailing menu stays live. `disabled` takes the whole row. */
		actDisabled?: boolean;
		/**
		 * A line under the help that is about the row's STATE rather than what it does: files a
		 * product gave up on, with a way to try them again. Drawn only when given, so the ordinary
		 * row costs nothing.
		 */
		children?: Snippet;
		/**
		 * A name for this row, so the settings search can point at it.
		 *
		 * A search result opens the section it is on and then rings the row it names. See
		 * `settings-anchor`. A row drawn from the registry carries its key here already; these are
		 * the hand-written ones, which without it would have nothing to ring and would land the
		 * reader at the top of a pane with nothing pointed at. `SettingGroup` takes one for the same reason.
		 */
		id?: string;
	}

	let {
		id,
		label,
		help,
		action,
		actionLabel,
		onclick,
		disabled = false,
		destructive = false,
		busy = false,
		icon,
		note,
		trailingIcon,
		trailingLabel,
		ontrailing,
		children,
		trailingMenu,
		actDisabled = false
	}: Props = $props();

	const glyph = $derived(actGlyph(action, icon));

	/* On a pane held read only (a pane about the computer Sift runs on, on a phone) the row keeps
	   its name, its help and its figure and draws no press: a press that cannot be pressed says
	   nothing the pane's own note does not. See `read-only.ts`. */
	const held = paneHeld();
</script>

<div class="row ruled-row" {id}>
	<div class="named path-host">
		<PathCopy name={label} />
		<span class="name">{label}</span>
		{#if help}<p class="help">{help}</p>{/if}
		{#if children}<div class="foot">{@render children()}</div>{/if}
	</div>
	<!-- Held with no figure, the row has nothing for its control column: none is drawn, so the row
	     does not keep an empty line where its press was. -->
	{#if !held() || note}<div class="control">
			{#if note}<span class="note">{note}</span>{/if}
			<!-- Placed in the second column BY NAME rather than by being the second child. The
		     figure is optional, so on a row without one the button would be the FIRST grid item
		     and land in the figure's stretchy column, ending short of the edge every setting
		     row on the pane ends at, which is precisely the seam this file exists to remove. -->
			{#if !held()}<div class="press">
					{#if trailingIcon && trailingLabel}
						<SplitButton
							tone={destructive ? 'danger-quiet' : 'secondary'}
							size="small"
							icon={glyph}
							disabled={disabled || busy}
							leadDisabled={actDisabled}
							aria-busy={busy}
							{onclick}
							{trailingIcon}
							{trailingLabel}
							{ontrailing}
							menu={trailingMenu}
						>
							{action}
						</SplitButton>
					{:else}
						<Button
							tone={destructive ? 'danger-quiet' : 'secondary'}
							size="small"
							icon={glyph}
							disabled={disabled || busy}
							aria-busy={busy}
							aria-label={actionLabel}
							{onclick}
						>
							{action}
						</Button>
					{/if}
				</div>{/if}
		</div>{/if}
</div>

<style>
	/* The same grid as a setting row, and the same right edge: the two are read as one list. The
	   control column is the setting row's width at least and grows to hold a long figure and the
	   press on one line, so a figure never pushes the press under it; every control still ends on
	   the one edge. */
	.row {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(var(--settings-control-col, 15rem), max-content);
		align-items: start;
		column-gap: var(--space-6);
		padding: var(--space-4) 0;
	}

	/* The line between rows, by the rule `LabelledRow` states: the lower row draws it, and only
	   under another row, so a group's last row draws none. */
	:global(:is(.ruled-row, :has(> .ruled-row))) + .row,
	:global(:is(.ruled-row, :has(> .ruled-row)) + *) > .row:first-child {
		border-block-start: 1px solid var(--sift-line);
	}

	.named {
		position: relative;
		min-width: 0;
	}

	.name {
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

	/* Under the help, in the same measure: a fact about the row and the one thing to do about it. */
	.foot {
		margin: var(--space-2) 0 0;
		max-width: var(--reading-measure);
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
		line-height: 1.5;
	}

	/* The figure and the button each get their own share of the column, so a count sits in one place
	   down the list rather than wherever the button beside it happens to start. */
	.control {
		/* One line: the figure, then the press at the column's end. The column grows to hold both
		   (see `.row`), so a long figure (how many files, and how much it frees) sits beside its press
		   as a short one does. Only where the window leaves no room does the figure wrap, between
		   its words and inside its own share, and the press keeps its place on the line. */
		--row-pack: flex-end;
		display: flex;
		flex-wrap: nowrap;
		justify-content: flex-end;
		align-items: center;
		gap: var(--space-3);
		min-height: 2rem;
	}

	/* The press never shrinks and ends at the column's edge whether or not a figure precedes it.
	   Where the figure has less room than it needs, it wraps (see `.note`) rather than running
	   under the press: every one of these is a number, and a number on two lines reads where half
	   a number with a fade on the end does not. */
	.press {
		flex: 0 0 auto;
	}

	.note {
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
		font-variant-numeric: tabular-nums;
		text-align: end;
		/* Breaks between words, and inside one only when a single word cannot fit the cell.
		   `anywhere` is right for one long number and wrong for a list of counts: "16,000
		   thumbnails, 18,000 previews" would come out as "thumbnail" and "s" on two lines. */
		overflow-wrap: break-word;
		min-width: 0;
		flex: 0 1 auto;
	}

	/* At a phone's width the press goes under the name, as on every settings row: see
	   `LabelledRow`, whose grid this one keeps. */
	@media (max-width: 767px) {
		.row {
			grid-template-columns: minmax(0, 1fr);
			row-gap: var(--space-3);
		}

		.control {
			--row-pack: flex-start;
			justify-content: flex-start;
		}

		.note {
			text-align: start;
		}
	}
	/* The marker the copy press stands against: PathCopy places itself in the gutter of whatever
	   carries this class, so the class itself is what makes that place. */
	.path-host {
		position: relative;
	}
</style>
