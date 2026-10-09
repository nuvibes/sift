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
	/* Right-click a thing, get what can be done to it, on the library's menu. A hold never opens
	 * it (a hold selects; see menu-touch.ts). At a phone's width it is a sheet from the foot, put
	 * away by a stroke down its head. */
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
		/** A class for the trigger's wrapper, for a caller whose parent lays it out. */
		triggerClass?: string;
		/** Told on open and close, for a caller marking what the menu is about. */
		onOpenChange?: (open: boolean) => void;
		/** Where the open menu is drawn: the filled box while a screen fills the window. */
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

	/*
	 * Every layer this menu owns, flyouts included, goes and dresses as it does (menu-portal.ts).
	 */
	ownsTheMenu({ where: () => portalTo });

	/* Escape while the menu leaves belongs to it (menu-closing.svelte.ts). */
	const closingMenu = escapeWhileClosing();

	/* Whether the menu is open, for the shield under it. See `PageShield`. */
	let open = $state(false);

	/* The kind of press on the trigger, for a long press's contextmenu (menu-touch.ts). */
	const press = touchPress();

	/* The tap that opens the sheet must not also choose a row. See `menu-touch.ts`. */
	const sheet = sheetPresses();

	/* Every open and close goes through here, the sheet's stroke included. */
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
			<!-- A sheet from the foot, placed by this file's rule. -->
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
			<!-- whole: the window's room is its ceiling (see the rule below). -->
			<ContextMenu.Content {...WHOLE_MENU} aria-label={label}>
				<!-- The rows scroll, so a long menu never runs off the screen. -->
				<Scroller arrows>
					{@render items()}
				</Scroller>
			</ContextMenu.Content>
		{/if}
	</ContextMenu.Portal>
</ContextMenu.Root>

<style>
	/* Global: the menu is portalled out of this markup. */
	:global(.ui-menu) {
		z-index: var(--z-menu);
		min-inline-size: 180px;
		padding: var(--space-1);
		border-radius: var(--radius-lg);
		background: var(--sift-surface-3);
		box-shadow: var(--elev-3);
		/* `rise` travels nothing under reduced motion. */
		animation: rise var(--dur-fast) var(--ease);
	}

	/* No control ring on the surface: the rows show where the keyboard is. */
	:global(.ui-menu:focus-visible) {
		box-shadow: var(--elev-3);
	}

	/* Leaves by fading, one pace quicker. */
	:global(.ui-menu[data-state='closed']) {
		animation: leave var(--dur-instant) var(--ease-in) forwards;
	}

	/* The ceiling, on floating menus only: the window's room or the app's, with a grid so the
	   Scroller obeys it (check_capped_scroller.js). Not on a borrowed `.ui-menu` list. */
	:global(.ui-menu:is([data-bits-floating-content-wrapper] > *)) {
		display: grid;
		grid-template-rows: minmax(0, 1fr);
		max-block-size: min(
			var(--bits-floating-available-height, var(--menu-max-height)),
			var(--menu-max-height)
		);
	}

	/* The right-click menu opens whole where the window allows: a scroll hides its last rows. */
	:global(.ui-menu.whole:is([data-bits-floating-content-wrapper] > *)) {
		grid-template-rows: minmax(0, 1fr);
		max-block-size: var(--bits-floating-available-height, var(--menu-max-height));
	}

	/* The sheet: every menu at a phone's width, from the window's foot, rising out of that edge. */
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

	/* The head takes the stroke that puts it away: a finger's height, and script's alone. */
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

	/* A finger's height for every menu row at a phone's width. */
	@media (max-width: 767px) {
		:global(.ui-menu .item) {
			min-block-size: var(--touch-target);
		}
	}

	/* The trigger is a wrapper, not a control: no ring when focus returns to it after closing. */
	:global([data-context-menu-trigger]:focus-visible) {
		box-shadow: none;
	}

	/* No browser callout on a long press (iPhone Safari raises no refusable contextmenu). */
	:global([data-context-menu-trigger]) {
		-webkit-touch-callout: none;
	}
</style>
