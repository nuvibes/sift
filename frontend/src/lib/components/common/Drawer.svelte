<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Drawer',
		category: 'surface',
		role: 'a sheet that slides in from the right edge or the bottom and stays while the page is used',
		basis: 'composes:PageShield,Scroller',
		states: ['right', 'bottom', 'beside the page', 'shut']
	} satisfies DesignEntry;

	/** Which edge it comes in from. */
	export type DrawerSide = 'right' | 'bottom';
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: the library's Dialog is modal and traps focus, and the drawer's reason to
	   exist is the case it cannot serve: a sheet that stays open while the page beside it is used
	   (a mode's picks, built by pressing the page). The shield below is Sift's own floating layer. */
	/*
	 * A sheet from the edge of the window: the right edge, or the bottom on a narrow window.
	 *
	 * It MOVES in over `--dur-base` on `--ease` and goes back into its edge one pace quicker,
	 * `--dur-fast` on `--ease-in`, the way every sheet from an edge leaves (`sheet-out` in
	 * `app.css`), and it is held in the DOM while shut so the leaving is seen: a transform and a
	 * `visibility` withheld by the same duration, the arrangement the filter bar's drawer uses.
	 * Under reduced motion it does not travel at all: it fades in and out where it stands.
	 *
	 * Two ways to stand over the page, and the caller says which:
	 *
	 * - `beside`: false (the default) is a floating surface like any other. It stands on the clear
	 *   `PageShield`, so a press outside closes it and reaches nothing underneath, and Escape
	 *   closes it too.
	 * - `beside`: true leaves the page live, because the page is what is being worked. A mode's
	 *   drawer is this: every press on the wall picks something into the drawer, and a shield
	 *   would take exactly those presses. It is closed only by what is in it.
	 *
	 * From the bottom, a finger drawn down its head puts it away as well, the way every phone's sheet
	 * goes: the same stroke, read by the same rules, as the viewer's sideways step (`strokes` in
	 * `player/swipe.ts`), so a quick straight stroke means one thing everywhere. Not on a drawer
	 * beside the page, which only what is in it closes.
	 */
	import type { Snippet } from 'svelte';
	import PageShield from './PageShield.svelte';
	import Scroller from './Scroller.svelte';
	import { phoneWidth } from './phone-width.svelte';
	import { strokes } from '$lib/components/player/swipe';

	interface Props {
		open?: boolean;
		side?: DrawerSide;
		/** What it is, for a screen reader, and the heading drawn at its head. */
		label: string;
		/** Leave the page pressable while it is open. See the header. */
		beside?: boolean;
		/** Told when a press outside or Escape closes it. A beside drawer never hears either. */
		onclose?: () => void;
		children: Snippet;
		/** The row of buttons at its foot, kept in view while the rest scrolls. */
		footer?: Snippet;
	}

	let {
		open = $bindable(false),
		side = 'right',
		label,
		beside = false,
		onclose,
		children,
		footer
	}: Props = $props();

	let sheet = $state<HTMLElement | null>(null);

	/*
	 * The edge it comes from. A drawer beside the page at a phone's width comes from the BOTTOM,
	 * whatever the caller asked for: from the right it takes 22rem of a 24rem screen off the page it
	 * leaves live, and that page would reflow into a column one word wide. Decided here rather than
	 * by each caller, because a caller is what would forget.
	 */
	const edge = $derived(beside && side === 'right' && phoneWidth.yes ? 'bottom' : side);

	function close(): void {
		open = false;
		onclose?.();
	}

	/*
	 * A drawer beside the page takes its room from the page rather than covering the right-hand side
	 * of it: its measured width goes on the root as `--drawer-beside`, which the layout's main column
	 * keeps clear, so a wall reflows beside it instead of hiding a column behind it. One measure,
	 * read off the drawer itself, so the two cannot come to disagree about how wide it is.
	 */
	$effect(() => {
		if (!open || !beside || edge !== 'right' || !sheet) return;
		const root = document.documentElement;
		const target = sheet;
		const measure = () =>
			root.style.setProperty('--drawer-beside', `${target.getBoundingClientRect().width}px`);
		measure();
		const watch = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
		watch?.observe(target);
		return () => {
			watch?.disconnect();
			root.style.removeProperty('--drawer-beside');
		};
	});

	/* A press outside, heard on the way down so it is heard before the shield's own handling. */
	$effect(() => {
		if (!open || beside) return;
		const press = (event: PointerEvent) => {
			if (sheet && event.target instanceof Node && !sheet.contains(event.target)) close();
		};
		const key = (event: KeyboardEvent) => {
			if (event.key !== 'Escape' || event.defaultPrevented) return;
			// Answered here, so a viewer under the sheet listening on the same window does not close too.
			event.preventDefault();
			close();
		};
		window.addEventListener('pointerdown', press, true);
		window.addEventListener('keydown', key);
		return () => {
			window.removeEventListener('pointerdown', press, true);
			window.removeEventListener('keydown', key);
		};
	});
</script>

{#if !beside}
	<PageShield up={open} />
{/if}

<aside
	bind:this={sheet}
	class="drawer"
	data-side={edge}
	class:open
	class:beside
	aria-label={label}
	aria-hidden={!open}
	inert={!open}
>
	<h2
		class="head"
		{@attach strokes(() => ({
			live: open && edge === 'bottom' && !beside,
			on: { down: close }
		}))}
	>
		{label}
	</h2>
	<div class="body"><Scroller fill>{@render children()}</Scroller></div>
	{#if footer}
		<div class="foot">{@render footer()}</div>
	{/if}
</aside>

<style>
	/*
	 * Below the top bar, so the bar's own controls (the one that opened it among them) stay where
	 * they are, and on the menu layer over its shield; a drawer beside the page sits on the popover
	 * layer instead, under every menu and dialog the page it leaves live can still open.
	 */
	.drawer {
		--drawer-top: calc(var(--window-chrome) + var(--topbar-height) + var(--safe-top));
		position: fixed;
		z-index: var(--z-menu);
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		padding: var(--space-4);
		background: var(--sift-surface-2);
		box-shadow: var(--elev-3);
		visibility: hidden;
		/* `visibility` transitioned over the same duration keeps the sheet visible for the whole of
		   the slide out and shows it at the first frame of the slide in: no literal delay needed. */
		transition:
			transform var(--dur-fast) var(--ease-in),
			visibility var(--dur-fast) linear;
	}

	.drawer.beside {
		z-index: var(--z-popover);
	}

	.drawer[data-side='right'] {
		inset-block: var(--drawer-top) 0;
		inset-inline-end: 0;
		inline-size: min(var(--drawer-width), 100%);
		border-start-start-radius: var(--radius-xl);
		border-end-start-radius: var(--radius-xl);
		transform: translateX(100%);
	}

	.drawer[data-side='bottom'] {
		inset-inline: 0;
		inset-block-end: 0;
		block-size: min(var(--drawer-height), 100%);
		/* Clear of a phone's home bar: the last row is never under it. Added to the inset every other
		   side keeps, as a menu's sheet and a Settings section add it: in place of it, the last row
		   would stand on the very edge of any screen with no home bar. */
		padding-block-end: calc(var(--space-4) + var(--safe-bottom));
		border-start-start-radius: var(--radius-xl);
		border-start-end-radius: var(--radius-xl);
		transform: translateY(100%);
	}

	.drawer.open {
		visibility: visible;
		transform: none;
		transition:
			transform var(--dur-base) var(--ease),
			visibility var(--dur-base) linear;
	}

	/* Reduced motion: no travel, a fade in place. The root caps the durations already. */
	:global(:root[data-motion='reduce']) .drawer {
		opacity: 0;
		transform: none;
		transition:
			opacity var(--dur-fast) var(--ease-in),
			visibility var(--dur-fast) linear;
	}

	:global(:root[data-motion='reduce']) .drawer.open {
		opacity: 1;
		transition:
			opacity var(--dur-base) var(--ease),
			visibility var(--dur-base) linear;
	}

	.head {
		margin: 0;
		font: var(--text-h2);
		color: var(--sift-ink);
		/* A name in a heading can be one unbroken token; it breaks rather than leaving the box. */
		overflow-wrap: anywhere;
	}

	/* A sheet from the bottom is put away by a finger drawn down its head, so the head is a finger's
	   height and leaves that stroke to script: a stroke the browser took for a scroll of the page
	   under it would be cancelled half way and never finish. */
	.drawer[data-side='bottom'] .head {
		display: flex;
		align-items: center;
		min-block-size: var(--touch-target);
		touch-action: none;
	}

	/* The list scrolls, through the one scroller; the head and the foot stay. */
	.body {
		display: flex;
		flex: 1;
		flex-direction: column;
		min-block-size: 0;
	}

	/* A sheet's list scrolls on its own: at its end a finger's stroke stops there, rather than
	   carrying on into the page under the shield (the page's rubber band, or a pull to refresh
	   that reloads Sift under an open sheet). On the scroller's own box, since that is what the
	   browser chains from; the sheet around it never scrolls. */
	.body :global([data-scroll-area-viewport]) {
		overscroll-behavior: contain;
	}

	.foot {
		display: flex;
		justify-content: flex-end;
		gap: var(--space-2);
	}
</style>
