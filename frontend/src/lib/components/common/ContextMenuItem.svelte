<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'ContextMenuItem',
		category: 'surface',
		role: 'one row of a menu: a verb or a checkbox, its icon, and whether it is destructive',
		basis: 'bits-ui:ContextMenu',
		states: ['default', 'filled', 'destructive', 'disabled', 'checked', 'with a shortcut']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/*
	 * DRESSED BY: .ui-menu (ContextMenu styles the surface a row's own rows open onto; it is the
	 * same surface the outer menu is drawn on)
	 *
	 * One line in a context menu, and where it is given rows of its own, one that opens out to the
	 * side to hold them.
	 *
	 * `destructive` is the only styling choice, mapping to the one colour that means one thing in
	 * this app: red destroys something. A dangerous item that looks like the others gets picked by
	 * mistake.
	 *
	 * A row that holds rows is this component rather than another. A menu can only grow so far
	 * before it covers what somebody is acting on, and a run of near-identical rows (put this on a
	 * person, in a collection, on a Site) belongs under one row saying what they share. That row
	 * must look exactly like its neighbours until reached, with an arrow at the far end as the only
	 * tell, and that dressing lives here.
	 *
	 * The behaviour is the library's: the hover delay, the diagonal a pointer may travel without
	 * closing the submenu, the right arrow key, and the flip to the other side when there is no
	 * room.
	 */
	import type { Snippet } from 'svelte';
	import { ContextMenu } from 'bits-ui';
	import Scroller from './Scroller.svelte';
	import { theMenu } from './menu-portal';
	import { givesWayToARestingPointer } from './menu-resting.svelte';
	import { phoneWidth } from './phone-width.svelte';
	import { strokes } from '$lib/components/player/swipe';
	import Icon from '$lib/components/Icon.svelte';
	import type { IconName } from '$lib/design/icons';

	interface Row {
		label: string;
		icon?: IconName;
		/**
		 * Draw the glyph solid rather than as an outline, for glyphs whose outline is mostly empty
		 * space: an outlined `group` is a few thin strokes that disappear on this surface at this
		 * size, while a filled one reads at a glance. The fill axis is what the variable font is
		 * for.
		 */
		filled?: boolean;
		destructive?: boolean;
		disabled?: boolean;
		/**
		 * A row with a state a press flips, rather than a verb.
		 *
		 * Given this, the row is a checkbox: `menuitemcheckbox` and `aria-checked` come from the
		 * library, so a screen reader is told the same fact the tick draws (a density switch, a
		 * filter that stays on, anything whose label names a thing rather than an act). The box
		 * glyph comes from this, not from an `icon` beside it, so the tick and the state cannot
		 * disagree.
		 */
		checked?: boolean;
		/**
		 * With `checked`: the row is ONE CHOICE OF SEVERAL (a download folder, an order), not a
		 * switch. Picking one is the whole answer, so the menu closes on it, as a menu a pick has
		 * finished with does; a switch row keeps the menu open so its tick is seen to move. Read as
		 * `menuitemradio`, and ticked rather than boxed, so the one chosen is the one marked.
		 */
		oneOf?: boolean;
		/**
		 * The rows this one opens out to are as wide as THEY are, not as wide as a menu.
		 *
		 * A menu has a floor on its width (`min-inline-size` on `.ui-menu`) so that a column of
		 * verbs does not come out as a narrow strip of words. That floor is wrong for a flyout whose
		 * rows are a glyph and a digit: the rating chooser's widest row is the words "No rating" and
		 * at the menu's 180px it would be half empty.
		 *
		 * Only the SUBMENU is let off. The menu that opened it keeps its floor, which is why this is
		 * a prop on the row rather than a change to the shared surface.
		 */
		fit?: boolean;
		/**
		 * A second line under the label, in small letters, saying something about the row's SUBJECT
		 * rather than about the act.
		 *
		 * "Last: 16 Sep 2026, 4:12 PM" under Enrich. It is not help text and it is not a tooltip:
		 * help says what a control does and this says what has already happened to the thing the
		 * control acts on, which is what somebody decides by before pressing it.
		 *
		 * Absent draws nothing at all, and that is the point rather than a default. A file nothing
		 * has ever enriched has no line, not a line saying never: a row of "never" under every
		 * verb on a fresh library is noise, and the absence is already the answer.
		 */
		note?: string;
	}

	/*
	 * A row does something, or it holds rows. Never both, and the type is what says so rather than a
	 * comment: a row that both opened out and acted would act on the way past, which is a menu
	 * doing something to a library because a pointer crossed it.
	 */
	type Props = Row &
		(
			| { children: Snippet; fit?: boolean; onselect?: never; href?: never; onfollow?: never }
			| { children?: never; fit?: never; onselect: () => void; href?: never; onfollow?: never }
			/* A row that goes somewhere: a real link, so the address is a link's to the end (a
			   middle press opens it beside this one, and a screen reader says where it goes). The
			   folded steps of a breadcrumb trail are these. */
			| {
					children?: never;
					fit?: never;
					onselect?: () => void;
					href: string;
					/** Run on a click of the link before it is followed, as a crumb's own link runs
					    `returnTo`, so a folded crumb steps back the same way a standing one does. */
					onfollow?: (event: MouseEvent) => void;
			  }
		);

	let {
		label,
		icon,
		filled = false,
		destructive = false,
		disabled = false,
		fit = false,
		checked,
		oneOf = false,
		note,
		children,
		onselect,
		href,
		onfollow
	}: Props = $props();

	/* Where the flyout is drawn, and how it is dressed. See `menu-portal.ts`: without the portal the
	   flyout is inside the scrolling region its own trigger sits in, and a scrolling region clips;
	   without the dressing it is the default surface even when what opened it is the tighter box. */
	const menu = theMenu();

	/* Whether the rows this one opens onto are showing, so a stroke down a phone's sheet of them can
	   put them away. */
	let subOpen = $state(false);

	/* The row that opens it, for the rule that a pointer resting on a neighbouring row is answered
	   by that row even while this flyout is open. See `menu-resting.svelte.ts`. */
	let subTrigger = $state<HTMLElement | null>(null);
	givesWayToARestingPointer({
		open: () => subOpen,
		close: () => (subOpen = false),
		trigger: () => subTrigger
	});
</script>

{#if children}
	<ContextMenu.Sub bind:open={subOpen}>
		<ContextMenu.SubTrigger class="item" {disabled} bind:ref={subTrigger}>
			{#if icon}<Icon name={icon} {filled} />{/if}
			<span class="said">
				<span>{label}</span>
				{#if note}<span class="note">{note}</span>{/if}
			</span>
			<span class="arrow"><Icon name="chevron_right" size={16} /></span>
		</ContextMenu.SubTrigger>
		<!--
			PORTALLED, and this is what makes a flyout appear at all.

			The rows of the menu that opened this one are inside a `Scroller`, and a scrolling region
			clips, so a flyout, which is drawn beside that region rather than in it, would be mounted
			with the right size at the right place and then cut away to nothing. `menu-portal.ts`
			holds the whole account, and says why the target is handed down rather than assumed.
		-->
		<ContextMenu.Portal to={menu.where() ?? undefined}>
			{#if phoneWidth.yes}
				<!-- AT A PHONE'S WIDTH THE ROWS IT OPENS ONTO ARE A SHEET TOO, over the sheet it came from:
			     a flyout beside a sheet the width of the screen opens past the screen's edge. Headed with
			     the row that opened it; a finger drawn down the head puts it back.
			     DRESSED BY: .menu-sheet (ContextMenu styles the sheet every menu is at a phone width)
			     DRESSED BY: .menu-sheet-head (ContextMenu styles the sheet's head beside the sheet) -->
				<ContextMenu.SubContentStatic class="ui-menu menu-sheet">
					<p
						class="menu-sheet-head"
						aria-hidden="true"
						{@attach strokes(() => ({ live: subOpen, on: { down: () => (subOpen = false) } }))}
					>
						{label}
					</p>
					<Scroller arrows>
						{@render children()}
					</Scroller>
				</ContextMenu.SubContentStatic>
			{:else}
				<ContextMenu.SubContent class="ui-menu {fit ? 'fits' : ''}">
					<!-- The rows scroll, on the same ceiling the menu that opened this one has.
					     `ContextMenu` owns both halves of it; this is the region they scroll in. -->
					<Scroller arrows>
						{@render children()}
					</Scroller>
				</ContextMenu.SubContent>
			{/if}
		</ContextMenu.Portal>
	</ContextMenu.Sub>
{:else if checked !== undefined && oneOf}
	<!--
		ONE CHOICE OF SEVERAL. `onSelect` is let through, so the menu closes on the pick (the
		library's `closeOnSelect`): the choice is made and nothing is left to do here. Drawn through
		the library's `child` so the row can say `menuitemradio`; `aria-checked` is the library's.
	-->
	<ContextMenu.CheckboxItem {disabled} {checked} onSelect={() => onselect?.()}>
		{#snippet child({ props })}
			<div {...props} role="menuitemradio" class="item {destructive ? 'destructive' : ''}">
				<span class="tick" class:unpicked={!checked}><Icon name="check" {filled} /></span>
				<span>{label}</span>
			</div>
		{/snippet}
	</ContextMenu.CheckboxItem>
{:else if checked !== undefined}
	<!--
		A CHECKBOX row. `onSelect` takes the event and refuses it, which does two things at once and
		both are wanted: the menu stays OPEN, so the tick is seen to move, and the library does not
		flip its own copy of `checked` underneath the answer the caller holds. The same shape
		`PickMenu` uses, for the same two reasons.
	-->
	<ContextMenu.CheckboxItem
		class="item {destructive ? 'destructive' : ''}"
		{disabled}
		{checked}
		onSelect={(event: Event) => {
			event.preventDefault();
			onselect?.();
		}}
	>
		<Icon name={checked ? 'check_box' : 'check_box_outline_blank'} {filled} />
		<span>{label}</span>
	</ContextMenu.CheckboxItem>
{:else if href !== undefined}
	<!-- The library's row, drawn as a link through its `child`: the row's keys, highlight and
	     closing are the library's, and going there is the link's own. -->
	<ContextMenu.Item {disabled} onSelect={onselect}>
		{#snippet child({ props })}
			<a
				{...props}
				class="item link {destructive ? 'destructive' : ''}"
				{href}
				onclick={(event: MouseEvent) => {
					(props.onclick as ((event: MouseEvent) => void) | undefined)?.(event);
					onfollow?.(event);
				}}
			>
				{#if icon}<Icon name={icon} {filled} />{/if}
				<span class="said">
					<span>{label}</span>
					{#if note}<span class="note">{note}</span>{/if}
				</span>
			</a>
		{/snippet}
	</ContextMenu.Item>
{:else}
	<ContextMenu.Item class="item {destructive ? 'destructive' : ''}" {disabled} onSelect={onselect}>
		{#if icon}<Icon name={icon} {filled} />{/if}
		<span class="said">
			<span>{label}</span>
			{#if note}<span class="note">{note}</span>{/if}
		</span>
	</ContextMenu.Item>
{/if}

<style>
	/* The inset is the app's, not this file's: a row in a menu, a row in the chooser's list, a
	   suggestion and a rating are the same row, and four copies would drift apart. See
	   `--menu-row-padding` in `app.css`.

	   From `.ui-menu`, the one menu surface: `PickMenu` writes `.item` on its rows so they are this
	   row, while the rail and the settings list write `.item` on rows of their own that this must
	   not dress. */
	:global(.ui-menu .item) {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		padding: var(--menu-row-padding);
		border-radius: var(--menu-row-radius);
		font: var(--text-body);
		color: var(--sift-ink);
		cursor: pointer;
		user-select: none;
		/* The Light register: the ground STEPS rather than snapping. `data-highlighted`
		   is the menu's own idea of where the pointer or the keyboard is, so this covers both. */
		transition: background var(--dur-instant) var(--ease);
	}

	/* The tick of one choice of several holds its room on every row, so the labels line up. */
	.tick {
		display: inline-flex;
	}

	.tick.unpicked {
		visibility: hidden;
	}

	/* A row that is a link reads as a row, not as underlined words. */
	:global(.ui-menu a.item.link) {
		text-decoration: none;
	}

	:global(.ui-menu .item[data-highlighted]) {
		background: var(--menu-row-highlight);
	}

	/* A flyout as wide as its own rows. See the `fit` prop: the menu that opened it keeps its floor,
	   and only the submenu this row draws is let off. */
	:global(.ui-menu.fits) {
		min-inline-size: 0;
	}

	/*
	 * The one disabled strength, as every control takes it.
	 *
	 * A row has no pressed state: choosing one closes the menu, so nothing is left to draw it on.
	 * Nor a selected one beyond its tick (`checked`), which is the row's value, not a look.
	 */
	:global(.ui-menu .item[data-disabled]) {
		opacity: var(--disabled-opacity);
		cursor: default;
	}

	/* Red means one thing in this app: this destroys something. Nothing else uses it. */
	:global(.ui-menu .item.destructive) {
		color: var(--sift-bad-text);
	}

	/* Pushed to the far end of the row, which is what says "there is more this way" before anybody
	   hovers to find out. Written here rather than beside the trigger it sits in, because the row it
	   has to line up with is dressed here too. Global from the menu surface, like the row, so
	   `PickMenu`'s row that opens out wears the same chevron rather than a copy of it. */
	:global(.ui-menu .arrow) {
		display: inline-flex;
		margin-inline-start: auto;
		color: var(--sift-ink-3);
	}

	/* The label and its note as one column, so a row with a note is one row that is taller rather
	   than two rows that happen to sit together. */
	.said {
		display: flex;
		flex-direction: column;
	}

	/* Small letters, quiet ink. `--sift-ink-3` and not `--sift-ink-4`: this is TEXT somebody reads a
	   date out of, and ink-4 is decoration by the contrast test's own rule. */
	.note {
		font: var(--text-label);
		color: var(--sift-ink-3);
	}
</style>
