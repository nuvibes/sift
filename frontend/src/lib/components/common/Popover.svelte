<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Popover',
		category: 'surface',
		role: 'a panel that opens from a control and floats beside it, holding a small form or a few choices',
		basis: 'bits-ui:Popover; composes:Panel',
		states: ['closed', 'open', 'on hover', 'menu shape']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* A panel that opens from a control: opening and closing are bits-ui's, the look is Panel's and
	 * `surface`'s. Menus are not this; this is the one door to the library's Popover. */
	import type { Snippet } from 'svelte';
	import { Popover } from 'bits-ui';
	import Panel from '$lib/components/common/Panel.svelte';
	import PageShield from '$lib/components/common/PageShield.svelte';
	import { fromBar, fromEdge, surface } from '$lib/shell/motion.svelte';
	import Scroller from '$lib/components/common/Scroller.svelte';
	import { phoneWidth } from './phone-width.svelte';
	import { sheetPresses } from './menu-touch';
	import { strokes } from '$lib/components/player/swipe';
	import { closesWhenThePointerLeaves } from './popover-leave.svelte';

	interface Props {
		/** Whether it is showing. Bindable. */
		open?: boolean;
		onOpenChange?: (open: boolean) => void;
		/** Also opens while the pointer rests on the trigger, and stays while the panel is in use. */
		hover?: boolean;
		/** Which side of the trigger, and which edge to line up with. */
		side?: 'top' | 'bottom' | 'left' | 'right';
		align?: 'start' | 'center' | 'end';
		sideOffset?: number;
		/** Names the panel for a screen reader: "Add media". */
		label: string;
		/** The panel's width, fixed for a form; `auto` for a chooser as wide as its choices. */
		width?: string;
		/** How much room inside: `md` for a form, `sm` for a row of choices. */
		inset?: 'sm' | 'md';
		/**
		 * `menu` for a panel of menu rows: the menu's corner and inset, so rows stay concentric.
		 */
		shape?: 'panel' | 'menu';
		/** Where the open panel is drawn: the filled box while a screen fills the window. */
		portalTo?: Element | null;
		/** Hang from the top bar, coming out from under it as the Filter panel does. */
		fromTheBar?: boolean;
		/** The control that opens it. Spread `props` onto a Button or SplitButton. */
		trigger: Snippet<[{ props: Record<string, unknown> }]>;
		children: Snippet;
	}

	let {
		open = $bindable(false),
		onOpenChange,
		hover = false,
		side = 'bottom',
		align = 'end',
		sideOffset = 8,
		label,
		width = '320px',
		inset = 'md',
		shape = 'panel',
		portalTo = null,
		fromTheBar = false,
		trigger,
		children
	}: Props = $props();

	/* The opening tap must not also press what the sheet puts under it (menu-touch.ts). */
	const sheet = sheetPresses();

	/* How the floating panel arrives and leaves: a small surface's, or the bar's (see `fromTheBar`). */
	const arrives = (node: Element) => (fromTheBar ? fromBar(node) : surface(node));
	$effect(() => {
		if (!open) sheet.reset();
	});

	/* A hover panel closes when the pointer leaves, even after a pick (popover-leave.svelte.ts);
	   a press on the control means it was asked for, and it stays. */
	let panelBox = $state<HTMLElement | null>(null);
	/* Plain, not state: it is noted while the trigger is drawn and read only when the pointer moves. */
	let triggerId: string | null = null;
	let asked = $state(false);
	$effect(() => {
		if (!open) asked = false;
	});
	closesWhenThePointerLeaves({
		open: () => open,
		hover: () => hover,
		asked: () => asked,
		panel: () => panelBox,
		trigger: () => (triggerId ? document.getElementById(triggerId) : null),
		close: () => {
			open = false;
			onOpenChange?.(false);
		}
	});

	/* The library's own trigger wiring, with the press noted after it has been answered. */
	function noted(props: Record<string, unknown>): Record<string, unknown> {
		triggerId = typeof props.id === 'string' ? props.id : null;
		const pressed = props.onclick as ((event: MouseEvent) => void) | undefined;
		return {
			...props,
			onclick: (event: MouseEvent) => {
				pressed?.(event);
				queueMicrotask(() => (asked = open));
			}
		};
	}
</script>

<Popover.Root bind:open {onOpenChange}>
	<Popover.Trigger openOnHover={hover} openDelay={0} closeDelay={120}>
		{#snippet child({ props })}
			{@render trigger({ props: noted(props) })}
		{/snippet}
	</Popover.Trigger>

	<Popover.Portal to={portalTo ?? undefined}>
		<!-- A press outside closes it and reaches nothing under it, except over a hover panel. -->
		<PageShield up={open && !hover} />
		{#if phoneWidth.yes}
			<!-- At a phone's width a sheet from the foot, headed with its name, put away by a stroke down it.
			DRESSED BY: .ui-menu (ContextMenu styles the one menu surface)
			DRESSED BY: .menu-sheet (ContextMenu styles the sheet every menu is at a phone width)
			DRESSED BY: .menu-sheet-head (ContextMenu styles the sheet's head beside the sheet)
			It sinks back through `fromEdge`, as it leaves the page immediately. -->
			<Popover.ContentStatic forceMount>
				{#snippet child({ props, open: showing })}
					{#if showing}
						<div
							{...props}
							class="ui-menu menu-sheet"
							role="dialog"
							aria-label={label}
							onpointerdowncapture={sheet.down}
							onpointerupcapture={sheet.up}
							onclickcapture={sheet.click}
							out:fromEdge
						>
							<p
								class="menu-sheet-head"
								aria-hidden="true"
								{@attach strokes(() => ({ live: open, on: { down: () => (open = false) } }))}
							>
								{label}
							</p>
							<Scroller>
								<div class="sheet-body" class:rows={shape === 'menu'}>
									{@render children()}
								</div>
							</Scroller>
						</div>
					{/if}
				{/snippet}
			</Popover.ContentStatic>
		{:else}
			<!-- Rendered through `child`, so this file's rules and the transition reach it. -->
			<Popover.Content forceMount {side} {sideOffset} {align}>
				{#snippet child({ props, wrapperProps, open: showing })}
					{#if showing}
						<div {...wrapperProps}>
							<div
								{...props}
								class="popover-panel"
								bind:this={panelBox}
								role="dialog"
								aria-label={label}
								style:inline-size={width}
								transition:arrives
							>
								<Panel
									inset={shape === 'menu' ? 'xs' : inset}
									corner={shape === 'menu' ? 'lg' : 'md'}
									elevated
								>
									{@render children()}
								</Panel>
							</div>
						</div>
					{/if}
				{/snippet}
			</Popover.Content>
		{/if}
	</Popover.Portal>
</Popover.Root>

<style>
	/* The menu layer, so it opens over a dialog's sheet too. */
	.popover-panel {
		z-index: var(--z-menu);
	}

	/* A form keeps a panel's inset in the sheet; rows take the menu's. */
	.sheet-body {
		padding: var(--space-2) var(--space-2) var(--space-3);
	}

	.sheet-body.rows {
		padding: 0;
	}
</style>
