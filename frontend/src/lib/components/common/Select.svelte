<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Select',
		category: 'control',
		role: 'one value picked from a list, searchable when the list is long',
		basis: 'bits-ui:Select',
		states: ['closed', 'open', 'searchable', 'disabled']
	} satisfies DesignEntry;

	/** The chevron every chooser opens from, one size wherever it is drawn: a select, and the unit
	 *  door on a number (`NumberInput`). Two sizes read as two kinds of control. */
	export const CHOOSER_CHEVRON = 18;
</script>

<script lang="ts">
	/*
	 * A dropdown that wears the app's look instead of the operating system's, since a native <select>
	 * cannot be restyled. Built on the library's Select, so the keyboard behaves as every menu does, and
	 * shaped like a <select> (a bound value, a list of options).
	 */
	import NarrowBox from './NarrowBox.svelte';
	import Scroller from './Scroller.svelte';
	import PageShield from './PageShield.svelte';
	import { phoneWidth } from './phone-width.svelte';
	import { sheetPresses } from './menu-touch';
	import { strokes } from '$lib/components/player/swipe';
	import { Select } from 'bits-ui';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from './Tooltip.svelte';
	import { fromBar } from '$lib/shell/motion.svelte';
	import { untrack, type Snippet } from 'svelte';
	import type { IconName } from '$lib/design/icons';

	export interface SelectOption {
		value: string;
		label: string;
		/** A quieter phrase beside a repeated name, just enough to tell it apart (`disambiguate`). */
		detail?: string;
		/**
		 * A second line under the name, in small letters: what the row is measured against, or why it
		 * is dimmed, as a menu row's `note`. It wraps and never widens the list.
		 */
		note?: string;
		disabled?: boolean;
		/** A press rather than an answer (`Shuffle again`): it fires `onAction`, never `value`. */
		action?: boolean;
		/** Words shown when the row is pointed at, for a label too short to say it (`1x2 (P)`). */
		tooltip?: string;
	}

	interface Props {
		/** The chosen value. Bindable, so `bind:value` works exactly as it did on the native tag. */
		value?: string;
		options: SelectOption[];
		id?: string;
		/** For the one-way callers: told the new value, the way a native `onchange` was. */
		onValueChange?: (value: string) => void;
		/**
		 * An ACTION row was pressed (`action`): its own door, since a caller hearing it as a value
		 * would store it.
		 */
		onAction?: (value: string) => void;
		disabled?: boolean;
		/** Name it when it stands on its own. Inside a Field the label already names it. */
		label?: string;
		/** aria-describedby, for the help text a Field renders beside it. */
		describedBy?: string;
		/** Drawn in the wrong-answer colour when a Field says the value is invalid. */
		invalid?: boolean;
		/** What the trigger says before anything is chosen. */
		placeholder?: string;
		/** An extra class on the trigger, for the rare caller that needs to place it. */
		class?: string;
		/**
		 * Draw the trigger as one icon rather than as the chosen value and a chevron, where what is
		 * chosen is visible on screen (the sort order); `label` still names it for a screen reader.
		 */
		icon?: IconName;
		/**
		 * Where the open list is drawn, when `document.body` is the wrong answer: in fullscreen only the
		 * fullscreen element's subtree is drawn, so the list goes inside it.
		 */
		portalTo?: Element | null;
		/**
		 * Told when the list opens and closes, for a caller that closes on leaving: the portalled list is
		 * not inside it.
		 */
		onOpenChange?: (open: boolean) => void;
		/**
		 * Whether the list is showing, for a caller that OWNS that: the screen bar keeps exactly one of
		 * its menus open.
		 */
		open?: boolean;
		/**
		 * The one element an open list leaves pressable under its sheet: a row of pointed-at triggers,
		 * so sliding along it moves between menus.
		 */
		spare?: HTMLElement | null;
		/**
		 * The pointer entered or left the open LIST, for a caller with a grace period that closes on
		 * leaving: the same pair the screen bar's drawer reports.
		 */
		onListPointer?: (inside: boolean) => void;
		/**
		 * A picture drawn beside each option, where the words are not the answer (Theater's layouts,
		 * drawn from the wall's own `grid-template`). A snippet, since a picture is not always a glyph.
		 */
		preview?: Snippet<[SelectOption]>;
		/**
		 * The option's WORDS, drawn by the caller, where the label must be seen (the appearance pane's
		 * fonts, set in each face). A snippet, since a typeface is keyed on theme attributes only
		 * `app.css` may use. The trigger draws it too, so closed and open agree.
		 */
		optionLabel?: Snippet<[SelectOption]>;
		/**
		 * A box at the top of the open list, for typing a few letters and filtering it. Unset, the list
		 * decides (past `NARROW_PAST` rows). It can only turn the box off (typeahead serves, or a touch
		 * keyboard would cover the list); `true` does nothing.
		 */
		searchable?: boolean;
		/**
		 * Words the closed control is as wide as, besides its own answers, so a column of choosers (each
		 * task's When) keeps one width.
		 */
		sizeTo?: readonly string[];
		/**
		 * A list hanging from the top bar: it comes out from under the bar and goes back up under
		 * it, as the Filter panel beside it does (`fromBar`), rather than rising as a small surface
		 * and vanishing. Sort by and a screen's own menus on that row.
		 */
		fromTheBar?: boolean;
	}

	let {
		value = $bindable(),
		options,
		id,
		onValueChange,
		onAction,
		disabled = false,
		label,
		describedBy,
		invalid = false,
		placeholder,
		class: klass,
		portalTo,
		onOpenChange,
		icon,
		open = $bindable(false),
		spare = null,
		onListPointer,
		preview,
		optionLabel,
		searchable,
		sizeTo = [],
		fromTheBar = false
	}: Props = $props();

	/*
	 * How many rows is too many to read: ten is one glance under the control; past that typing three
	 * letters beats scrolling. One figure for every chooser.
	 */
	const NARROW_PAST = 10;

	/** The trigger element, so a pointer-driven close can take the ring back off it. */
	let trigger = $state<HTMLElement | null>(null);

	/* The sheet a phone opens is headed with whose list it is: the chooser's name or its field's label. */
	const heading = $derived(
		label ?? (trigger as HTMLButtonElement | null)?.labels?.[0]?.textContent?.trim() ?? ''
	);

	/* The tap that opens the sheet must not also choose a row: the menu sheet's rule
	   (`menu-touch.ts`). */
	const sheet = sheetPresses();
	$effect(() => {
		if (!open) sheet.reset();
	});

	/* The option the trigger is showing, found by value since `value` is bindable; undefined when none. */
	const chosen = $derived(options.find((one) => one.value === value));

	/** Whether this open was started by a pointer rather than the keyboard. */
	let pointerDriven = $state(false);

	/** Whether the keyboard moved the highlight last, so the highlighted row's tooltip is held up. */
	let keyed = $state(false);
	$effect(() => {
		if (!open) return;
		keyed = !untrack(() => pointerDriven);
		const key = () => (keyed = true);
		const point = () => (keyed = false);
		window.addEventListener('keydown', key, true);
		window.addEventListener('pointermove', point, true);
		return () => {
			window.removeEventListener('keydown', key, true);
			window.removeEventListener('pointermove', point, true);
		};
	});

	/* An option whose value is the empty string: the underlying Select reads "" as nothing chosen, so
	 * it is swapped for a stand-in phrase here and back on the way out. */
	/* Only where the list holds such an option: otherwise "" means nothing picked yet, and the
	 * placeholder must show. */
	const NOTHING = 'no answer was chosen';
	const answerable = $derived(options.some((o) => o.value === ''));
	const inward = $derived(options.map((o) => (o.value === '' ? { ...o, value: NOTHING } : o)));
	/* What has been typed into the box, while the list is open. Cleared when it closes, so the next
	   opening is the whole list again. */
	let typed = $state('');
	const listed = $derived(
		typed.trim() === ''
			? inward
			: inward.filter((one) =>
					one.label.toLocaleLowerCase().includes(typed.trim().toLocaleLowerCase())
				)
	);
	const shown = $derived(value === '' && answerable ? NOTHING : value);
	/** Whether the filtering box is drawn: the length of the list, unless a caller said not to. */
	const narrowing = $derived(searchable !== false && options.length > NARROW_PAST);

	/* The answers the trigger can show (every row but a press, which never becomes the value), for
	   a list short enough to read. See `.ui-select-sizer`. */
	const sizing = $derived(options.length <= NARROW_PAST);
	const sized = $derived(options.filter((one) => !one.action));

	/** The rows that are a press rather than an answer, by value. See `action` on an option. */
	const acting = $derived(
		new Set(
			options.filter((one) => one.action).map((one) => (one.value === '' ? NOTHING : one.value))
		)
	);

	/*
	 * WHAT A ROW BEING PICKED MEANS: the primitive's one way of writing the value back, so a click,
	 * Enter and typeahead all arrive here, and an action row works from the keyboard too.
	 */
	function chose(next: string): void {
		/*
		 * A PRESS, which never becomes the value: `value` is left alone, so the getter un-takes the row
		 * and it can be pressed again.
		 */
		if (acting.has(next)) {
			onAction?.(next === NOTHING ? '' : next);
			return;
		}
		const answer = next === NOTHING ? '' : next;
		value = answer;
		onValueChange?.(answer);
	}
</script>

<!--
	The chosen value is read back on every render (`bind:` with a getter and a setter), so a set this
	file declines (a press) is undone at once. A caller whose value lands after a round trip shows
	the old answer until then, the truthful direction.
-->
<Select.Root
	type="single"
	bind:open
	bind:value={() => shown ?? '', chose}
	items={inward}
	{disabled}
	onOpenChange={(open) => {
		/* Closing by pointer leaves the focus ring on the trigger (a programmatic focus satisfies
		 * `:focus-visible` in Chromium), so it is taken off for the pointer case only. */
		if (!open && pointerDriven) {
			pointerDriven = false;
			trigger?.blur();
		}
		if (!open) typed = '';
		onOpenChange?.(open);
	}}
>
	<Select.Trigger
		{id}
		bind:ref={trigger}
		onpointerdown={() => (pointerDriven = true)}
		class={`ui-select ${klass ?? ''}`}
		aria-label={label}
		aria-describedby={describedBy}
		aria-invalid={invalid || undefined}
		data-invalid={invalid ? '' : undefined}
	>
		{#if icon}
			<Icon name={icon} size={18} />
			<!-- Announced, not drawn: the value is the whole point of the control to somebody who
			     cannot see the results it ordered. -->
			<span class="ui-select-said"><Select.Value {placeholder} /></span>
		{:else}
			<!-- The chosen row's mark on the trigger, by the list's own snippet, where one is given. -->
			{#if preview && chosen}
				<span class="ui-select-item-preview">{@render preview(chosen)}</span>
			{/if}
			<span class="ui-select-answers">
				<!-- The chosen option's words as the caller draws them, else the primitive's. -->
				<span class="ui-select-value">
					{#if optionLabel && chosen}
						{@render optionLabel(chosen)}
					{:else}
						<Select.Value {placeholder} />
					{/if}
				</span>
				<!-- Every answer stacked unseen in one cell, so the control keeps its widest width; from
				     an attribute, so the visible text stays the answer. A long list sizes by the chosen
				     answer, sparing a node per row. -->
				{#if sizing}
					{#each sized as option (option.value)}
						{#if optionLabel}
							<span class="ui-select-sizer" aria-hidden="true">{@render optionLabel(option)}</span>
						{:else}
							<span class="ui-select-sizer" aria-hidden="true" data-words={option.label}></span>
						{/if}
					{/each}
					{#if placeholder}
						<span class="ui-select-sizer" aria-hidden="true" data-words={placeholder}></span>
					{/if}
					{#each [...new Set(sizeTo)] as words (words)}
						<span class="ui-select-sizer" aria-hidden="true" data-words={words}></span>
					{/each}
				{/if}
			</span>
			<Icon name="expand_more" size={CHOOSER_CHEVRON} />
		{/if}
	</Select.Trigger>

	<Select.Portal to={portalTo ?? undefined}>
		<PageShield up={open} {spare} />
		{#if phoneWidth.yes}
			<!-- AT A PHONE'S WIDTH, A SHEET FROM THE FOOT, as every menu is there: the same rows, the
			     full width, headed with whose list it is, a finger's height each. Not floated, so the
			     library measures nothing for it.
			     DRESSED BY: .ui-menu (ContextMenu styles the one menu surface)
			     DRESSED BY: .menu-sheet (ContextMenu styles the sheet every menu is at a phone width)
			     DRESSED BY: .menu-sheet-head (ContextMenu styles the sheet's head beside the sheet) -->
			<Select.ContentStatic
				class="ui-select-content ui-menu menu-sheet"
				onpointerdowncapture={sheet.down}
				onpointerupcapture={sheet.up}
				onclickcapture={sheet.click}
			>
				<p
					class="menu-sheet-head"
					aria-hidden="true"
					{@attach strokes(() => ({ live: open, on: { down: () => (open = false) } }))}
				>
					{heading}
				</p>
				{@render list()}
			</Select.ContentStatic>
		{:else if fromTheBar}
			<!-- Kept in the document by this file, so the list can be seen going back under the bar, as
			     `Popover` holds its panel; inside the library's positioned layer. -->
			<Select.Content
				forceMount
				class="ui-select-content from-bar"
				sideOffset={6}
				onmouseenter={() => onListPointer?.(true)}
				onmouseleave={() => onListPointer?.(false)}
			>
				{#snippet child({ props, wrapperProps, open: showing })}
					{#if showing}
						<div {...wrapperProps}>
							<div {...props} transition:fromBar>
								{@render list()}
							</div>
						</div>
					{/if}
				{/snippet}
			</Select.Content>
		{:else}
			<Select.Content
				class="ui-select-content"
				sideOffset={6}
				onmouseenter={() => onListPointer?.(true)}
				onmouseleave={() => onListPointer?.(false)}
			>
				{@render list()}
			</Select.Content>
		{/if}
	</Select.Portal>
</Select.Root>

{#snippet row(option: SelectOption, selected: boolean)}
	{#if preview}
		<!-- Before the words, where a reader's eye lands first: on these lists the
		     picture is the answer and the words confirm it. -->
		<span class="ui-select-item-preview">{@render preview(option)}</span>
	{/if}
	<span class="ui-select-item-text" class:noted={Boolean(option.note)}>
		<span class="ui-select-item-label">
			{#if optionLabel}{@render optionLabel(option)}{:else}{option.label}{/if}
		</span>
		{#if option.detail}
			<!-- Quieter than the name, ellipsised so a path never pushes the tick off. -->
			<span class="ui-select-item-detail">{option.detail}</span>
		{/if}
		{#if option.note}
			<span class="ui-select-item-note">{option.note}</span>
		{/if}
	</span>
	{#if selected}<Icon name="check" size={16} />{/if}
{/snippet}

<!-- The list itself, drawn in the floating box or in a phone's sheet. -->
{#snippet list()}
	<!-- The list scrolls like every other region. The ceiling stays on the content box, which
	     is what the floating layer measures against the window. -->
	{#if narrowing}
		<!-- Above the scrolling rows. Letters stay in the box; Escape empties it first (`NarrowBox`),
		     and over an empty box closes the list. -->
		<!-- svelte-ignore a11y_autofocus: the list opened to be typed into -->
		<div class="ui-select-filter">
			<NarrowBox
				bind:value={typed}
				label="Filter the list"
				autofocus
				onkeydown={(event) => {
					if (event.key !== 'Escape') event.stopPropagation();
				}}
			/>
		</div>
	{/if}
	<!-- An arrow at each end while there is more list that way: this list opens over the page,
	     where nothing else says there is more of it until somebody turns the wheel. -->
	<Scroller arrows>
		<Select.Viewport>
			{#each listed as option (option.value)}
				<Select.Item
					value={option.value}
					label={option.label}
					disabled={option.disabled}
					class="ui-select-item"
				>
					{#snippet children({ selected, highlighted })}
						{#if option.tooltip}
							<!-- Beside the list, so it never covers the next row. -->
							<Tooltip
								label={option.tooltip}
								placement="right"
								stretch
								shrinks
								held={keyed && highlighted}
							>
								<span class="ui-select-item-row">{@render row(option, selected)}</span>
							</Tooltip>
						{:else}
							{@render row(option, selected)}
						{/if}
					{/snippet}
				</Select.Item>
			{/each}
			{#if narrowing && listed.length === 0}
				<p class="ui-select-none">Nothing is called that</p>
			{/if}
		</Select.Viewport>
	</Scroller>
{/snippet}

<style>
	/* The picture beside an option. It carries no size of its own: what is drawn in it decides
	   that, so this only stops it being stretched by the row it sits in. */
	:global(.ui-select-item-preview) {
		display: inline-flex;
		flex: none;
		align-items: center;
		color: var(--sift-ink-2);
	}

	/* Read out, never drawn. `display: none` would take it out of the accessibility tree with it,
	   which is the opposite of what this is for. */
	.ui-select-said {
		position: absolute;
		inline-size: 1px;
		block-size: 1px;
		overflow: hidden;
		clip-path: inset(50%);
		white-space: nowrap;
	}

	/* The primitive renders the trigger, the menu and each row, so their classes are reached globally. */
	:global(.ui-select) {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-2);
		/* As wide as its widest answer and the chevron (see `.ui-select-sizer`), and never wider
		   than the place it sits in. A Field asks for that width by name. See the rule there. */
		inline-size: auto;
		max-inline-size: 100%;
		/* The field height and no taller, so a press beside it (which takes the field's height)
		   stands level with it. Block padding added to the line would make it 38. */
		min-block-size: var(--control-height);
		padding: 0 var(--space-3);
		border: 1px solid var(--border);
		border-radius: var(--radius-md);
		background: var(--sift-surface-3);
		color: var(--foreground);
		font: var(--text-body);
		text-align: start;
		cursor: pointer;
	}

	:global(.ui-select) {
		transition:
			border-color var(--dur-instant) var(--ease),
			background-color var(--dur-instant) var(--ease);
	}

	/* The hover layer on the trigger's own ground (see `--layer-hover`), and the edge a step up.
	   No pressed state: a press opens the list, and the open list is the answer. */
	:global(.ui-select:hover:not(:disabled)) {
		border-color: var(--sift-line-strong);
		background-color: color-mix(in srgb, currentColor var(--layer-hover), var(--sift-surface-3));
	}

	:global(.ui-select:focus-visible),
	:global(.ui-select[data-state='open']) {
		outline: none;
		border-color: transparent;
		box-shadow: var(--focus-ring);
	}

	:global(.ui-select:disabled) {
		cursor: not-allowed;
		opacity: var(--disabled-opacity);
	}

	:global(.ui-select[data-invalid]) {
		border-color: var(--sift-bad-text);
	}

	/*
	 * The words take the free width, so with a mark in front they stay beside it rather than float
	 * between the ends (`space-between` suits two children, not three). `min-inline-size: 0` lets the
	 * ellipsis appear.
	 */
	.ui-select-answers {
		display: grid;
		grid-template-columns: minmax(0, auto);
		flex: 1;
		min-inline-size: 0;
	}

	/* One cell for the shown answer and every unseen one, so the widest decides the width. Each
	   clips to an ellipsis where the place the control sits is narrower than that. */
	.ui-select-value,
	.ui-select-sizer {
		grid-area: 1 / 1;
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	/* Takes its width and nothing else: never seen, never read out, never pressed. */
	.ui-select-sizer {
		visibility: hidden;
		pointer-events: none;
	}

	.ui-select-sizer[data-words]::before {
		content: attr(data-words);
	}

	/* The open list: the app's menu surface, no wider than its control (`--bits-select-anchor-width`). */
	/*
	 * It opens with the stylesheet's `rise`, as every small surface does; an animation, since the
	 * element is the library's. Nothing moves under reduced motion.
	 */
	:global(.ui-select-content) {
		animation: rise var(--dur-fast) var(--ease);
		z-index: var(--z-menu);
		min-inline-size: var(--bits-select-anchor-width);
		/* The smaller of the window's room and the app's ceiling for a floating list (`--menu-max-height`). */
		max-block-size: min(
			var(--bits-select-content-available-height, var(--menu-max-height)),
			var(--menu-max-height)
		);
		/*
		 * A flex column, not a grid: the library sets flex inline, which beats any class; the
		 * scroller's definite height comes from `Scroller`. `overflow: hidden` clips, which
		 * `check_capped_scroller.js` requires of a ceiling.
		 */
		overflow: hidden;
		padding: var(--space-1);
		border-radius: var(--radius-lg);
		background: var(--sift-surface-3);
		box-shadow: var(--elev-3);
	}

	/* A list hanging from the top bar moves by the bar's transition alone (`fromTheBar`), at one width
	   on every wall (`--menu-bar-width`). */
	:global(.ui-select-content.from-bar) {
		animation: none;
		min-inline-size: max(var(--bits-select-anchor-width), var(--menu-bar-width));
	}

	/*
	 * OVER A FILLED SCREEN, THE LIST IS THE SAME PANE THE BARS ARE, not an opaque grey card; `BarPanel`
	 * follows this grey. Written with our own class first and the ancestor inside `:is()`, so it can
	 * only reach a list this file drew, outweighing the plain rule above.
	 */
	:global(.ui-select-content:is(.screen-box:fullscreen *)) {
		border: 1px solid var(--sift-line);
		background: var(--sift-scrim);
		backdrop-filter: blur(var(--blur-glass));
	}

	/*
	 * Mark, then words, then the tick: the words take the free space, so every label shares one edge
	 * and the tick sits at the end, as in the facet panel. The inset is `--menu-row-padding`.
	 */
	:global(.ui-select-item) {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		padding: var(--menu-row-padding);
		border-radius: var(--menu-row-radius);
		font: var(--text-body);
		color: var(--sift-ink);
		cursor: pointer;
		user-select: none;
		/* The ground steps rather than snapping. `data-highlighted` is where the pointer
		   or the keyboard is, so one transition covers both. */
		transition: background var(--dur-instant) var(--ease);
	}

	:global(.ui-select-item[data-highlighted]) {
		background: var(--menu-row-highlight);
	}

	.ui-select-item-row {
		display: flex;
		flex: 1;
		align-items: center;
		gap: var(--space-2);
		min-inline-size: 0;
	}

	/* A finger's height for a row on a phone, in the sheet the list is there. */
	@media (max-width: 767px) {
		:global(.ui-select-item) {
			min-block-size: var(--touch-target);
		}
	}

	/* The one disabled strength, as every control takes it. */
	:global(.ui-select-item[data-disabled]) {
		opacity: var(--disabled-opacity);
		cursor: default;
	}

	/* The name and its phrase as one thing, shrinkable, so the tick stays at the end. */
	.ui-select-item-text {
		display: flex;
		flex: 1;
		min-inline-size: 0;
		align-items: baseline;
		gap: var(--space-2);
	}

	.ui-select-item-label {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	/* A row with a second line stacks its words, name over note. */
	.ui-select-item-text.noted {
		flex-direction: column;
		align-items: stretch;
		gap: 0;
	}

	/* A menu row's note (`ContextMenuItem`'s `.note`), wrapping inside the list rather than widening it. */
	.ui-select-item-note {
		inline-size: 0;
		min-inline-size: 100%;
		white-space: normal;
		font: var(--text-label);
		color: var(--sift-ink-3);
	}

	.ui-select-item-detail {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* The narrowing box, at the list's own inset, with a hairline under it so the rows read as
	   what it narrows. The box itself is `NarrowBox`: the same one the facet panel draws. */
	.ui-select-filter {
		padding: var(--space-2);
		margin-block-end: var(--space-1);
		border-block-end: 1px solid var(--sift-line);
	}

	.ui-select-none {
		margin: 0;
		padding: var(--space-2) var(--space-3);
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}
</style>
