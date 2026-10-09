<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'NumberInput',
		category: 'control',
		role: 'a number and its unit, with the unit picked from a short list',
		basis: 'site:<input type=number>; bits-ui:DropdownMenu',
		states: ['default', 'with a unit', 'disabled']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/*
	 * DRESSED BY: .ui-menu (ContextMenu styles the surface every menu in the app is drawn on; the
	 * unit door opens that same one, so there is one rule for every menu. Same borrowing `RowMenu`
	 * declares.)
	 * WHY NOT BITS-UI: bits-ui has no number field. The behaviour here is the site's <input
	 * type=number>, kept whole; the OS's stepper arrows are replaced.
	 * A `type="text"` `inputmode="numeric"` box, committed on blur and Enter, as wide as its widest
	 * reading; a ladder of units turns the unit into a menu (`$lib/shell/units`).
	 */
	import { untrack } from 'svelte';
	import { DropdownMenu } from 'bits-ui';
	import { phoneWidth } from './phone-width.svelte';
	import { sheetPresses } from './menu-touch';
	import { strokes } from '$lib/components/player/swipe';
	import ContextMenuItem from './ContextMenuItem.svelte';
	import Scroller from './Scroller.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { CHOOSER_CHEVRON } from './Select.svelte';
	import { fromRung, openingRung, toRung, type Rung } from '$lib/shell/units';
	import PageShield from './PageShield.svelte';

	interface Props {
		value: number;
		id?: string;
		min?: number;
		max?: number;
		step?: number;
		disabled?: boolean;
		describedBy?: string;
		/** Shown after the box: GB, minutes, p. Never part of the value. */
		unit?: string;
		/** The units it may be read in, canonical first; two or more make a menu. */
		units?: Rung[];
		/** What zero is called where a bare 0 misleads; clearing the box chooses it. */
		automatic?: string;
		/** Digits of room, where the maximum does not say. */
		width?: number;
		/** Placeholder, for a box that appears empty and asks for something. */
		placeholder?: string;
		/** Name it where there is no visible label: a box that appeared in place of a control. */
		label?: string;
		/** Take the caret on appearing, only for a box that was asked for (the pager's jump). */
		autofocus?: boolean;
		/** Told after the box is left and committed. */
		onblur?: () => void;
		onchange: (value: number) => void;
	}

	/** Whether the unit menu is open, for the sheet under it. */
	let unitOpen = $state(false);

	/* The tap that opens a phone's sheet of units must not also choose one (`menu-touch.ts`). */
	const unitSheet = sheetPresses();
	$effect(() => {
		if (!unitOpen) unitSheet.reset();
	});

	let {
		value,
		id,
		min,
		max,
		step = 1,
		disabled = false,
		describedBy,
		unit,
		units = [],
		automatic,
		width,
		placeholder,
		label,
		autofocus = false,
		onblur,
		onchange
	}: Props = $props();

	/* The reader's rung, chosen from the value once and then theirs; untracked, read at setup. */
	const opening = untrack(() => (units.length > 1 ? openingRung(units) : undefined));
	let rung = $state<Rung | undefined>(opening);

	/** A whole number in the canonical unit, or the reading on the chosen rung. */
	const shownValue = $derived(rung ? toRung(value, rung) : value);

	/* The box's text follows the saved value until edited; derived, so a rollback shows. */
	const saved = $derived(String(shownValue));

	let typed = $state<string | null>(null);
	let editing = $state(false);

	/** What the box shows: what is being typed, or the saved value when nothing is. */
	const draft = $derived(typed ?? saved);

	/* The word for zero as the value itself, while nobody types. */
	const readingIsAutomatic = $derived(
		Boolean(automatic) && !editing && typed === null && value === 0
	);
	const shownText = $derived(readingIsAutomatic ? (automatic as string) : draft);

	/* Digits of room from the maximum on every rung, so a unit change does not move the edge. */
	const digits = $derived.by(() => {
		if (width !== undefined) return width;
		if (max === undefined) return Math.max(4, String(value).length);
		const readings = rung ? units.map((one) => toRung(max, one)) : [max];
		return Math.max(3, ...readings.map((one) => String(one).length + 1));
	});

	/* Drawn unseen to measure the box; zeros stand for any digits. */
	const sizes = $derived(
		[
			{ words: '0'.repeat(digits), figures: true },
			...[automatic, placeholder]
				.filter((one): one is string => Boolean(one))
				.map((words) => ({ words, figures: false }))
		].map((one, at) => ({ ...one, at }))
	);

	const hasTrailer = $derived(Boolean(rung) || Boolean(unit));

	/* The trailing cell's room: its longest unit. */
	const unitChars = $derived(
		rung ? Math.max(...units.map((one) => one.unit.length)) : (unit?.length ?? 0)
	);

	function clamp(next: number): number {
		let held = next;
		if (min !== undefined) held = Math.max(min, held);
		if (max !== undefined) held = Math.min(max, held);
		return held;
	}

	function commit(): void {
		editing = false;
		commitValue();
		onblur?.();
	}

	function commitValue(): void {
		const parsed = Number(draft.trim());
		/* An emptied box, where zero has a name, chooses that name. */
		if (draft.trim() === '' && automatic) {
			typed = null;
			if (value !== 0) onchange(0);
			return;
		}
		/* An empty or unreadable box puts the saved value back. */
		if (draft.trim() === '' || !Number.isFinite(parsed)) {
			typed = null;
			return;
		}
		/* Rounded on a scaled rung, truncated on the canonical one, so "10.9" never saves as 11. */
		const held = clamp(rung ? fromRung(parsed, rung) : Math.trunc(parsed));
		typed = null;
		if (held !== value) onchange(held);
	}

	/* An arrow steps one of what the box reads, not one canonical unit. */
	function step_by(direction: 1 | -1): void {
		const from = Number(draft.trim());
		const base = Number.isFinite(from) ? from : shownValue;
		const stepped = base + direction * step;
		const held = clamp(rung ? fromRung(stepped, rung) : Math.trunc(stepped));
		typed = null;
		editing = false;
		if (held !== value) onchange(held);
	}

	function onkeydown(event: KeyboardEvent): void {
		if (event.key === 'ArrowUp') {
			event.preventDefault();
			step_by(1);
		} else if (event.key === 'ArrowDown') {
			event.preventDefault();
			step_by(-1);
		} else if (event.key === 'Enter') {
			event.preventDefault();
			commit();
		}
	}
</script>

<span
	class="number"
	class:trailered={hasTrailer}
	class:menu={Boolean(rung)}
	class:reads-automatic={readingIsAutomatic}
	style:--unit-chars={unitChars}
	style:--chevron="{CHOOSER_CHEVRON}px"
>
	<!-- svelte-ignore a11y_autofocus: off unless a caller says otherwise, and the one caller that
	does replaced the control being typed into with this box. -->
	<input
		{id}
		type="text"
		inputmode="numeric"
		autocomplete="off"
		spellcheck="false"
		class:automatic={readingIsAutomatic}
		value={shownText}
		{disabled}
		placeholder={automatic ?? placeholder}
		{autofocus}
		size={1}
		aria-label={label}
		aria-describedby={describedBy}
		role="spinbutton"
		aria-valuenow={value}
		aria-valuemin={min}
		aria-valuemax={max}
		oninput={(event) => {
			editing = true;
			typed = event.currentTarget.value;
		}}
		onfocus={(event) => {
			/* The word is not something to edit around. Focusing clears it, so the first keystroke
			   is the whole of the number and nobody has to select "Automatic" first. */
			/* A box that was asked for (`autofocus`) opens on the number it replaces, selected, so
			   the first keystroke starts a new number instead of adding a digit to the old one:
			   typing 5 into the pager's box on page 2 goes to page 5, not 25. */
			if (autofocus && !readingIsAutomatic) event.currentTarget.select();
			if (!readingIsAutomatic) return;
			editing = true;
			typed = '';
			event.currentTarget.value = '';
		}}
		onblur={commit}
		{onkeydown}
	/>
	<!-- The box's width: each thing it can show, unseen. -->
	{#each sizes as one (one.at)}
		<span class="sizer" class:figures={one.figures} aria-hidden="true" data-words={one.words}
		></span>
	{/each}
	{#if rung}
		<!-- The unit menu: the app's one menu surface. -->
		<DropdownMenu.Root bind:open={unitOpen}>
			<DropdownMenu.Trigger class="unit-door" {disabled} aria-label="Unit for this number">
				<span class="unit-word" class:silent={readingIsAutomatic}>{rung.unit}</span>
				<Icon name="expand_more" size={CHOOSER_CHEVRON} />
			</DropdownMenu.Trigger>
			<DropdownMenu.Portal>
				<PageShield up={unitOpen} />
				{#if phoneWidth.yes}
					<!-- A sheet from the foot at a phone's width, as every menu is there.
					     DRESSED BY: .menu-sheet (ContextMenu styles the sheet every menu is at a phone width)
					     DRESSED BY: .menu-sheet-head (ContextMenu styles the sheet's head beside the sheet) -->
					<DropdownMenu.ContentStatic
						class="ui-menu menu-sheet"
						aria-label="Unit for this number"
						onpointerdowncapture={unitSheet.down}
						onpointerupcapture={unitSheet.up}
						onclickcapture={unitSheet.click}
					>
						<p
							class="menu-sheet-head"
							aria-hidden="true"
							{@attach strokes(() => ({ live: unitOpen, on: { down: () => (unitOpen = false) } }))}
						>
							Unit
						</p>
						{@render unitRows()}
					</DropdownMenu.ContentStatic>
				{:else}
					<DropdownMenu.Content class="ui-menu" aria-label="Unit for this number">
						{@render unitRows()}
					</DropdownMenu.Content>
				{/if}
			</DropdownMenu.Portal>
		</DropdownMenu.Root>
	{:else if unit}
		<span class="unit" class:silent={readingIsAutomatic}>{unit}</span>
	{/if}
</span>

<!-- The units, in the floating menu or a phone's sheet. -->
{#snippet unitRows()}
	<!-- The rows scroll, as every menu's do. -->
	<Scroller arrows>
		{#each units as one (one.unit)}
			<ContextMenuItem
				label={one.unit}
				onselect={() => {
					/* The stored value does not move. Only what it is read as does, so a
						   reader switching units is not making an edit and nothing is saved. */
					typed = null;
					editing = false;
					rung = one;
				}}
			/>
		{/each}
	</Scroller>
{/snippet}

<style>
	/* The unit rides inside the field's padding rather than a second box (`check_one_field.js`);
	 * one grid cell holds the input and its sizers, `size=1` dropping the input's own width. */
	.number {
		position: relative;
		display: inline-grid;
		grid-template-columns: auto;
		align-items: center;
		max-inline-size: 100%;
		/* The trailing cell: its longest unit, plus the chevron where the unit is a menu. */
		--unit-cell: calc(var(--unit-chars) * 1ch + 2 * var(--space-2));
	}

	/* The unit, the chooser's chevron and the door's own inset. */
	.number.menu {
		--unit-cell: calc(var(--unit-chars) * 1ch + var(--chevron) + var(--space-3));
	}

	.number input,
	.sizer {
		grid-area: 1 / 1;
		min-inline-size: 0;
	}

	.number input {
		inline-size: 100%;
	}

	/* The input's face and horizontal box, with no ground and no edge anyone sees. */
	.sizer {
		visibility: hidden;
		pointer-events: none;
		overflow: hidden;
		white-space: nowrap;
		block-size: 0;
		padding-inline: var(--space-3);
		border-inline: 1px solid transparent;
		font: var(--text-body);
	}

	.sizer::before {
		content: attr(data-words);
	}

	/* The sizer at the phone field size, or it measures words short. */
	@media (max-width: 767px) {
		.sizer {
			font-size: var(--field-text-phone);
		}
	}

	.sizer.figures {
		font-variant-numeric: tabular-nums;
	}

	/* The digits stop where the unit begins. Without this the number runs under the door. */
	.number.trailered input,
	.number.trailered .sizer {
		padding-inline-end: calc(var(--unit-cell) + var(--space-2));
	}

	/* Only what a number field adds; the box is app.css's. */
	input {
		/* Numbers in a column read as a column only if their digits are the same width. */
		font-variant-numeric: tabular-nums;
		text-align: end;
	}

	/* The states are app.css's; only the pointer is its own. */
	input:disabled {
		cursor: default;
	}

	/* A word, so no tabular figures. */
	input.automatic {
		font-variant-numeric: normal;
		text-align: start;
	}

	/* Reading the word for zero, the door is only its chevron. */
	.number.reads-automatic :global(button.unit-door) {
		border-inline-start-color: transparent;
	}

	/* While zero is a word the unit is silent; the cell keeps its width. */
	.silent {
		visibility: hidden;
	}

	.unit {
		position: absolute;
		inset-block: 1px;
		inset-inline-end: 1px;
		inline-size: var(--unit-cell);
		display: inline-flex;
		align-items: center;
		justify-content: center;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
		pointer-events: none;
	}

	/* The unit door, quieter than a Button inside a field; global, on bits-ui's element. */
	:global(button.unit-door) {
		position: absolute;
		inset-block: 1px;
		inset-inline-end: 1px;
		inline-size: var(--unit-cell);
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 2px;
		padding: 0;
		border: 0;
		/* The divider only on the menu, the one pressable half. */
		border-inline-start: 1px solid var(--sift-line);
		border-radius: 0 var(--radius-md) var(--radius-md) 0;
		background: none;
		/* The chevron in the door's ink; only the word is quiet. */
		color: var(--foreground);
		font: var(--text-body-sm);
		cursor: pointer;
		transition:
			background var(--dur-instant) var(--ease),
			color var(--dur-instant) var(--ease);
	}

	/* A finger's height on a phone, inside the field's 1px edge. */
	@media (max-width: 767px) {
		:global(button.unit-door)::after {
			content: '';
			position: absolute;
			inset-block: -1px;
			inset-inline: 0 -1px;
		}
	}

	/* The unit's word, quieter than the number it qualifies, until the door is pointed at. */
	.unit-word {
		color: var(--sift-ink-3);
		transition: color var(--dur-instant) var(--ease);
	}

	:global(button.unit-door:hover:not(:disabled)) .unit-word,
	:global(button.unit-door[data-state='open']) .unit-word {
		color: inherit;
	}

	/* The state layer (see `--layer-hover`) over the field's own ground, which shows through. */
	:global(button.unit-door:hover:not(:disabled)) {
		color: var(--hover-ink);
		background: color-mix(in srgb, currentColor var(--layer-hover), transparent);
	}

	/* While its list of units is open, the stronger layer: the door says it is holding a list open. */
	:global(button.unit-door[data-state='open']) {
		color: var(--hover-ink);
		background: color-mix(in srgb, currentColor var(--layer-pressed), transparent);
	}

	/* The ring goes inside the field's edge. */
	:global(button.unit-door:disabled) {
		opacity: var(--disabled-opacity);
		cursor: default;
	}
</style>
