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
	/*
	 * A panel that opens from a control.
	 *
	 * ## What is the library's and what is Sift's
	 *
	 * Opening and closing are the hard part and they are bits-ui's: click-outside, Escape, focus
	 * into the panel and back out, the safe polygon a pointer crosses on its way from the button to
	 * the panel, and, when asked, opening on hover without stealing focus every time a pointer
	 * crosses the button. Rebuilt by hand it takes timers and flags standing in for "the panel is
	 * in use", which the library already knows.
	 *
	 * What is Sift's is the look: the panel is `Panel` (raised, inset md, corner md, elevated) and
	 * it opens and closes as every small surface does (`surface`), so a popover, a menu and a toast
	 * move alike. Menus are NOT this: a list of rows is `MenuButton` / `RowMenu` / `ContextMenu`.
	 *
	 * The fence refuses the library outside the primitives, so this is the one door to Popover.
	 */
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
		/** How wide the panel is. A popover is a fixed-width surface; the form inside fills it. `auto` for a chooser that is as wide as its choices. */
		width?: string;
		/** How much room inside: `md` for a form, `sm` for a row of choices. */
		inset?: 'sm' | 'md';
		/**
		 * `menu` for a panel whose contents are MENU ROWS: a chooser drawn as a column of rows,
		 * like the rating chooser behind a star.
		 *
		 * Rows carry `--menu-row-radius`, which is the menu's corner less the menu's inset, so they
		 * are concentric only inside a box with exactly that corner and that inset. The plain panel's
		 * `md` corner and `sm` inset leave a row radius of two pixels in the geometry, which reads
		 * as a rectangle in a rounded box. So `menu` takes the menu's own pair (the `lg` corner and
		 * the `xs` inset) and `inset` above is not read. A shape rather than two more props because
		 * the two only ever mean anything together.
		 */
		shape?: 'panel' | 'menu';
		/**
		 * Where the open panel is drawn, when the end of the document is the wrong answer.
		 *
		 * It normally is the right answer: a portalled layer escapes every `overflow: hidden` and
		 * every stacking context between it and the trigger, which is the whole reason to portal.
		 *
		 * Fullscreen is the exception, and it is not a styling problem. A browser filling the screen
		 * draws ONLY the fullscreened element and its descendants: everything else in the document
		 * is not hidden, it is not drawn. So a panel at the end of `body` simply does not exist on
		 * screen while a player is fullscreen: the button opens it, it takes the keyboard, and there
		 * is nothing to see. Handing the fullscreened element in puts the panel back inside the
		 * subtree that is being painted. The same door `Select.portalTo` and `ContextMenu.portalTo`
		 * opened, for the same reason and with the same name.
		 */
		portalTo?: Element | null;
		/**
		 * A panel hanging from the top bar: it comes out from under the bar and goes back up under
		 * it, as the Filter panel does (`fromBar`), rather than rising as a small surface. Add's.
		 */
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

	/* The tap that opens the sheet on a phone must not also press what the sheet puts under it:
	   the menu sheet's own rule, for the same reason (`menu-touch.ts`). */
	const sheet = sheetPresses();

	/* How the floating panel arrives and leaves: a small surface's, or the bar's (see `fromTheBar`). */
	const arrives = (node: Element) => (fromTheBar ? fromBar(node) : surface(node));
	$effect(() => {
		if (!open) sheet.reset();
	});

	/*
	 * A hover panel closes when the pointer leaves it, after a pick inside it too. The library stops
	 * answering a leaving at the first press inside the panel; see `popover-leave.svelte.ts`.
	 *
	 * `asked` is a press on the control itself, read after the library has answered it: open
	 * after the press means somebody asked for the panel, and it stays until they put it away.
	 */
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
		<!--
			A press outside closes the panel and reaches nothing under it (see `PageShield`), except
			on a panel opened by hover: a sheet over its own trigger would read as the pointer leaving
			it, and the panel would shut under a resting hand.
		-->
		<PageShield up={open && !hover} />
		{#if phoneWidth.yes}
			<!-- AT A PHONE'S WIDTH, A SHEET FROM THE FOOT, as every menu is there: a panel floated
			     beside its trigger on a screen one panel wide lands against the edge and over what it
			     was opened from, and a chooser of rows is a menu by any other name. Headed with its
			     own name, since it covers what opened it; put away by a finger drawn down the head.
			     DRESSED BY: .ui-menu (ContextMenu styles the one menu surface)
			     DRESSED BY: .menu-sheet (ContextMenu styles the sheet every menu is at a phone width)
			     DRESSED BY: .menu-sheet-head (ContextMenu styles the sheet's head beside the sheet)
			     It rises by the sheet's own rule, and sinks back through `fromEdge`: this element is
			     this file's and leaves the page at once, where the library's sheets are held for a
			     closing animation. -->
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
			<!-- Rendered by this file through the library's `child`, the way Modal renders its sheet: a
		     real element that this file's rules and the surface transition can reach. `wrapperProps`
		     is the library's positioned layer; the panel goes inside it. -->
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
	/* The menu layer, not the popover one: a popover opens from a chip inside a dialog as readily as
	   from the top bar, and a dialog's sheet sits above the popover layer. Menus made the same call. */
	.popover-panel {
		z-index: var(--z-menu);
	}

	/* A form in the sheet keeps a panel's inset from the sheet's edge; a column of rows takes the
	   menu's, which the sheet already has. */
	.sheet-body {
		padding: var(--space-2) var(--space-2) var(--space-3);
	}

	.sheet-body.rows {
		padding: 0;
	}
</style>
