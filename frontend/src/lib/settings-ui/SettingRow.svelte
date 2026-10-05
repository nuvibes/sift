<script lang="ts">
	/* DRESSED BY: .name (LabelledRow styles the label snippet its caller writes). The weight and the
	   ink that separate a row's name from its help are the row component's, for every caller. */

	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	/*
	 * One setting, one row: its name on the left, its control on the right, on the same line.
	 *
	 * There is one place a row is defined, so spacing, control width and alignment agree on every
	 * pane and there is one place to fix them. Three rules live here rather than in thirteen panes:
	 *
	 *   The control follows from the setting's own metadata. See `control.ts`: a number cannot be
	 *   drawn as a switch, because nothing draws a switch by hand.
	 *
	 *   Help sits under the name, inside the name's column, one size down and capped at a readable
	 *   measure. Full-bleed help makes the sentence under one setting read as the heading of the
	 *   next.
	 *
	 *   Every control sits in ONE right-hand column of a fixed width, so they line up down the page
	 *   whatever they are. Nothing spans the pane.
	 *
	 * Saving is the caller's. This reports a new value and shows what it is told to show, so a pane
	 * can save a batch, roll one back, or write somewhere that is not the settings endpoint.
	 */
	import {
		NumberInput,
		Select,
		Slider,
		Switch,
		TextInput,
		TimeField
	} from '$lib/components/common';
	import type { SelectOption } from '$lib/components/common';
	import type { Snippet } from 'svelte';
	import { choiceFor, controlFor, optionsFor, PERCENT } from './control';
	import { ladderFor } from '$lib/shell/units';
	import type { SettingEntry } from '$lib/settings-ui/settings';

	interface Props {
		entry: SettingEntry;
		/** The value to show. Held by the pane, so an optimistic write and its rollback are its own. */
		value: unknown;
		onchange: (value: unknown) => void;
		disabled?: boolean;
		/** Off for a row inside a table that carries one sentence of help above all of them. */
		showHelp?: boolean;
		/**
		 * Off where "more about this" is not what the screen is for.
		 *
		 * Separate from `showHelp` because they answer different questions: help is what the setting
		 * DOES, and a disclosure is the detail somebody can ask for. The first-run flow wants neither
		 * (it is a screen for choosing quickly, not for reading), and the settings screen wants
		 * both, which is where a stranger goes when they do want to read.
		 */
		showDisclosure?: boolean;
		/**
		 * A picture beside each answer of a menu row, handed to the chooser's own `preview`.
		 *
		 * For a setting whose answers are shapes rather than words: Theater's default layout is
		 * the Layouts chooser on the wall, drawn the same way.
		 */
		preview?: Snippet<[SelectOption]>;
		/** The words each answer shows when highlighted, as the wall's own chooser does. */
		tooltip?: (value: string) => string | undefined;
	}

	let {
		entry,
		value,
		onchange,
		disabled = false,
		showHelp = true,
		showDisclosure = true,
		preview,
		tooltip
	}: Props = $props();

	const kind = $derived(controlFor(entry));
	const options = $derived(
		optionsFor(entry).map((one) => ({ ...one, tooltip: tooltip?.(one.value) }))
	);
	/* The units this number may be read in, if it is a number and if its unit has any. A setting
	   whose unit has no ladder gets the plain word it always got. */
	const units = $derived(ladderFor(entry.unit));
	const helpId = $derived(`${entry.key}-help`);
	const rowId = $derived(`${entry.key}-control`);

	// A number arrives from an input as a string and is stored as a number. Kept here so every
	// number field in Settings coerces the same way rather than each pane remembering to.
	function asNumber(raw: string): number | null {
		// An empty box is not a zero. `Number('')` is 0, so without this a cleared field saves 0,
		// which on the job count means "let Sift decide" and on a size target means one megabyte.
		if (raw.trim() === '') return null;
		const parsed = Number(raw);
		return Number.isFinite(parsed) ? Math.round(parsed) : null;
	}

	/* A committed number, or the stored one put back.
	 *
	 * The change fires on blur and on the spinner, and an empty or half-typed box (a lone "-", say)
	 * is not a value to send: left alone it would save NaN, and left in the box it would read as
	 * a value that had been accepted. */
	function commitNumber(raw: string, box?: HTMLInputElement) {
		const parsed = asNumber(raw);
		if (parsed === null) {
			if (box) box.value = String(shown ?? '');
			return;
		}
		const low = entry.minimum ?? Number.NEGATIVE_INFINITY;
		const high = entry.maximum ?? Number.POSITIVE_INFINITY;
		onchange(Math.min(high, Math.max(low, parsed)));
	}

	/** What the control shows: the stored value, or the declared default when nothing is stored. */
	const shown = $derived(value ?? entry.default);

	/* Where a slider is while it is being dragged.
	 *
	 * A range input fires `input` continuously (once per pixel of travel), and `change` once, on
	 * release. Saving on `input` would be a request per frame of a drag, so the number beside the
	 * slider follows this while the thumb moves and only the release is written. Cleared when the
	 * value comes back from the caller, so a rejected write puts the reading back with the value.
	 */
	let dragging = $state<number | null>(null);
	const reading = $derived(dragging ?? (typeof shown === 'number' ? shown : 0));

	$effect(() => {
		void value;
		dragging = null;
	});

	/* Off for a row inside a table that carries one sentence of help above all of them, so the
	   sentence is not repeated on every line of it. */
	const help = $derived(showHelp ? entry.help : undefined);
	const disclosure = $derived(showDisclosure ? entry.disclosure : undefined);
</script>

<!--
	The row's shape is `LabelledRow`, shared with the facts on About and Users. What is left here
	is the part that is a SETTING: reading a declared entry, choosing which control it means, and
	handing the value up. See `LabelledRow` for why the two share one shape.
-->
{#snippet rowName()}
	<label class="name" for={rowId}>{entry.label ?? entry.key}</label>
{/snippet}

<!-- The row's address is the setting's own key, so a link anywhere in the app can name the setting
     it is talking about rather than the section it is somewhere inside. -->
<LabelledRow id={entry.key} {help} {helpId} {disclosure} inert={disabled} name={rowName}>
	{#if kind === 'menu'}
		<div class="chooser">
			<Select
				id={rowId}
				value={String(value ?? entry.default ?? '')}
				{options}
				{disabled}
				{preview}
				describedBy={entry.help ? helpId : undefined}
				onValueChange={(next) => {
					/* Back to the declared choice, typed. See `choiceFor`. The menu hands over a
					   string, and a number setting's server refuses one. */
					const choice = choiceFor(entry, next);
					if (choice !== undefined) onchange(choice);
				}}
			/>
		</div>
	{:else if kind === 'toggle'}
		<Switch
			id={rowId}
			checked={value === true}
			{disabled}
			describedBy={entry.help ? helpId : undefined}
			onCheckedChange={(next) => onchange(next)}
		/>
	{:else if kind === 'slider'}
		<div class="slider">
			<!-- The shared slider, which is where the filled part of the track comes from:
			     Chromium draws none by itself, so a setting would be a grey groove with a dot
			     on it and nothing to say how far along it was except the number beside it. -->
			<Slider
				id={rowId}
				class="span"
				label={entry.label ?? entry.key}
				min={entry.minimum ?? 0}
				max={entry.maximum ?? 100}
				value={reading}
				valueText="{reading}{PERCENT}"
				{disabled}
				describedBy={entry.help ? helpId : undefined}
				oninput={(next) => (dragging = next)}
				onchange={(next) => commitNumber(String(next))}
			/>
			<span class="reading">{reading}{PERCENT}</span>
		</div>
	{:else if kind === 'number'}
		<!-- As wide as this setting's own content (the digits of its maximum, its unit, the word
		     it shows for zero) and packed to the column's far edge like every control on the
		     pane: one right edge, never a box mostly empty. See `NumberInput`. -->
		<NumberInput
			id={rowId}
			value={typeof value === 'number' ? value : ((entry.default as number) ?? 0)}
			min={entry.minimum}
			max={entry.maximum}
			unit={entry.unit ?? undefined}
			{units}
			automatic={entry.automatic_label}
			{disabled}
			describedBy={entry.help ? helpId : undefined}
			onchange={(next) => onchange(next)}
		/>
	{:else if kind === 'time'}
		<!-- A clock, not a text box. See `control.ts` for how a setting comes to be one: the quiet
		     hours hold `23:00` and a string is a string, so as free text they would accept `11pm`.
		     The segments are the library's, which is what makes an arrow key step the hour without
		     touching the minute. -->
		<TimeField
			id={rowId}
			value={typeof value === 'string' ? value : ((entry.default as string) ?? '')}
			label={entry.label ?? entry.key}
			{disabled}
			describedBy={entry.help ? helpId : undefined}
			onchange={(next) => onchange(next)}
		/>
	{:else if kind === 'text'}
		<TextInput
			id={rowId}
			type="text"
			value={typeof value === 'string' ? value : (entry.default as string)}
			{disabled}
			describedBy={entry.help ? helpId : undefined}
			onchange={(event) => onchange(event.currentTarget.value)}
		/>
	{:else}
		<!-- Nothing here knows what this holds. Saying so beats drawing the nearest thing, which
			     would make a number a switch that could only fail. -->
		<span class="unknown">Not shown — this setting has no control yet</span>
	{/if}
</LabelledRow>

<style>
	/* A row that hangs off a setting that is switched off. The whole row dims, not just its control:
	   a full-strength name beside a faded box reads as a control that has broken rather than one
	   that is waiting for something. */
	/* A menu is as wide as its widest answer (`Select` measures it) and ends at the column's far
	   edge like every control on the pane: one right edge, never a box mostly empty. It gives way
	   to the column where an answer is longer than the column is wide. */
	/* Named for what it holds, not "menu": a class of that name is dressed app-wide as a POP-UP
	   menu (its own padding, corners, surface and shadow). */
	.chooser {
		display: flex;
		justify-content: var(--row-pack, flex-end);
		min-inline-size: 0;
		max-inline-size: 100%;
	}

	.slider {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		width: 100%;
	}

	/* DRESSED BY: .span (`Slider` draws the track, the fill and the thumb; this file only says that it
	   takes the width the row gives it). */
	.slider :global(.span) {
		min-inline-size: 0;
	}

	.reading {
		min-width: 4ch;
		text-align: right;
		font-variant-numeric: tabular-nums;
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
	}

	/* Only the width. The box (the height, the padding, the edge, the corner, the ground and the
	   face) is `app.css`'s; restated here with a heavier edge, a smaller corner and a different
	   ground, the one text setting on a pane would look like no other field in the application.
	   One rule says what a field looks like; see `check_one_field.js`. */
	.unknown {
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}
</style>
