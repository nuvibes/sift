<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Scroller',
		category: 'surface',
		role: 'the one scrolling region, with the one scrollbar',
		basis: 'bits-ui:ScrollArea',
		states: ['default', 'with arrows']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* Everything that scrolls, scrolling the same way: the browser's own overflow, with the library's
	 * bar floating over the content so a scrolling box keeps its width. The caller styles a box
	 * inside; the viewport is the library's and scoped styles cannot reach it. */
	import type { Snippet } from 'svelte';
	import { ScrollArea } from 'bits-ui';
	import Icon from '$lib/components/Icon.svelte';
	import { nudgeDelay } from './scroll-nudge';

	/** What one line is worth, for a wheel that counts in them. The app's body line height. */
	const LINE_HEIGHT = 24;

	/** A pause this long ends one wheel gesture: a wheel has no end event. */
	const NEW_GESTURE_AFTER_MS = 250;

	interface Props {
		children: Snippet;
		/** When the bar exists; only `auto` scrolls by wheel and keyboard before a hover. */
		visibility?: 'auto' | 'hover' | 'scroll' | 'always';
		/** Also scroll sideways; a sideways-only strip takes the vertical wheel. */
		horizontal?: boolean;
		/**
		 * Make the content at least the viewport's height, as a column, on the library's wrapper.
		 */
		fill?: boolean;
		/** A class for code that has to find the scrolling element, not for styling. */
		viewportClass?: string;
		/** Handed the scrolling element once it exists, for a screen that has to measure it. */
		onviewport?: (element: HTMLElement) => void;
		/** An arrow at each end while there is more that way, for a list opening over the page. */
		arrows?: boolean;
	}

	let {
		children,
		visibility = 'auto',
		horizontal = false,
		fill = false,
		viewportClass,
		onviewport,
		arrows = false
	}: Props = $props();

	let viewport = $state<HTMLElement | null>(null);

	/** Whether there is list above / below what is on screen. Both false on a list that fits. */
	let canUp = $state(false);
	let canDown = $state(false);

	/* Half a pixel of slack: scroll positions are fractional at other zooms. */
	const AT_THE_END = 0.5;

	function measure(): void {
		const box = viewport;
		if (box === null) return;
		canUp = box.scrollTop > AT_THE_END;
		canDown = box.scrollTop < box.scrollHeight - box.clientHeight - AT_THE_END;
	}

	/* One nudge is one measured row, so it never lands mid-row. */
	function oneRow(box: HTMLElement): number {
		const row = box.querySelector<HTMLElement>('[role="option"], [role="menuitem"]');
		return row?.offsetHeight || LINE_HEIGHT;
	}

	/* A timer, since the wait between nudges eases (scroll-nudge.ts). */
	let held: ReturnType<typeof setTimeout> | null = null;

	function stopNudging(): void {
		if (held !== null) clearTimeout(held);
		held = null;
	}

	function nudge(way: -1 | 1): void {
		stopNudging();
		let tick = 0;
		const step = () => {
			const box = viewport;
			if (box === null) return;
			box.scrollTop += way * oneRow(box);
			measure();
			/* Stopping at the end rather than going on ringing a timer nobody can see. */
			if (way < 0 ? !canUp : !canDown) return;
			held = setTimeout(step, nudgeDelay(tick++));
		};
		/* One row on arrival, then the eased wait. */
		step();
	}

	/* Kept in step with the wheel, the keyboard, a shorter list and a resize. */
	$effect(() => {
		const box = viewport;
		if (box === null || !arrows) return;
		measure();
		const again = () => measure();
		box.addEventListener('scroll', again, { passive: true });
		const watching = new ResizeObserver(again);
		watching.observe(box);
		const inside = box.firstElementChild;
		if (inside !== null) watching.observe(inside);
		return () => {
			box.removeEventListener('scroll', again);
			watching.disconnect();
			stopNudging();
		};
	});

	$effect(() => {
		if (viewport) onviewport?.(viewport);
	});

	/* A vertical wheel over a sideways-only strip moves it sideways; a trackpad's deltaX is left
	   alone. A strip at its end gives the page any movement it was not already serving. */
	$effect(() => {
		const box = viewport;
		if (box === null || !horizontal) return;

		/* The movement being served, reset by a pause. */
		let lastTurnAt = 0;
		let holding = false;

		function sideways(event: WheelEvent) {
			if (box === null) return;
			// Already sideways, or not a turn at all.
			if (event.deltaX !== 0 || event.deltaY === 0) return;
			// Scrolls both ways: the wheel means what it always meant.
			if (box.scrollHeight - box.clientHeight > 1) return;

			// A pause ends the hold; the event's own timestamp, so a busy frame cannot split a
			// movement.
			if (event.timeStamp - lastTurnAt > NEW_GESTURE_AFTER_MS) holding = false;
			lastTurnAt = event.timeStamp;

			const by = wheelPixels(event, box.clientWidth);
			const furthest = box.scrollWidth - box.clientWidth;
			// At the end it is pushed towards (both ends, with nothing to scroll).
			const spent = by > 0 ? box.scrollLeft >= furthest - 1 : box.scrollLeft <= 0;
			if (spent) {
				// Only a movement this strip was serving is held back from the page.
				if (holding) event.preventDefault();
				return;
			}

			event.preventDefault();
			holding = true;
			box.scrollLeft += by;
		}

		box.addEventListener('wheel', sideways, { passive: false });
		return () => box.removeEventListener('wheel', sideways);
	});

	/** How far a wheel turned, in pixels: Firefox reports lines, some setups pages. */
	function wheelPixels(event: WheelEvent, across: number): number {
		if (event.deltaMode === 1) return event.deltaY * LINE_HEIGHT;
		if (event.deltaMode === 2) return event.deltaY * across;
		return event.deltaY;
	}
</script>

<!-- The root anchors the bar and never scrolls; the viewport inside scrolls. -->
<ScrollArea.Root type={visibility} class={fill ? 'scroll-root fills' : 'scroll-root'}>
	<!-- Pointer only, no tab stop: the keyboard already walks the list. -->
	{#if arrows && canUp}
		<button
			type="button"
			class="scroll-nudge"
			tabindex="-1"
			aria-hidden="true"
			onpointerenter={() => nudge(-1)}
			onpointerleave={stopNudging}
			onpointerdown={() => nudge(-1)}
			onpointerup={stopNudging}
		>
			<Icon name="expand_less" size={16} />
		</button>
	{/if}

	<ScrollArea.Viewport bind:ref={viewport} class={viewportClass}>
		{@render children()}
	</ScrollArea.Viewport>

	{#if arrows && canDown}
		<button
			type="button"
			class="scroll-nudge"
			tabindex="-1"
			aria-hidden="true"
			onpointerenter={() => nudge(1)}
			onpointerleave={stopNudging}
			onpointerdown={() => nudge(1)}
			onpointerup={stopNudging}
		>
			<Icon name="expand_more" size={16} />
		</button>
	{/if}

	<ScrollArea.Scrollbar orientation="vertical" class="scroll-bar vertical">
		<ScrollArea.Thumb class="scroll-thumb" />
	</ScrollArea.Scrollbar>

	{#if horizontal}
		<ScrollArea.Scrollbar orientation="horizontal" class="scroll-bar horizontal">
			<ScrollArea.Thumb class="scroll-thumb" />
		</ScrollArea.Scrollbar>
		<ScrollArea.Corner />
	{/if}
</ScrollArea.Root>

<style>
	/*
	 * Global throughout: the library renders every element. A column, so flex sizes the viewport.
	 */
	:global(.scroll-root) {
		position: relative;
		overflow: hidden;
		display: flex;
		flex-direction: column;
		block-size: 100%;
		min-block-size: 0;
	}

	/* The viewport told its height: flex from the root's used size with a 100% basis, since a
	   bare percentage resolves to auto under a shrunk root. */
	:global(.scroll-root > [data-scroll-area-viewport]) {
		flex: 1 1 auto;
		block-size: 100%;
		min-block-size: 0;
	}

	/* The arrow: a quiet full-width row, drawn only while there is somewhere to go. */
	:global(.scroll-nudge) {
		display: flex;
		flex: none;
		align-items: center;
		justify-content: center;
		padding-block: var(--space-1);
		padding-inline: 0;
		border: 0;
		border-radius: var(--menu-row-radius);
		background: transparent;
		color: var(--sift-ink-3);
		cursor: default;
		transition:
			background var(--dur-instant) var(--ease),
			color var(--dur-instant) var(--ease);
	}

	:global(.scroll-nudge:hover) {
		background: var(--menu-row-highlight);
		color: var(--sift-ink);
	}

	/* `fill`: the library's content box made a full-height column. */
	:global(.scroll-root.fills > [data-scroll-area-viewport] > [data-scroll-area-content]) {
		display: flex;
		flex-direction: column;
		min-block-size: 100%;
	}

	/* As app.css paints its scrollbar: ten wide, two inset, transparent track. */
	:global(.scroll-bar) {
		display: flex;
		touch-action: none;
		user-select: none;
		padding: 2px;
		background: transparent;
		opacity: 0;
		transition: opacity var(--dur-base) var(--ease);
	}

	:global(.scroll-bar[data-state='visible']) {
		opacity: 1;
	}

	:global(.scroll-bar.vertical) {
		inline-size: 10px;
		border-inline-start: 1px solid transparent;
	}

	:global(.scroll-bar.horizontal) {
		flex-direction: column;
		block-size: 10px;
		border-block-start: 1px solid transparent;
	}

	/* The painted scrollbar's two colours, so the two never differ on one screen. */
	:global(.scroll-thumb) {
		flex: 1;
		border-radius: var(--radius-full);
		background: var(--sift-surface-4);
		transition: background var(--dur-instant) var(--ease);
		/* A 6px pill is a small target. This grows the draggable area without widening the pill. */
		position: relative;
	}

	:global(.scroll-thumb)::before {
		content: '';
		position: absolute;
		inset: -6px;
	}

	:global(.scroll-thumb:hover) {
		background: var(--sift-line-strong);
	}

	:global(:root[data-motion='reduce']) :global(.scroll-bar) {
		transition: none;
	}
</style>
