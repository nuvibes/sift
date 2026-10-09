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
	exist is a sheet that stays open while the page beside it is used. */
	/* A sheet from the right edge or the bottom, sliding in and back one pace quicker, fading under
	 * reduced motion. `beside: false` stands on PageShield (a press outside or Escape closes it);
	 * `beside: true` leaves the page live. From the bottom, a stroke down its head puts it away. */
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

	/* A beside drawer comes from the bottom on a phone, or the page reflows to one word wide. */
	const edge = $derived(beside && side === 'right' && phoneWidth.yes ? 'bottom' : side);

	function close(): void {
		open = false;
		onclose?.();
	}

	/*
	 * A beside drawer's width goes on the root as `--drawer-beside`, so the page reflows beside it.
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
	/* Below the top bar, on the menu layer over its shield; beside, on the popover layer. */
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
		/* `visibility` over the same duration holds it visible while sliding out. */
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
		/* Clear of a phone's home bar. */
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

	/* The head is a finger's height and leaves the stroke to script. */
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

	/* The list's scroll stops at its end rather than chaining into the page. */
	.body :global([data-scroll-area-viewport]) {
		overscroll-behavior: contain;
	}

	.foot {
		display: flex;
		justify-content: flex-end;
		gap: var(--space-2);
	}
</style>
