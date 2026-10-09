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
	cannot supply: the button's own dressing, written once. */

	/* A button that opens the app's menu, and nothing about its rows (RowMenu composes it). The
	   rows are ContextMenuItem, the same objects as the right-click menu's. */
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
		/** The button's and the menu's accessible name, saying whose menu ("More for orla"). */
		label: string;
		/** Words on the button instead of three dots, for a door not at the end of a row. */
		words?: string;
		/** Nothing can be opened just now: a request is in flight, or the row is being rebuilt. */
		disabled?: boolean;
		/** Whether this door scrolls its own contents: false for one composed thing that already
		 * scrolls (PickMenu inline), or two nested scrollers hand the track to the outer one. */
		scrolls?: boolean;
		/** How far the menu sits from the button; zero for a menu against a floating bar. */
		sideOffset?: number;
		/** Which way it opens, for a door in a bar at the window's foot. */
		side?: 'top' | 'bottom' | 'left' | 'right';
		/**
		 * Where the menu and its flyouts are drawn: the filled box while a screen fills the window.
		 */
		portalTo?: Element | null;
		/** Whether the menu is open, for a row that opens it on a plain press. Bindable. */
		open?: boolean;
		/** Told on open and close, for a caller holding one menu open at a time. */
		onOpenChange?: (open: boolean) => void;
		/** The door drawn by the caller (SplitButton's half), spreading the library's `props`. */
		trigger?: Snippet<[{ props: Record<string, unknown> }]>;
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
	 * A row's flyout goes and dresses as this door does, inside the filled box when there is one.
	 */
	ownsTheMenu({ where: () => portalTo });

	/* Escape while the menu leaves belongs to it (menu-closing.svelte.ts). */
	const closingMenu = escapeWhileClosing();

	/* The tap that opens the sheet must not also choose a row. See `menu-touch.ts`. */
	const sheet = sheetPresses();

	/* The press a chooser opens from, so its side can be read off the window at the press. */
	let door = $state<HTMLElement | null>(null);
	let placed = $state<'top' | 'bottom' | undefined>(undefined);

	/* A chooser's side, from the room at the press (see `chooserSide`); a whole menu needs none. */
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

	/* Every open and close goes through here, the sheet's stroke included. */
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
		<!--
		The shared Button, handed the trigger props through `child`; the chevron is `trailing`.
		-->
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
			<!-- At a phone's width the rows come up as a sheet; ContextMenu dresses it. -->
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
				<!--
				The rows scroll under the ceiling, unless the content scrolls itself (see
				`scrolls`).
				-->

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
	/* The button, dressed once; bounded by `menu-dots`, since `more` alone is other buttons too. */
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
		/* Light register: the dots do not snap to a new ground. */
		transition:
			background var(--dur-instant) var(--ease),
			color var(--dur-instant) var(--ease);
	}

	/* The state layer over no ground, so it answers on any surface. */
	:global(button.more.menu-dots:hover:not(:disabled)) {
		color: var(--hover-ink);
		background-color: color-mix(in srgb, currentColor var(--layer-hover), transparent);
	}

	/* The stronger layer while its menu is open. */
	:global(button.more.menu-dots:active:not(:disabled)),
	:global(button.more.menu-dots[data-state='open']) {
		color: var(--hover-ink);
		background-color: color-mix(in srgb, currentColor var(--layer-pressed), transparent);
	}

	:global(button.more.menu-dots:disabled) {
		opacity: var(--disabled-opacity);
		cursor: default;
	}

	/* A finger's size on a phone: the dots are a row's only menu door there. */
	@media (max-width: 767px) {
		:global(button.more.menu-dots) {
			inline-size: var(--touch-target);
			block-size: var(--touch-target);
		}
	}
</style>
