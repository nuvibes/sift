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
	 *
	 * The unit list's rows are `ContextMenuItem`, the app's menu row (bits-ui re-exports one
	 * `menu-item` to dropdowns and right-click menus alike), so they get the shared inset, corner
	 * and highlighted ground.
	 *
	 * WHY NOT BITS-UI: bits-ui has no number field. The behaviour here is the site's <input
	 * type=number>, kept whole; what is replaced is the operating system's stepper arrows, drawn in
	 * the OS's own look at a size the page has no say over.
	 *
	 * A number box that wears the app's look instead of the browser's: keep the behaviour, draw the
	 * control. The field is `type="text"` with `inputmode="numeric"`, which asks a phone for the
	 * number pad without asking a desktop browser for the arrows, and the arrow keys are handled
	 * here so stepping still works.
	 *
	 * The value is committed on blur and on Enter rather than every keystroke: saving as you type
	 * would save the "1" in "10" first, and a minimum of 5 would refuse it.
	 *
	 * The unit is attached, inside the field's edge behind a divider, one control with two parts
	 * like a button group.
	 *
	 * The box is as wide as the widest thing it can hold and no wider: the digits of its maximum,
	 * the word standing in for zero, its placeholder, and its unit. Those are drawn unseen in the
	 * box's own cell (the way `Select` sizes to its widest answer), so the browser measures the
	 * words rather than a guess in characters, and the width holds whatever is typed. A column of
	 * these ends on one right edge because the row packs every control to its far side.
	 *
	 * Where a caller hands a ladder instead of a word, the unit becomes a small menu and the reader
	 * chooses what they are reading (5000 KB/s or 5 MB/s, 90 seconds or 1.5 minutes). The value is
	 * unchanged: `$lib/shell/units` converts on the way in and out, and `onchange` always carries the
	 * canonical whole number the caller declared. See that file for why the chosen unit is not
	 * remembered.
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
		/**
		 * The units this number may be READ in, largest last, canonical first.
		 *
		 * Two or more rungs turns the unit into a menu. One or none leaves it as the plain word
		 * `unit` already drew, so a caller with nothing to convert passes nothing and is unaffected.
		 */
		units?: Rung[];
		/**
		 * What ZERO is called, where a bare 0 would be misread.
		 *
		 * A count of jobs reading `0` says the opposite of what it means (Sift choosing for you), and
		 * "Skip files smaller than 0 MB" reads as a size when it means no minimum at all. With this
		 * the box shows the word instead, clearing the box means it, and typing a number is still the
		 * way to set one. Nothing about the stored value changes: it is still the number 0.
		 */
		automatic?: string;
		/**
		 * Digits the box has room for, where the caller knows better than the maximum does: a box
		 * with no maximum, or one whose maximum is not what anybody types.
		 */
		width?: number;
		/** Placeholder, for a box that appears empty and asks for something. */
		placeholder?: string;
		/** Name it where there is no visible label: a box that appeared in place of a control. */
		label?: string;
		/**
		 * Take the caret the moment it appears.
		 *
		 * For a box that was ASKED for and is the only thing on screen that can be typed into:
		 * the pager's jump box, which replaces the position it changes. Never for a box that is
		 * simply on the page: moving somebody's caret because a screen loaded is a screen that
		 * fights whatever they were doing.
		 */
		autofocus?: boolean;
		/** Told after the box is left and its value committed, for a caller that shows it on demand
		 *  and has to put the control back. */
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

	/* Which rung of the ladder the reader is on.
	 *
	 * Chosen from the value the first time and then theirs. `$state` rather than `$derived`, because
	 * a derived one would snap back the instant they typed a number that reads better on another
	 * rung: choose MB/s, type 400, and watch the menu jump to KB/s underneath your hands. */
	/* `untrack`, and it says what the line means rather than silencing a warning.
	 *
	 * Reading a prop once at setup is exactly what is wanted here (the opening rung is a decision
	 * taken when the field appears and then the reader's) and Svelte cannot tell that from the
	 * mistake it looks like, so it warns. A setting's declared unit never changes under a mounted
	 * row anyway: a row is keyed by its setting's key, so a different setting is a different
	 * component. */
	const opening = untrack(() => (units.length > 1 ? openingRung(units) : undefined));
	let rung = $state<Rung | undefined>(opening);

	/** A whole number in the canonical unit, or the reading on the chosen rung. */
	const shownValue = $derived(rung ? toRung(value, rung) : value);

	/*
	 * What is in the box while it is being typed in. It follows the saved value until somebody
	 * starts editing, so a value changed elsewhere still shows up here.
	 *
	 * Read through a derived rather than off the prop: state initialised from a prop captures its
	 * first value and never hears about a change, so a value saved elsewhere, or rolled back after
	 * a refusal, would leave the box showing the previous one.
	 */
	const saved = $derived(String(shownValue));

	let typed = $state<string | null>(null);
	let editing = $state(false);

	/** What the box shows: what is being typed, or the saved value when nothing is. */
	const draft = $derived(typed ?? saved);

	/* The word standing in for zero, and only while nobody is typing into the box.
	 *
	 * Shown as the input's own value rather than as a placeholder, because a placeholder is drawn
	 * in the muted ink a field uses for "nothing here yet", and this IS the value. Clicking in
	 * clears it, so what somebody types replaces the word rather than being appended to it. */
	const readingIsAutomatic = $derived(
		Boolean(automatic) && !editing && typed === null && value === 0
	);
	const shownText = $derived(readingIsAutomatic ? (automatic as string) : draft);

	/*
	 * Digits of room, from the largest number this box allows, read on every rung it offers, so
	 * choosing another unit does not move the box's edge. One more than the widest reading, for
	 * the caret. With no maximum, room for four digits or the value, whichever is more.
	 */
	const digits = $derived.by(() => {
		if (width !== undefined) return width;
		if (max === undefined) return Math.max(4, String(value).length);
		const readings = rung ? units.map((one) => toRung(max, one)) : [max];
		return Math.max(3, ...readings.map((one) => String(one).length + 1));
	});

	/* What the box is measured against, drawn unseen in its cell. Figures are all one width, so a
	   run of zeros stands for any number of that many digits. */
	const sizes = $derived(
		[
			{ words: '0'.repeat(digits), figures: true },
			...[automatic, placeholder]
				.filter((one): one is string => Boolean(one))
				.map((words) => ({ words, figures: false }))
		].map((one, at) => ({ ...one, at }))
	);

	/** Whether the trailing cell exists at all. */
	const hasTrailer = $derived(Boolean(rung) || Boolean(unit));

	/* Characters of room in the trailing cell: the longest unit it can show, so the divider does
	   not move when another is chosen. */
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
		/* An emptied box, where zero has a name, is somebody CHOOSING that name. It is the only way
		   back to it once a number has been typed, and it is what the help says to do. */
		if (draft.trim() === '' && automatic) {
			typed = null;
			if (value !== 0) onchange(0);
			return;
		}
		/*
		 * An empty or unreadable box is somebody who cleared it and walked away, not a request to
		 * save zero: put the saved value back and say nothing.
		 */
		if (draft.trim() === '' || !Number.isFinite(parsed)) {
			typed = null;
			return;
		}
		/* Rounded on a scaled rung, truncated on the canonical one.
		 *
		 * They are not the same choice. On the canonical rung the digits typed ARE the value and
		 * truncating is what stops "10.9" saving as 11 behind somebody's back. On a scaled rung the
		 * digits are a reading (1.5 MB/s IS 1500 KB/s) and truncating there would throw away the
		 * half the reader deliberately typed. */
		const held = clamp(rung ? fromRung(parsed, rung) : Math.trunc(parsed));
		typed = null;
		if (held !== value) onchange(held);
	}

	/* One press of an arrow moves the box by one of whatever it is READING, not by one canonical
	   unit: an arrow on a field showing 5 MB/s that moved it to 5.001 would be a broken arrow. */
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
	     does replaced the control being typed into with this box, so it is the only thing on screen
	     that can now take a keystroke. -->
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
	<!-- The box's width: each thing it can show, in the box's own face and padding, stacked in
	     its cell and never seen, read out or pressed. The input takes the widest. -->
	{#each sizes as one (one.at)}
		<span class="sizer" class:figures={one.figures} aria-hidden="true" data-words={one.words}
		></span>
	{/each}
	{#if rung}
		<!-- The trailing half of a split control: the box is the value, this is what the value is
		     IN. Same shape as the three-dot door on a row (bits-ui's menu, the app's one menu
		     surface) so there is no second kind of dropdown in the application. -->
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
	<!-- The rows scroll, on the ceiling `ContextMenu` puts on every floating menu. A
		     ladder is two or three rungs, so it will not reach it: what matters is that
		     this menu is built like the others rather than nearly like them. -->
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
	/*
	 * The unit sits INSIDE the field's edge, over its own trailing padding.
	 *
	 * Absolutely placed rather than laid out beside the input, and that is not a shortcut: it is
	 * what keeps this from being a second field box. Drawing an outer shell with a border, a corner,
	 * a ground and a face and then stripping those off the input would restate the box `app.css`
	 * gives every field in the application, which is precisely what `check_one_field.js` counts and
	 * refuses. One box, one rule, and the unit rides in its padding.
	 */
	/* One grid cell holds the input and its unseen sizers, so the widest sizer is the box's width
	   and the input fills it. `size=1` takes away the input's own default width, which would
	   otherwise decide instead. */
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

	/* The sizer is the box's width, so it sets its words at the size the box shows them: a phone
	   sets every field at its own size (`--field-text-phone`, the floor in `app.css`), and a sizer
	   left at the body size would measure "No minimum" smaller than the box draws it, cutting the
	   word short in every number box on a phone. */
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

	/* Only what a NUMBER field needs beyond the field every screen gets. The box (the height,
	   the padding, the edge, the corner, the ground and the face) is `app.css`'s, so the number
	   input looks like the fields beside it. */
	input {
		/* Numbers in a column read as a column only if their digits are the same width. */
		font-variant-numeric: tabular-nums;
		text-align: end;
	}

	/* The box's hover, focus, disabled strength and error edge are `app.css`'s, as every text box
	   takes them. Only the pointer here is its own. */
	input:disabled {
		cursor: default;
	}

	/* A word, not a number, so it drops the tabular figures: those space letters out as though
	   each were a digit and "Automatic" comes out looking typeset by a machine. */
	input.automatic {
		font-variant-numeric: normal;
		text-align: start;
	}

	/* While the box reads the word for zero, the unit cell is empty, and a divider in front of an
	   empty cell draws a second box with nothing in it. So the word starts where a chooser's answer
	   starts and the door is only its chevron, the way a chooser reads when its answer is short. */
	.number.reads-automatic :global(button.unit-door) {
		border-inline-start-color: transparent;
	}

	/* A unit that is only a word, and the empty cell a column asks for. Same box as the door below
	   so the two never disagree about where the divider is. */
	/* While the box shows the word for zero, the unit says nothing: "No minimum MB" is not a
	   sentence. The cell keeps its width, so the box's edge does not move when a number arrives. */
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

	/*
	 * The unit door. Global because the class goes onto bits-ui's own element, which is this
	 * component's child and nobody else's: the same reach `RowMenu` documents for `button.more`.
	 *
	 * It is deliberately quieter than a Button: it sits INSIDE a field, and a second full-weight
	 * control there would read as two things to press rather than one field with a unit on it.
	 */
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
		/* The one line that says "this half is pressable". Only the MENU gets it: a static word and
		   an empty reserved cell are not things to press, and a divider in front of either would be
		   promising a control that is not there. */
		border-inline-start: 1px solid var(--sift-line);
		border-radius: 0 var(--radius-md) var(--radius-md) 0;
		background: none;
		/* The chevron wears the door's ink, the ink every chooser's chevron wears; only the word
		   is quiet. */
		color: var(--foreground);
		font: var(--text-body-sm);
		cursor: pointer;
		transition:
			background var(--dur-instant) var(--ease),
			color var(--dur-instant) var(--ease);
	}

	/* A finger's height on a phone. The door stands inside the field's 1px edge, so it is 42px in a
	   44px field and the press 1px from its top or foot would go to the field instead. Its ring reaches
	   over that edge; the field's own press is everything to the left of the door. */
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

	/* Inside the field's own edge, so the ring goes INSIDE too: offset outward it would be drawn
	   on top of the field's border and read as the whole field being focused. */
	:global(button.unit-door:disabled) {
		opacity: var(--disabled-opacity);
		cursor: default;
	}
</style>
