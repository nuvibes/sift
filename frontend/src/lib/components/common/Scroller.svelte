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
	/*
	 * Everything in Sift that scrolls, scrolling the same way.
	 *
	 * ## What this changes, and what it deliberately does not
	 *
	 * It does NOT replace scrolling. The box inside still uses the browser's own overflow, so the
	 * wheel, momentum on a trackpad, the keyboard, scroll anchoring and touch are all exactly what
	 * they were: the library hides the site scrollbar and draws one that reads as part of this
	 * app instead. That distinction is the whole reason this was safe to put on the media grid,
	 * which is the one surface where taking real scrolling away would be felt immediately.
	 *
	 * ## What it adds over the rule the app already has
	 *
	 * Every scrollbar in Sift is already painted in the app's own colours: one rule in `app.css`
	 * does it for both browser families. So this is NOT here to make the app look consistent; the
	 * rule already does that.
	 *
	 * What it adds is that the bar stops taking part in layout. A painted site scrollbar is ten
	 * pixels of the container's own width, so a box that scrolls is ten pixels narrower inside than
	 * the same box when it does not, and the justified grid measures exactly that width to decide
	 * how many files fit a row. The library's bar floats over the content instead, so the measurement
	 * is the same whether or not there is anything to scroll.
	 *
	 * Which is also why the colours below repeat what `app.css` says rather than inventing anything.
	 * The two have to be indistinguishable: this one is on the frame body of every route and the
	 * painted one is on the panels inside them, and a person moving between them must not be able to
	 * tell that two mechanisms are in play.
	 *
	 * ## Why the caller cannot style the scrolling element
	 *
	 * The viewport is rendered by the library, so a class handed to it is not a class this app's
	 * scoped styles can reach: a rule written for it in the calling file matches nothing, silently.
	 * The way out is not `:global`; it is that the caller puts its padding, its measure and its
	 * layout on a box INSIDE, and hands over only the things this needs to know about. `viewportClass`
	 * exists for one thing only: a name other code can find the scrolling element by.
	 */
	import type { Snippet } from 'svelte';
	import { ScrollArea } from 'bits-ui';
	import Icon from '$lib/components/Icon.svelte';
	import { nudgeDelay } from './scroll-nudge';

	/** What one line is worth, for a wheel that counts in them. The app's body line height. */
	const LINE_HEIGHT = 24;

	/**
	 * How long a gap between wheel events ends one gesture and begins the next.
	 *
	 * A wheel does not say when somebody stopped turning it (there is no end event) so the only
	 * thing that separates one movement from the next is a pause. A mouse turning steadily fires
	 * every 50-100ms and a trackpad far more often than that, so a quarter of a second is well clear
	 * of "still going" and short enough that a deliberate second push is never mistaken for the
	 * first one continuing.
	 */
	const NEW_GESTURE_AFTER_MS = 250;

	interface Props {
		children: Snippet;
		/**
		 * When the scrollbar exists.
		 *
		 * `auto` is the default and is the only one that behaves like a scrollbar: the bar is there
		 * whenever there is something to scroll and absent when there is not, which is what every
		 * painted scrollbar in the app already does.
		 *
		 * The others are not merely cosmetic. The library makes the box scrollable only while the
		 * bar is MOUNTED, so `hover` means the wheel does nothing until the pointer has been inside
		 * the region and nothing at all for somebody scrolling by keyboard. It is offered because the
		 * library offers it; it is the wrong choice for any region holding a page of content.
		 */
		visibility?: 'auto' | 'hover' | 'scroll' | 'always';
		/** Also scroll sideways. Off by default: a region that scrolls both ways is nearly always a
		 *  layout that has not been told to wrap.
		 *
		 *  A region that scrolls ONLY sideways also takes the wheel, which is the whole of the
		 *  reason this is worth a note: most mice have one wheel and it goes up and down, so a strip
		 *  that answers only a sideways wheel cannot be scrolled at all by most people. See below. */
		horizontal?: boolean;
		/**
		 * Make the content at least as tall as the viewport, and lay it out as a column.
		 *
		 * For a region whose LAYOUT depends on there being leftover space: a sidebar that pushes its
		 * footer to the bottom, a panel with something set to take the slack. Without it the content
		 * is exactly as tall as what is in it, so the leftover space does not exist and everything
		 * bunches at the top the moment the region is taller than its contents.
		 *
		 * This cannot be done from the calling file, which is why it is a prop rather than a rule
		 * over there. The library renders a wrapper of its own between the scrolling box and the
		 * children; a `min-block-size: 100%` written by the caller lands on the caller's own box,
		 * inside that wrapper, and resolves against a height that is automatic, so it silently
		 * does nothing. The wrapper is the thing that has to be told, and this file is the only one
		 * that knows the wrapper exists.
		 */
		fill?: boolean;
		/**
		 * A class on the scrolling element, for code that has to FIND it, not for styling it.
		 *
		 * The grid measures one screenful of this box to decide how many rows a page holds, and the
		 * browser tests read its scroll position. Both need a name to look it up by.
		 */
		viewportClass?: string;
		/** Handed the scrolling element once it exists, for a screen that has to measure it. */
		onviewport?: (element: HTMLElement) => void;
		/**
		 * An arrow at each end while there is more list that way.
		 *
		 * For a list that opens over the page (a chooser, a menu, a flyout): a scrolling panel on a
		 * page has the page's scrollbar and visible movement under the wheel, while a list that
		 * appears under a control has neither until somebody turns the wheel over it. Off by
		 * default, the answer for every region that fills a screen: an arrow on the media wall
		 * would be a permanent chevron over the first row.
		 *
		 * Not the library's `Select.ScrollUpButton`: those buttons read the element
		 * `Select.Viewport` renders (`select.svelte.js`, `canScrollDown`), and in this app that
		 * element sits inside this region and does not scroll, so they would never appear. Here the
		 * arrow is beside the element that moves. The pace is the shared one: `nudgeDelay`.
		 */
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

	/* Half a pixel of slack at each end, because a scroll position is fractional on a display that
	   is not at 100% and an arrow that will not go away at the bottom of a list reads as broken. */
	const AT_THE_END = 0.5;

	function measure(): void {
		const box = viewport;
		if (box === null) return;
		canUp = box.scrollTop > AT_THE_END;
		canDown = box.scrollTop < box.scrollHeight - box.clientHeight - AT_THE_END;
	}

	/*
	 * What one nudge is worth: a row, measured, not a number written here. A menu row, a chooser's
	 * row and a tag suggestion differ in height, so a fixed distance would land mid-row, which is
	 * what an arrow should resolve. The roles are the site's own, so this asks the list what it is
	 * rather than reading a private attribute of the library's.
	 */
	function oneRow(box: HTMLElement): number {
		const row = box.querySelector<HTMLElement>('[role="option"], [role="menuitem"]');
		return row?.offsetHeight || LINE_HEIGHT;
	}

	/* The hold in progress, if there is one. A timer rather than an interval because the wait
	   between nudges CHANGES. See `scroll-nudge.ts`, which owns that easing for every arrow. */
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
		/* One row on arrival, then the eased wait. A pointer crossing the arrow on its way somewhere
		   else moves the list by exactly one row, which is the behaviour the delay is shaped for. */
		step();
	}

	/* Kept in step with the list, which changes under this from three directions: the wheel and the
	   keyboard move it, a filtered list is suddenly shorter, and the box itself is resized by the
	   window. A scroll listener answers the first, a resize observer on both boxes the other two. */
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

	/*
	 * A wheel over a strip that only scrolls sideways moves it sideways.
	 *
	 * A mouse wheel reports only a vertical distance, and over a strip with nothing to scroll
	 * vertically the browser applies it to the page instead. A trackpad reporting `deltaX` is left
	 * alone, since taking over would fight the gesture.
	 *
	 * Two guards stop this being a hijack. A box that can also scroll vertically keeps the wheel's
	 * own meaning. A strip already at the end it is pushed towards does not take the wheel, so a
	 * strip inside a scrolling page can be scrolled past.
	 *
	 * Reaching the end does not hand the page the rest of the same movement: the strip holds the
	 * wheel for the rest of a movement it has been scrolling, and stops holding after a
	 * quarter-second pause. The hold is only earned; a strip already at the end when a fresh
	 * movement arrives never takes it.
	 *
	 * Attached here rather than as an attribute because the element belongs to the library, this
	 * file holds the reference, and refusing the browser's handling needs an option an attribute
	 * cannot pass.
	 */
	$effect(() => {
		const box = viewport;
		if (box === null || !horizontal) return;

		/* The movement being served, and whether this strip is the one serving it. Reset by the
		   pause above rather than by any event, because a wheel has no end. */
		let lastTurnAt = 0;
		let holding = false;

		function sideways(event: WheelEvent) {
			if (box === null) return;
			// Already sideways, or not a turn at all.
			if (event.deltaX !== 0 || event.deltaY === 0) return;
			// Scrolls both ways: the wheel means what it always meant.
			if (box.scrollHeight - box.clientHeight > 1) return;

			// A gap long enough to be somebody stopping ends the hold, whether or not this strip
			// still has room. `event.timeStamp` rather than a clock: it is the moment the browser
			// recorded for the turn, so a busy frame cannot make one movement look like two.
			if (event.timeStamp - lastTurnAt > NEW_GESTURE_AFTER_MS) holding = false;
			lastTurnAt = event.timeStamp;

			const by = wheelPixels(event, box.clientWidth);
			const furthest = box.scrollWidth - box.clientWidth;
			// At the end it is being pushed towards, including a strip with nothing to scroll,
			// where both ends are the same place.
			const spent = by > 0 ? box.scrollLeft >= furthest - 1 : box.scrollLeft <= 0;
			if (spent) {
				// The rest of a movement this strip was already serving stops here rather than
				// going on to the page. A movement that arrived with the strip already at its end
				// was never ours, and the page gets it.
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

	/**
	 * How far a wheel turned, in pixels.
	 *
	 * A wheel does not have to report pixels. Firefox reports LINES and a few setups report PAGES,
	 * and adding either number to a scroll position as though it were pixels moves the strip by
	 * about three pixels a turn, which reads as the wheel not working rather than as a unit being
	 * wrong. The line figure is the app's own body line height; a page is the strip itself.
	 */
	function wheelPixels(event: WheelEvent, across: number): number {
		if (event.deltaMode === 1) return event.deltaY * LINE_HEIGHT;
		if (event.deltaMode === 2) return event.deltaY * across;
		return event.deltaY;
	}
</script>

<!--
	The root is the positioned box the scrollbar is drawn against; the viewport inside it is what
	actually scrolls. The root must not scroll itself, which is what `overflow: hidden` below is for.
-->
<ScrollArea.Root type={visibility} class={fill ? 'scroll-root fills' : 'scroll-root'}>
	<!-- Pointer only, and no tab stop. It is a shortcut for a hand: the keyboard already walks the
	     list with the arrow keys and brings each row into view on its own, so a second thing for Tab
	     to land on would be two stops before the list somebody came here to read. -->
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
	 * `:global` throughout: every element here is rendered by the library, so none carries this
	 * file's scoping hash and a plain rule would silently match nothing. The names (`scroll-root`,
	 * `scroll-bar`, `scroll-thumb`) are ours and handed to the library above, so the reach is this
	 * component's own markup.
	 *
	 * A column, so the viewport is sized by the flex algorithm rather than a percentage (see the
	 * rule under it). The library's scrollbars are `position: absolute`, so the viewport, with the
	 * arrows when asked for, is the only child in this layout.
	 */
	:global(.scroll-root) {
		position: relative;
		overflow: hidden;
		display: flex;
		flex-direction: column;
		block-size: 100%;
		min-block-size: 0;
	}

	/*
	 * The viewport has to be told how tall it is, and the library does not tell it: it sets the
	 * overflow only, so the box would grow to its content, have nothing to scroll, and the root's
	 * `overflow: hidden` would cut the rest off.
	 *
	 * `block-size: 100%` alone is not enough: a percentage height resolves only against a definite
	 * height, and otherwise becomes `auto`, so wherever the root's height came from being shrunk
	 * (as in the filter panel's column chooser) the viewport grows past it, the library mounts no
	 * scrollbar, and the rows past the edge are cut off.
	 *
	 * `flex: 1 1 auto` with the percentage kept as the base fixes it. Flex sizing works from the
	 * container's used size, known by the time its children are laid out, so the viewport shrinks
	 * to the root however the root got its height. The percentage base keeps a root taller than its
	 * content filled, and a root sized by its content unchanged; `flex-basis: 0` would collapse
	 * that case.
	 */
	:global(.scroll-root > [data-scroll-area-viewport]) {
		flex: 1 1 auto;
		block-size: 100%;
		min-block-size: 0;
	}

	/*
	 * The arrow at each end of a list too long for its box.
	 *
	 * A quiet full-width row rather than a floating chip: it is part of the list, it must not cover
	 * the row under it, and the ground it steps to on a pointer is the one every other row in a menu
	 * steps to. Drawn only while there is somewhere to go that way (see `canUp` / `canDown`) so
	 * on a list that fits there is nothing here at all.
	 */
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
		/* The ground steps rather than snapping, at the pace every other row uses. */
		transition:
			background var(--dur-instant) var(--ease),
			color var(--dur-instant) var(--ease);
	}

	:global(.scroll-nudge:hover) {
		background: var(--menu-row-highlight);
		color: var(--sift-ink);
	}

	/*
	 * `fill`: the content box is told to be as tall as the viewport, and to be a column.
	 *
	 * Two boxes down, and both are the library's. Neither carries this file's scoping hash, which is
	 * why this is a `:global` chain rather than a rule on something the caller can reach. See the
	 * `fill` prop for why the caller cannot write this itself.
	 */
	:global(.scroll-root.fills > [data-scroll-area-viewport] > [data-scroll-area-content]) {
		display: flex;
		flex-direction: column;
		min-block-size: 100%;
	}

	/* Ten wide with a two-pixel inset, which is what `app.css` gives the painted one. Transparent
	   track for the same reason it gives one: a scrolling box sits on a menu, a dialog and the page,
	   and a track painted any of those three is wrong on the other two. */
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

	/* The same two colours the painted scrollbar uses, in the same order. If these ever disagree,
	   the frame body's bar and the settings pane's bar are two different greys on one screen. */
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
