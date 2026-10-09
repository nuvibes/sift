<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Select',
		category: 'control',
		role: 'one value picked from a list, searchable when the list is long',
		basis: 'bits-ui:Select',
		states: ['closed', 'open', 'searchable', 'disabled']
	} satisfies DesignEntry;

	/** The one chevron size for every chooser, a select or a number's unit door. */
	export const CHOOSER_CHEVRON = 18;
</script>

<script lang="ts">
	/* A dropdown in the app's look on the library's Select, shaped like a <select>. */
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
		/** A small second line under the name; it wraps and never widens the list. */
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
		/** An action row was pressed: its own door, so a caller never stores it as a value. */
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
		/** The trigger as one icon, where the choice shows on screen; `label` still names it. */
		icon?: IconName;
		/**
		 * Where the open list is drawn: inside the fullscreen element while one fills the screen.
		 */
		portalTo?: Element | null;
		/** Told on open and close, for a caller that closes on leaving. */
		onOpenChange?: (open: boolean) => void;
		/** Whether the list shows, for a caller that owns it (the screen bar). */
		open?: boolean;
		/** The one element left pressable under the open sheet, for a row of triggers. */
		spare?: HTMLElement | null;
		/** The pointer entered or left the open list, for a caller closing on leaving. */
		onListPointer?: (inside: boolean) => void;
		/** A picture beside each option, where the words are not the answer. */
		preview?: Snippet<[SelectOption]>;
		/**
		 * The option's words drawn by the caller (fonts in each face); the trigger draws it too.
		 */
		optionLabel?: Snippet<[SelectOption]>;
		/** The filter box: past `NARROW_PAST` rows unless turned off. */
		searchable?: boolean;
		/** Words the closed control is as wide as, so a column of choosers keeps one width. */
		sizeTo?: readonly string[];
		/** Hang from the top bar, coming out from under it as the Filter panel does. */
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

	/* Past ten rows, typing beats scrolling. */
	const NARROW_PAST = 10;

	/** The trigger element, so a pointer-driven close can take the ring back off it. */
	let trigger = $state<HTMLElement | null>(null);

	/* The sheet a phone opens is headed with whose list it is: the chooser's name or its field's label. */
	const heading = $derived(
		label ?? (trigger as HTMLButtonElement | null)?.labels?.[0]?.textContent?.trim() ?? ''
	);

	/* The opening tap must not also choose a row (`menu-touch.ts`). */
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

	/* The library reads "" as nothing chosen, so an empty-string option is swapped for a stand-in,
	 * only where the list holds one. */
	const NOTHING = 'no answer was chosen';
	const answerable = $derived(options.some((o) => o.value === ''));
	const inward = $derived(options.map((o) => (o.value === '' ? { ...o, value: NOTHING } : o)));
	/* Typed into the box while open; cleared on close. */
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

	/* The answers the trigger can show, for a short list (`.ui-select-sizer`). */
	const sizing = $derived(options.length <= NARROW_PAST);
	const sized = $derived(options.filter((one) => !one.action));

	/** The rows that are a press rather than an answer, by value. See `action` on an option. */
	const acting = $derived(
		new Set(
			options.filter((one) => one.action).map((one) => (one.value === '' ? NOTHING : one.value))
		)
	);

	/* Every pick arrives here (click, Enter, typeahead), so action rows work from the keyboard. */
	function chose(next: string): void {
		/* A press never becomes the value, so the row can be pressed again. */
		if (acting.has(next)) {
			onAction?.(next === NOTHING ? '' : next);
			return;
		}
		const answer = next === NOTHING ? '' : next;
		value = answer;
		onValueChange?.(answer);
	}
</script>

<!-- The value read back every render, so a declined set is undone immediately. -->
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
			<!-- Announced, not drawn. -->
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
				<!--
				Every answer stacked unseen in one cell, so the control keeps its widest width.
				-->
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
			<!-- At a phone's width, a sheet from the foot headed with whose list it is.
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
			<!-- Held in the document so it can be seen going back under the bar. -->
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
		<!-- The picture first: on these lists it is the answer. -->
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
	<!-- The ceiling stays on the content box, which the floating layer measures. -->
	{#if narrowing}
		<!-- Above the rows; Escape empties the box first, then closes the list. -->
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
	<!-- Arrows while there is more list that way. -->
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
	/* Not stretched by its row; its content sizes it. */
	:global(.ui-select-item-preview) {
		display: inline-flex;
		flex: none;
		align-items: center;
		color: var(--sift-ink-2);
	}

	/* Read out, never drawn, so not `display: none`. */
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
		/* As wide as its widest answer and the chevron, never wider than its place. */
		inline-size: auto;
		max-inline-size: 100%;
		/* The field height, so a press beside it stands level. */
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

	/* The hover layer on the trigger's ground; no pressed state. */
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

	/* The words take the free width beside the mark; min 0 lets the ellipsis appear. */
	.ui-select-answers {
		display: grid;
		grid-template-columns: minmax(0, auto);
		flex: 1;
		min-inline-size: 0;
	}

	/* One cell for every answer, so the widest decides the width. */
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

	/* The open list: the menu surface, no wider than its control, rising as small surfaces do. */
	:global(.ui-select-content) {
		animation: rise var(--dur-fast) var(--ease);
		z-index: var(--z-menu);
		min-inline-size: var(--bits-select-anchor-width);
		/* The smaller of the window's room and the app's ceiling for a floating list (`--menu-max-height`). */
		max-block-size: min(
			var(--bits-select-content-available-height, var(--menu-max-height)),
			var(--menu-max-height)
		);
		/* A flex column; `overflow: hidden` clips, as `check_capped_scroller.js` requires. */
		overflow: hidden;
		padding: var(--space-1);
		border-radius: var(--radius-lg);
		background: var(--sift-surface-3);
		box-shadow: var(--elev-3);
	}

	/* From the top bar: the bar's transition and `--menu-bar-width`. */
	:global(.ui-select-content.from-bar) {
		animation: none;
		min-inline-size: max(var(--bits-select-anchor-width), var(--menu-bar-width));
	}

	/* Over a filled screen, the bars' pane, reaching only a list this file drew. */
	:global(.ui-select-content:is(.screen-box:fullscreen *)) {
		border: 1px solid var(--sift-line);
		background: var(--sift-scrim);
		backdrop-filter: blur(var(--blur-glass));
	}

	/* Mark, words, then the tick, at `--menu-row-padding`. */
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
		/* The ground steps, for pointer and keyboard. */
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

	/* The filter box at the list's inset, a hairline under it. */
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
