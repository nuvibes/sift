<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'ContextMenu',
		category: 'surface',
		role: 'the menu on a right-click, a long press, or a three-dot button',
		basis: 'bits-ui:ContextMenu',
		states: ['open']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/*
	 * Right-click on a thing, get the things you can do to it.
	 *
	 * Built on the library's menu rather than by hand, and that is the whole reason to have one
	 * shared component: a menu is mostly keyboard and focus behaviour (arrow keys, typeahead,
	 * escape, returning focus to what opened it, not closing before the click lands) and every
	 * hand-rolled one gets some of that wrong.
	 *
	 * It wraps its trigger rather than being positioned by a caller, so the thing that opens the menu
	 * and the menu itself cannot drift apart.
	 *
	 * ## A HOLD DOES NOT OPEN THIS, on any input, and that is deliberate
	 *
	 * Press and hold is how a selection starts (the gesture from every photo app there is) and it
	 * raises the action bar for whatever is now picked. It is a different question from a right-click:
	 * holding says "I mean these ones", right-clicking says "what can I do to this".
	 *
	 * The library's own trigger has a touch long-press built in (700 ms), and it is switched off
	 * here. Left alone, one hold on a phone would do two things, the selection at 300 ms and then a
	 * desktop dropdown over it at 700. The menu has another door on a
	 * touch screen, the three dots, which is what a hover-only affordance's non-hover equivalent is
	 * for. So the hold belongs to `TileGesture`, on every input, and this opens on the right button,
	 * the menu key and the three dots.
	 *
	 * Two halves, because a touch screen asks for a menu two ways. The library's timer starts on a
	 * touch `pointerdown`, so that handler is handed on for a mouse only (it does nothing for one
	 * today; handing it on keeps anything the library adds for the mouse). And a phone's browser
	 * raises its own `contextmenu` event from a long press (Chrome on Android does), which the
	 * library would open on as it does for a right-click; that one is refused, and refusing it also
	 * keeps the browser's own menu off a tile that is being picked. A `contextmenu` from a mouse or
	 * from the keyboard is handed on unchanged.
	 *
	 * ## At a phone's width it is a SHEET
	 *
	 * The same rows, from the foot of the screen, the whole width of it, each row a finger's height.
	 * A dropdown anchored to a point is a desktop shape: at 390 wide a menu of sixteen rows covers
	 * whatever it was opened on, and its rows are a mouse's height. The library draws a menu without
	 * its floating placement (`ContentStatic`), so the rows, the keyboard and the dismissal are the
	 * library's either way; only the placement is this file's. `MenuButton` does the same for the
	 * three dots, off the same `phoneWidth`.
	 *
	 * A finger drawn down the sheet's head puts it away, as every phone's sheet goes and as a sheet
	 * from `Drawer` does: the stroke is read by `strokes` (`player/swipe.ts`), the one reading of a
	 * stroke in the app, and it closes the menu exactly as a press outside would.
	 */
	import type { Snippet } from 'svelte';
	import { ContextMenu } from 'bits-ui';
	import Scroller from './Scroller.svelte';
	import PageShield from './PageShield.svelte';
	import { ownsTheMenu } from './menu-portal';
	import { escapeWhileClosing } from './menu-closing.svelte';
	import { WHOLE_MENU } from '$lib/components/common/menu-whole';
	import { phoneWidth } from './phone-width.svelte';
	import { handOn, libraryMaySee, refusesTheHold, sheetPresses, touchPress } from './menu-touch';
	import { strokes } from '$lib/components/player/swipe';

	interface Props {
		/** What can be right-clicked. */
		children: Snippet;
		/** The menu itself, built from the `Item` component exported alongside this one. */
		items: Snippet;
		/** Announced to a screen reader when the menu opens. */
		label?: string;
		/**
		 * A class for the element this wraps its trigger in.
		 *
		 * The wrapper is a plain block, so around something that was stretching to fill a column it
		 * is fine and around something laid out by its parent (a row in a flex list), it is a box
		 * that does not know it should stretch. The caller knows; this lets it say so.
		 */
		triggerClass?: string;
		/**
		 * Opened or closed, for a caller that marks what the menu is ABOUT.
		 *
		 * A screen that aims at the thing under the pointer without selecting it has to know when
		 * to stop marking it, and the only thing that knows the menu has gone is the menu. Optional,
		 * because most callers act on a selection that is already visible on its own.
		 */
		onOpenChange?: (open: boolean) => void;
		/**
		 * Where the open menu is drawn, when the end of the document is the wrong answer.
		 *
		 * It normally is the right answer, and for the reason every menu is portalled: it escapes
		 * every `overflow: hidden` and every stacking context between it and whatever was
		 * right-clicked.
		 *
		 * Fullscreen is the exception, and it is not a styling problem that a z-index could reach. A
		 * browser filling the screen draws ONLY the fullscreened element and its descendants:
		 * everything else in the document is not hidden, it is not drawn at all. So a menu at the end
		 * of `body` simply does not exist on screen while a wall is filling the window: the menu
		 * opens, takes the keyboard, and shows nothing. Handing the fullscreened element in puts it
		 * back inside the subtree the browser is painting.
		 *
		 * The same prop, with the same name, that `Select` already carries for the same reason. A
		 * primitive that cannot express a genuine need is EXTENDED, once, for every caller, never
		 * worked around locally, which is how a second menu gets built beside this one.
		 */
		portalTo?: Element | null;
	}

	let {
		children,
		items,
		label = 'Actions',
		triggerClass,
		onOpenChange,
		portalTo
	}: Props = $props();

	/* Every layer this menu owns goes where this menu goes AND is dressed the way this menu is,
	   including the flyout a row opens, which is drawn by `ContextMenuItem` or `PickMenu` and has no
	   way of asking either question. See `menu-portal.ts`. */
	ownsTheMenu({ where: () => portalTo });

	/* Escape while the menu is on its way out belongs to the menu, not to the panel under it. See
	   `menu-closing.svelte.ts`. */
	const closingMenu = escapeWhileClosing();

	/* Whether the menu is open, for the shield under it. See `PageShield`. */
	let open = $state(false);

	/* What kind of press is on the trigger, for the `contextmenu` a long press raises. See
	   `menu-touch.ts`. */
	const press = touchPress();

	/* The tap that opens the sheet must not also choose a row. See `menu-touch.ts`. */
	const sheet = sheetPresses();

	/* Every way the menu opens or closes goes through here: the library's own, and the stroke down
	   the sheet's head, which the library does not hear of. */
	function changed(next: boolean): void {
		open = next;
		sheet.reset();
		closingMenu.changed(next);
		onOpenChange?.(next);
	}
</script>

<ContextMenu.Root bind:open onOpenChange={changed} onOpenChangeComplete={closingMenu.complete}>
	<!-- The library's trigger wiring, less its touch hold. See the header and `menu-touch.ts`. -->
	<ContextMenu.Trigger class={triggerClass}>
		{#snippet child({ props })}
			<div
				{...props}
				onpointerdown={(event: PointerEvent) => {
					press.down(event);
					if (libraryMaySee(event)) handOn(props.onpointerdown, event);
				}}
				oncontextmenu={(event: MouseEvent) => {
					if (refusesTheHold(event, press.kind)) return;
					handOn(props.oncontextmenu, event);
				}}
			>
				{@render children()}
			</div>
		{/snippet}
	</ContextMenu.Trigger>

	<ContextMenu.Portal to={portalTo ?? undefined}>
		<PageShield up={open || closingMenu.closing} />
		{#if phoneWidth.yes}
			<!-- A sheet from the foot of the screen, placed by this file's rule below rather than
			     floated against the press. See the header. -->
			<ContextMenu.ContentStatic
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
				<Scroller arrows>
					{@render items()}
				</Scroller>
			</ContextMenu.ContentStatic>
		{:else}
			<!-- `whole`: this menu takes the window's room as its ceiling (see the rule below), and
			     the padding keeps it off the window's edge when the library slides it to fit. -->
			<ContextMenu.Content {...WHOLE_MENU} aria-label={label}>
				<!-- The rows scroll like every other region in the app, so a menu longer than the
				     ceiling below never runs off the screen with unreachable rows. Every surface
				     wearing `.ui-menu` puts its rows in one of these. -->
				<Scroller arrows>
					{@render items()}
				</Scroller>
			</ContextMenu.Content>
		{/if}
	</ContextMenu.Portal>
</ContextMenu.Root>

<style>
	/*
	 * Portalled to the end of the document, so it is out of this component's markup by the time it
	 * exists and these rules have to be global to reach it.
	 *
	 * One menu: the compact rows on every list (the row tokens in `app.css`), on the surface every
	 * other floating layer has (this box, the select's, the combobox's).
	 */
	:global(.ui-menu) {
		z-index: var(--z-menu);
		min-inline-size: 180px;
		padding: var(--space-1);
		border-radius: var(--radius-lg);
		background: var(--sift-surface-3);
		box-shadow: var(--elev-3);
		/*
		 * It arrives with the motion every small surface opening shares, `rise` in `app.css`, which
		 * travels nothing under reduced motion, so no second rule is needed here for that.
		 */
		animation: rise var(--dur-fast) var(--ease);
	}

	/* The surface is not a control, so it does not wear a control's ring when the library puts focus
	   on it as it opens (the rows show where the keyboard is, `data-highlighted`). An accent ring
	   round the whole sheet would read as the sheet being chosen. Its own shadow, as
	   at rest. */
	:global(.ui-menu:focus-visible) {
		box-shadow: var(--elev-3);
	}

	/* And leaves by fading only, one pace quicker, the way everything leaves: the library keeps the
	   surface in the document while a closing animation runs. */
	:global(.ui-menu[data-state='closed']) {
		animation: leave var(--dur-instant) var(--ease-in) forwards;
	}

	/*
	 * THE CEILING, AND IT IS ON THE FLOATING MENUS ONLY.
	 *
	 * Without a ceiling a long menu (a wall's verbs, a folder list) would be drawn at whatever
	 * height its rows came to and run off the bottom of the window, where the rows past the edge
	 * could not be reached and nothing would say they existed. The smaller of the room the window
	 * has (`--bits-floating-available-height`, which the library measures for every floating layer)
	 * and the app's own ceiling, so a short window still bounds it.
	 *
	 * The grid is what makes the `Scroller` inside obey that ceiling rather than merely sit in a box
	 * with one: a `max-block-size` with no track to bound the child is a box the content paints
	 * straight out of. `check_capped_scroller.js` is the gate that knows this shape.
	 *
	 * Anchored at the floating wrapper (the element bits-ui portals every menu into) because
	 * `.ui-menu` is BORROWED by a list that is not floating: the search box draws its suggestions as
	 * a `<ul class="ui-menu">` inside its own popover, already inside a `Scroller` of its own, and a
	 * grid and a ceiling arriving from here would be this file redesigning that list at a distance.
	 * Written leading with our own class and asking about the ancestor inside `:is()`, the way
	 * `Select` does for fullscreen: a rule beginning with somebody else's attribute would be a rule
	 * about every floating layer in the application.
	 */
	:global(.ui-menu:is([data-bits-floating-content-wrapper] > *)) {
		display: grid;
		grid-template-rows: minmax(0, 1fr);
		max-block-size: min(
			var(--bits-floating-available-height, var(--menu-max-height)),
			var(--menu-max-height)
		);
	}

	/*
	 * The right-click menu itself opens WHOLE wherever the window can hold it. Its rows are the
	 * declared verbs of one thing, so the declaration bounds how many there are, and a menu that
	 * scrolls with room to spare hides its last rows (the destructive one among them) behind a
	 * scroll nobody expects. So its ceiling is the window's room alone: the library measures that
	 * as the window's whole height less `collisionPadding`, because it may slide the menu along the
	 * pointer's side, and it slides the menu up until the whole of it fits. A flyout and a chooser
	 * keep the shared ceiling above: those list the library's things, as many as the library has.
	 * The track is restated with the ceiling so the `Scroller` inside is still bounded by it.
	 */
	:global(.ui-menu.whole:is([data-bits-floating-content-wrapper] > *)) {
		grid-template-rows: minmax(0, 1fr);
		max-block-size: var(--bits-floating-available-height, var(--menu-max-height));
	}

	/*
	 * THE SHEET: every menu at a phone's width, this one and the three dots' (`MenuButton`).
	 *
	 * From the foot of the window, the whole width of it, its top corners rounded and its foot
	 * square against the edge, clear of the home indicator. As tall as its rows, up to the foot of the
	 * top bar, where it scrolls. The head names whose menu it is, in the caption size a menu group's
	 * heading wears, because a sheet covers what it was opened from and cannot point at it. The rows
	 * are a finger's height (the rule after this one).
	 *
	 * Not floated, so the library measures nothing for it: the placement is the whole of this rule.
	 * The scroll inside it stops at its ends rather than carrying on into the page under it.
	 *
	 * It rises out of the edge it stands on, the whole of its height, at the pace a sheet opens at,
	 * and sinks back into it one pace quicker (`sheet-in`, `sheet-out` in `app.css`): the motion of
	 * every sheet from the foot, the drawer's included. Under reduced motion it fades in and out
	 * where it stands, the rule after these.
	 */
	:global(.ui-menu.menu-sheet) {
		position: fixed;
		inset-inline: 0;
		inset-block-end: 0;
		display: grid;
		grid-template-rows: auto minmax(0, 1fr);
		max-block-size: calc(100dvh - var(--window-chrome) - var(--topbar-height));
		padding: var(--space-2) var(--space-2) calc(var(--space-2) + var(--safe-bottom));
		border-end-start-radius: 0;
		border-end-end-radius: 0;
		border-start-start-radius: var(--radius-xl);
		border-start-end-radius: var(--radius-xl);
		overscroll-behavior: contain;
		animation: sheet-in var(--dur-base) var(--ease);
	}

	:global(.ui-menu.menu-sheet[data-state='closed']) {
		animation: sheet-out var(--dur-fast) var(--ease-in) forwards;
	}

	:global(:root[data-motion='reduce'] .ui-menu.menu-sheet) {
		animation-name: appear;
	}

	:global(:root[data-motion='reduce'] .ui-menu.menu-sheet[data-state='closed']) {
		animation-name: leave;
	}

	/* The head is also where the sheet is put away: a finger drawn down it (see the header). So it is
	   a finger's height, its words at its foot beside the rows they name and the room above them to
	   take hold of, and it leaves that stroke to script: a stroke the browser took for a scroll of the
	   page under it would be cancelled half way and never finish. */
	:global(.ui-menu.menu-sheet > .menu-sheet-head) {
		margin: 0;
		min-block-size: var(--touch-target);
		align-content: end;
		touch-action: none;
		padding: var(--space-2) var(--space-2) var(--space-1);
		font: var(--text-label);
		color: var(--sift-ink-3);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	/*
	 * A finger's height for every menu row at a phone's width: the sheet's, and a flyout opened
	 * from one. The desktop's compact row is a mouse's, about 28 pixels.
	 */
	@media (max-width: 767px) {
		:global(.ui-menu .item) {
			min-block-size: var(--touch-target);
		}
	}

	/*
	 * The trigger is not a control, so it does not wear a control's focus ring.
	 *
	 * It is a wrapper: what can be operated is whatever was passed in, and that keeps its own ring.
	 * The wrapper carries `tabindex="-1"`, so no Tab lands on it; the one time it gets focus is the
	 * library putting focus back after its menu closes, and the global `:focus-visible` would then
	 * paint a ring around the whole trigger box, which on screens that hang a menu on their empty
	 * space is the entire window. `box-shadow` only: the outline is already none from the same
	 * global rule.
	 *
	 * This does not reach the rows inside a menu, `[data-context-menu-item]`, which are focusable
	 * and keep theirs.
	 */
	:global([data-context-menu-trigger]:focus-visible) {
		box-shadow: none;
	}

	/*
	 * And no browser menu on a long press. Safari on an iPhone raises its own callout (a link's
	 * preview, an image's Save) from a held finger and sends no `contextmenu` a script could refuse;
	 * a hold on anything carrying this menu is Sift's, and selects. Inherited, so it reaches the
	 * picture and the link inside the trigger.
	 */
	:global([data-context-menu-trigger]) {
		-webkit-touch-callout: none;
	}
</style>
