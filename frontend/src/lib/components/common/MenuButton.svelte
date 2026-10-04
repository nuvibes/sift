<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'MenuButton',
		category: 'surface',
		role: 'a button that opens a dropdown of rows',
		basis: 'bits-ui:DropdownMenu',
		states: ['closed', 'open']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* DRESSED BY: .ui-menu (ContextMenu styles the surface every menu in the app is drawn on: this
	   door opens the same one, so there is one rule for both rather than a second copy here) */
	/* DRESSED BY: .menu-sheet (ContextMenu styles the sheet every menu is at a phone width) */
	/* DRESSED BY: .menu-sheet-head (ContextMenu styles the sheet's head beside the sheet) */
	/* WHY NOT BITS-UI: this IS bits-ui's DropdownMenu. What it adds is the one thing a library
	   cannot supply: the button's own dressing, written once rather than by every screen that
	   draws a menu door. */

	/*
	 * A button that opens the app's menu, and nothing about what is in it.
	 *
	 * ## Why this is separate from `RowMenu`
	 *
	 * A menu is two things: a DOOR (the trigger, the portal, the shared surface) and a set of ROWS,
	 * which `RowMenu` takes as declared verbs against a list of ids. That is exactly
	 * right for a row of a table, which is what it is named for and where all of its callers are.
	 *
	 * The Jobs screen wants the door and not the rows. Its actions are not verbs: a verb is
	 * something done to what is PICKED, `Verb.run` is handed the ids, and both surfaces that draw
	 * verbs draw the same declared list. A job action is done to a PILE (every failure, every
	 * stopped job) and there is no second surface. Declaring them as verbs against an empty id
	 * list would put a falsehood in the one place the type could not catch it, and would weaken the
	 * concept the verb modules exist to protect.
	 *
	 * So the door is here and `RowMenu` composes it. Nothing about how a menu opens is written
	 * twice, and each file owns one idea: this one opens the menu, that one knows what verbs look
	 * like as rows.
	 *
	 * ## What goes inside
	 *
	 * `ContextMenuItem`, which is the app's menu row and works here unchanged. In bits-ui 2.18
	 * `DropdownMenu.Item`, `.Sub`, `.SubTrigger`, `.SubContent` and `.Separator` are re-exported
	 * from the same `menu/` internals `ContextMenu` re-exports: only `Root`, `Content` and
	 * `Trigger` differ, which is the part about how the menu is OPENED. So a row written for the
	 * right-click menu is the same object here, and there is no second renderer.
	 */
	import type { Snippet } from 'svelte';
	import { DropdownMenu } from 'bits-ui';
	import Button from '$lib/components/common/Button.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Scroller from '$lib/components/common/Scroller.svelte';
	import PageShield from '$lib/components/common/PageShield.svelte';
	import { ownsTheMenu } from '$lib/components/common/menu-portal';
	import { escapeWhileClosing } from '$lib/components/common/menu-closing.svelte';
	import {
		CHOOSER_MENU,
		WHOLE_MENU,
		chooserSide,
		rootLength
	} from '$lib/components/common/menu-whole';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import { sheetPresses } from '$lib/components/common/menu-touch';
	import { strokes } from '$lib/components/player/swipe';
	import Icon from '$lib/components/Icon.svelte';

	interface Props {
		/**
		 * The accessible name of the button, and of the menu it opens.
		 *
		 * Says whose menu it is ("More for orla", "Options for this file") because a screen of
		 * twenty rows each announced as "More" tells somebody using a screen reader nothing about
		 * which is which.
		 */
		label: string;
		/**
		 * Words on the button, instead of the three dots.
		 *
		 * For a door that is not at the end of a row: a file's own screen, an entity's header, the
		 * Jobs screen. Three dots floating beside a named button says nothing about what is behind
		 * them, and a screen that had to invent its own worded trigger would be inventing this whole
		 * door again for the sake of a label.
		 *
		 * Absent at the end of a row, which is where the glyph is right: twenty rows each carrying
		 * the word "Options" is twenty words nobody reads.
		 */
		words?: string;
		/** Nothing can be opened just now: a request is in flight, or the row is being rebuilt. */
		disabled?: boolean;
		/**
		 * Whether this door scrolls its own contents. True unless the contents scroll themselves.
		 *
		 * A menu of rows wants the scroller below, and opens whole the way the right-click menu
		 * does (`menu-whole.ts`): its rows are one thing's, so it takes the window's room and flips
		 * upward when the room below is short, scrolling only when the window is shorter. A menu
		 * whose whole content is one composed thing that already scrolls (`PickMenu` drawn
		 * `inline`: a box, a scrolling list and a ceiling line) keeps the shared ceiling. Given the
		 * scroller as well, it would be two nested scrolling regions, and the outer one wins: the
		 * grid track's height belongs to it, so the picker is laid out at its full content height
		 * and a box at its far end lands far off screen.
		 *
		 * So the content says which it is. Given `false`, the composed thing is the grid item,
		 * bounded by the track like a flyout, and its own list is the one scrolling region.
		 */
		scrolls?: boolean;
		/**
		 * How far the menu sits from the button, in pixels.
		 *
		 * The default is what every menu has, touching what opened it. The prop is for a menu that
		 * must touch: one drawn against a floating bar, where a gap would make the box read as a
		 * panel that had come loose from another surface underneath.
		 *
		 * `--space-1` is the ceiling it is held to and zero is what it is given, so only each
		 * surface's shadow sits between the two edges.
		 */
		sideOffset?: number;
		/**
		 * Which way the menu opens, when the library's own answer is wrong.
		 *
		 * It is right almost everywhere: bits-ui opens downwards and flips when the window is in the
		 * way, which is what a door in a row wants. A door in a bar at the FOOT of the window has
		 * one honest direction, and being told so rather than discovering it by collision is what
		 * keeps a tall menu from opening downwards into two pixels of room and then jumping.
		 */
		side?: 'top' | 'bottom' | 'left' | 'right';
		/**
		 * Where the open menu is drawn, when the end of the document is the wrong answer.
		 *
		 * The same prop, with the same name and the same one reason, that `ContextMenu`, `Select` and
		 * `Popover` carry: a browser filling the screen paints the fullscreened element and its
		 * subtree and nothing else in the document, so a menu portalled to `body` while a screen is
		 * filled is not hidden: it is not drawn. The door opens, takes the press and the keyboard,
		 * and shows nothing anywhere.
		 *
		 * It reaches the flyouts a row opens as well as this menu's own layer. See below, and
		 * `menu-portal.ts`.
		 */
		portalTo?: Element | null;
		/**
		 * Whether the menu is open, for a caller that opens it from somewhere other than the button.
		 *
		 * A row whose only meaning is its verbs (a library folder in Settings, which has no screen
		 * of its own to go to) opens this door on a plain press anywhere on the row, so that a
		 * press does something. Bindable, so the caller sees it close as well. Left unbound, the
		 * button is the only way in.
		 */
		open?: boolean;
		/** Told when the menu opens or closes, for a caller that holds `open` without binding it:
		 *  a list of rows keeping one menu open at a time by id. */
		onOpenChange?: (open: boolean) => void;
		/**
		 * The door itself, drawn by the caller.
		 *
		 * For the one case where the trigger is not this component's to dress: the trailing half
		 * of a `SplitButton`, which has to be the shared Button at the pair's own tone and size so
		 * the two halves read as one control. The caller spreads `props` onto whatever it draws:
		 * that is the library's own trigger wiring, and it is what opens the menu.
		 */
		trigger?: Snippet<[{ props: Record<string, unknown> }]>;
		/** The rows. */
		children: Snippet;
	}

	let {
		label,
		words,
		disabled = false,
		scrolls = true,
		sideOffset = 0,
		side,
		portalTo = null,
		open = $bindable(false),
		onOpenChange,
		trigger,
		children
	}: Props = $props();

	/*
	 * The flyout a row opens goes where this menu's own layer goes, the end of the document, and
	 * wears this door's dressing. Said out loud rather than left to the default, because a row
	 * inside this door must not inherit either answer from whatever menu is open around it. See
	 * `menu-portal.ts`.
	 *
	 * `portalTo` is needed for the fullscreen case, as on `ContextMenu`, `Select` and `Popover`: a
	 * kept filter's pill draws a `RowMenu` inside the filter panel, which drops out of the bar
	 * inside `.screen-box`, so on a filled Theater wall the menu must land inside the fullscreened
	 * element. It goes to two places, `DropdownMenu.Portal` for the menu itself and the `where`
	 * below for a row's flyout; either alone is half a fix. Null everywhere else.
	 */
	ownsTheMenu({ where: () => portalTo });

	/* Escape while the menu is on its way out belongs to the menu, not to the panel under it. See
	   `menu-closing.svelte.ts`. */
	const closingMenu = escapeWhileClosing();

	/* The tap that opens the sheet must not also choose a row. See `menu-touch.ts`. */
	const sheet = sheetPresses();

	/* The press a chooser opens from, so its side can be read off the window at the press. */
	let door = $state<HTMLElement | null>(null);
	let placed = $state<'top' | 'bottom' | undefined>(undefined);

	/* A chooser's side, decided at the press from the room around it (`chooserSide` says why the
	   library's own flip cannot be left to do it). A caller's `side` is kept, and a menu that opens
	   whole needs none: its rows are all there when it is placed, so the flip sees them. */
	function placeChooser(): 'top' | 'bottom' | undefined {
		if (side || scrolls || !door) return undefined;
		const box = door.getBoundingClientRect();
		return chooserSide({
			top: box.top,
			bottom: box.bottom,
			window: window.innerHeight,
			chrome: rootLength('--window-chrome', 0),
			ceiling: rootLength('--menu-max-height', Infinity)
		});
	}

	/* Every way the menu opens or closes goes through here: the library's own, and the stroke down
	   the sheet's head (`ContextMenu` says why), which the library does not hear of. */
	function changed(next: boolean): void {
		if (next) placed = placeChooser();
		open = next;
		sheet.reset();
		closingMenu.changed(next);
		onOpenChange?.(next);
	}
</script>

<DropdownMenu.Root bind:open onOpenChange={changed} onOpenChangeComplete={closingMenu.complete}>
	{#if trigger}
		<DropdownMenu.Trigger {disabled} bind:ref={door}>
			{#snippet child({ props })}
				{@render trigger({ props })}
			{/snippet}
		</DropdownMenu.Trigger>
	{:else if words}
		<!-- The shared button, handed the library's trigger props through `child`, rather than a
		     button dressed here to look like one: a second set of rules for the same object is two
		     copies free to come apart. The chevron is `trailing`, which is what that prop is for. -->
		<DropdownMenu.Trigger {disabled} bind:ref={door}>
			{#snippet child({ props })}
				<Button {...props} trailing="expand_more">{words}</Button>
			{/snippet}
		</DropdownMenu.Trigger>
	{:else}
		<!-- Three dots alone, so a tooltip names them, as every glyph-only control is named. -->
		<Tooltip {label}>
			<DropdownMenu.Trigger class="more menu-dots" aria-label={label} {disabled} bind:ref={door}>
				<Icon name="more_vert" size={18} />
			</DropdownMenu.Trigger>
		</Tooltip>
	{/if}
	<DropdownMenu.Portal to={portalTo ?? undefined}>
		<!-- A press outside the menu closes it and reaches nothing under it. See `PageShield`. -->
		<PageShield up={open || closingMenu.closing} />
		{#if phoneWidth.yes}
			<!-- At a phone's width the same rows come up as a sheet from the foot of the screen, the
			     whole width of it, headed with whose menu it is: the three dots are how a touch
			     screen reaches a menu, and a dropdown hung off a 44 pixel square is a desktop shape.
			     The library's own menu, drawn without its floating placement; `ContextMenu` owns the
			     sheet's dressing, as it owns every `.ui-menu`. -->
			<DropdownMenu.ContentStatic
				class="ui-menu menu-sheet"
				aria-label={label}
				onpointerdowncapture={sheet.down}
				onpointerupcapture={sheet.up}
				onclickcapture={sheet.click}
			>
				<p
					class="menu-sheet-head"
					aria-hidden="true"
					{@attach strokes(() => ({ live: open, on: { down: () => changed(false) } }))}
				>
					{label}
				</p>
				{#if scrolls}
					<Scroller arrows>
						{@render children()}
					</Scroller>
				{:else}
					{@render children()}
				{/if}
			</DropdownMenu.ContentStatic>
		{:else}
			<DropdownMenu.Content
				{...scrolls ? WHOLE_MENU : CHOOSER_MENU}
				aria-label={label}
				{sideOffset}
				{...(side ?? placed) ? { side: side ?? placed } : {}}
			>
				<!-- The rows scroll, because the surface they are on now has a ceiling. `ContextMenu`
				     owns both halves of that (the cap and the grid track the scroller is bounded by)
				     and every surface wearing `.ui-menu` puts its rows in one of these.

				     Unless the content is one composed thing that already scrolls, and then this must
				     not be here at all: two nested scrolling regions hand the track's height to the
				     OUTER one, and the inner list is laid out at full content height with nothing to
				     shrink against. See `scrolls`. -->
				{#if scrolls}
					<Scroller arrows>
						{@render children()}
					</Scroller>
				{:else}
					{@render children()}
				{/if}
			</DropdownMenu.Content>
		{/if}
	</DropdownMenu.Portal>
</DropdownMenu.Root>

<style>
	/*
	 * The button, dressed here rather than by each screen that draws one.
	 *
	 * Global because the class goes onto bits-ui's own element, which is this component's child in
	 * the tree and nobody else's. One copy, so the dots cannot dim on hover while disabled in one
	 * screen and not in another.
	 *
	 * Bounded by `menu-dots`, which only this file writes. `more` alone is also the search box's
	 * "Show more" row and the facet panel's "View more" button, and a 2rem square fixed at the
	 * small control height is not what either of them is.
	 */
	:global(button.more.menu-dots) {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		inline-size: 2rem;
		block-size: var(--control-height-sm);
		padding: 0;
		border: 0;
		border-radius: var(--radius-sm);
		background: none;
		color: var(--sift-ink-2);
		cursor: pointer;
		/* The Light register, so the three dots do not snap to a different ground the
		   instant a pointer crosses them. */
		transition:
			background var(--dur-instant) var(--ease),
			color var(--dur-instant) var(--ease);
	}

	/* The state layer (see `--layer-hover`) over no ground of its own, so the square answers on a
	   row, on a card and on a translucent bar alike, and the dots come up to the full ink. */
	:global(button.more.menu-dots:hover:not(:disabled)) {
		color: var(--hover-ink);
		background-color: color-mix(in srgb, currentColor var(--layer-hover), transparent);
	}

	/* Pressed, and while its menu is open: the stronger layer, so the door says it is holding a
	   menu open for as long as it is. */
	:global(button.more.menu-dots:active:not(:disabled)),
	:global(button.more.menu-dots[data-state='open']) {
		color: var(--hover-ink);
		background-color: color-mix(in srgb, currentColor var(--layer-pressed), transparent);
	}

	:global(button.more.menu-dots:disabled) {
		opacity: var(--disabled-opacity);
		cursor: default;
	}

	/* A finger's size at a phone's width. The dots are the only door to a row's menu on a touch
	   screen (a hold selects), so they are the one target on the row that must not be missed. */
	@media (max-width: 767px) {
		:global(button.more.menu-dots) {
			inline-size: var(--touch-target);
			block-size: var(--touch-target);
		}
	}
</style>
