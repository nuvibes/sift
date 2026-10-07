<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Tooltip',
		category: 'primitive',
		role: 'the name of a control, shown on hover and on focus, never under a stationary pointer',
		basis: 'own',
		states: ['closed', 'open', 'with a shortcut']
	} satisfies DesignEntry;

	/*
	 * WHETHER THE LAST PRESS WAS A FINGER. A touch never leaves a label behind: a finger has no hover
	 * to take it away again, so a label a tap raised would stay over whatever the tap opened (the
	 * More sheet) until something else was pressed. A finger's moves never show one, and neither
	 * does focus that follows a finger (a sheet closing hands focus back to its trigger, and a
	 * phone's browser can call that focus visible). One reading for every tooltip, kept by the
	 * window: a key pressed afterwards is a keyboard again and clears it.
	 */
	let fingerLast = false;
	if (typeof window !== 'undefined') {
		window.addEventListener(
			'pointerdown',
			(event) => (fingerLast = event.pointerType === 'touch'),
			true
		);
		window.addEventListener('keydown', () => (fingerLast = false), true);
	}
</script>

<script lang="ts">
	/*
	 * WHY NOT BITS-UI: it opens on `pointerenter`, and this one deliberately does not.
	 *
	 * Checked in `tooltip.svelte.js`. The library solves one of the two faults this file handles:
	 * its focus handler gates on `isFocusVisible` behind an `ignoreNonKeyboardFocus` prop, the same
	 * fix as below. It does not solve the other, and reintroduces it: its trigger listens on
	 * `pointerenter` as well as `pointermove`, and an element appearing under a stationary pointer
	 * fires enter, so a panel that closes and rebuilds the control it was opened from would put the
	 * label back up with nobody hovering, through the library's delay.
	 *
	 * A short label for a control that does not carry its own words.
	 *
	 * The browser's version, the `title` attribute, is drawn by the operating system in its own
	 * look, appears about a second after the pointer stops, and cannot be reached by keyboard or
	 * touch, so a control labelled only by `title` is one some people cannot read. This is shown on
	 * hover and on focus, dismissed on Escape, and described to assistive technology through
	 * `aria-describedby`.
	 *
	 * It labels; it does not explain. Help text for a form field goes under the field where it can
	 * be read without hovering; a tooltip is the wrong place for anything needed to answer a
	 * question.
	 */
	import { keysFor } from '$lib/shell/shortcuts';
	import type { Snippet } from 'svelte';
	import { motion } from '$lib/shell/motion.svelte';
	import { untrack } from 'svelte';

	interface Props {
		/**
		 * The words. Keep them to a few.
		 *
		 * May be EMPTY, and only in one case: when `detail` is what the bubble is saying. A kept
		 * filter's bubble is a row of chips describing the filter, and a heading over them would be
		 * a caption on a picture that has already said it, and the widest thing in the bubble.
		 * Empty, no words are drawn and the detail takes the whole bubble.
		 *
		 * Empty with no `detail` either is a bubble with nothing in it. Nothing enforces that here
		 * because it cannot be seen from inside a component, and it would be plainly visible to
		 * anybody who looked at what they wrote.
		 */
		label: string;
		/**
		 * A first word carrying its own colour: "Shared", "Restricted".
		 *
		 * For the marks, where the status word means something on its own and the rest of the line
		 * is the explanation. It is a separate prop rather than markup in the label because a
		 * tooltip that took markup is a tooltip somebody eventually puts a control in.
		 */
		lead?: string;
		/**
		 * Let the detail be as wide as it needs, rather than capping it at a line of chips.
		 *
		 * The cap exists for the bubble this was written for (a row of chips, which wraps) and a
		 * PICTURE cannot wrap: a saved wall's diagram inside the capped box would draw its blocks
		 * outside the bubble, over whatever was behind it. Opt-in, so the ordinary bubble keeps the
		 * measure that makes it readable.
		 */
		wide?: boolean;
		/** Which colour the lead wears. */
		tone?: 'share' | 'restrict' | 'both' | 'hidden';
		/** Which side of the control to sit on. */
		placement?: 'top' | 'bottom' | 'right';
		/**
		 * A second line under the words, drawn as markup.
		 *
		 * Description only; nothing in here may be operated. A tooltip is portalled, not focusable,
		 * and vanishes when the pointer leaves its owner, so a control in one is unreachable by
		 * keyboard and by most mice. Markup is allowed because a saved filter has to show what it
		 * holds before it is pressed, and a row of chips is not something a string can say. So:
		 * chips, marks, a swatch. Never a button, a link, a field or anything with a handler.
		 */
		detail?: Snippet;
		/**
		 * Fill the width the wrapper was given.
		 *
		 * The wrapper is `inline-flex`, which shrink-wraps its contents. Around something that was
		 * stretching to fill a column (a nav row whose whole width highlights on hover) that
		 * silently collapses it to the width of its text.
		 */
		stretch?: boolean;
		/**
		 * Let the control inside be squeezed below its content, for a control that ellipsizes.
		 *
		 * These two boxes are `inline-flex`, so as a flex or grid item each resolves `min-width:
		 * auto`, the content's min-content width. Inside something that ellipsizes that is the
		 * whole string (`nowrap` leaves it nowhere to break), so the wrapper refuses to go below
		 * the full text and no ellipsis happens. `min-inline-size: 0` allows it.
		 *
		 * Opt-in, because a floor of zero around a fixed-size icon button lays the wrapper out at
		 * almost nothing and the buttons paint over one another. Only the caller knows whether what
		 * it wraps can give ground: text that ellipsizes can, a glyph cannot. Off by default.
		 */
		shrinks?: boolean;
		/**
		 * The id of a shortcut this control also answers to, whose keys are appended.
		 *
		 * A tooltip is always for "keyboard shortcuts, appended to the verb's own label", and
		 * shortcuts are shown rather than remembered. The KEYS
		 * are never written here: an id is looked up in the one list that declares them, so a key
		 * cannot drift out of step with the button that names it.
		 *
		 * An id nothing declares appends nothing, rather than leaving a dangling separator.
		 */
		shortcut?: string;
		/**
		 * Keep the label up when the control is pressed, for a control whose answer is the label.
		 *
		 * Pressing normally takes the bubble away: a control that opens something over itself
		 * carries the pointer out of the picture without crossing its edge, so no `pointerleave`
		 * fires and the label would be left behind whatever opened. Some controls answer in the
		 * label instead: the file name in the popout copies itself, and the tooltip is the only
		 * thing that says it worked ("Copy the filename", then "Copied").
		 *
		 * Opt-in: the default keeps the bubble off the back of a dialog, and a flag needed for the
		 * safe behaviour would be forgotten. Set it only where pressing opens nothing over the
		 * control and changes the label instead.
		 */
		staysOnPress?: boolean;
		/** Shown while true, whatever the pointer and focus do: a list row the keyboard highlighted. */
		held?: boolean;
		/** The control being labelled. */
		children: Snippet;
	}

	let {
		label,
		lead,
		tone,
		placement = 'top',
		stretch = false,
		shrinks = false,
		shortcut,
		staysOnPress = false,
		held = false,
		children,
		detail,
		wide = false
	}: Props = $props();

	const keys = $derived(shortcut ? keysFor(shortcut) : null);

	let open = $state(false);
	let id = $props.id();

	// The bubble is rendered into <body>, not next to the control, so it is never clipped by a
	// scrolling ancestor (the rail scrolls, and an absolutely-placed bubble there would drag a
	// horizontal scrollbar onto it) and never trapped behind a neighbour's stacking context. That
	// means it cannot inherit a position from the DOM and has to be placed against the control's
	// measured rectangle instead.
	let wrapEl: HTMLElement;
	let bubbleEl = $state<HTMLElement | undefined>();
	let x = $state(0);
	let y = $state(0);
	let placed = $state(false);

	/*
	 * Into `<body>`, unless a screen is filling the window, and then into that. A fullscreen
	 * browser paints only the fullscreened element's subtree, so a bubble appended to `body` would
	 * open, be placed, and show nothing.
	 *
	 * Asked at mount rather than tracked, like every other live question here: the bubble is
	 * created when it opens and destroyed when it closes, so it is never in the wrong place for
	 * longer than it is on screen. `document.fullscreenElement` rather than a store, because a
	 * shared primitive must not learn which screens can be filled.
	 */
	function portal(node: HTMLElement) {
		const filled = typeof document === 'undefined' ? null : document.fullscreenElement;
		(filled ?? document.body).appendChild(node);
		return {
			destroy() {
				node.remove();
			}
		};
	}

	const GAP = 8; // matches --space-2, the resting offset from the control
	const EDGE = 4; // keep the bubble this far off the viewport edge

	function place() {
		if (!wrapEl || !bubbleEl) return;
		const t = wrapEl.getBoundingClientRect();
		const b = bubbleEl.getBoundingClientRect();
		let left: number;
		let top: number;
		// The window's own title strip sits above this layer, so the top of the usable area is the
		// strip's foot, not the window edge: a label clamped to the edge is drawn under the strip.
		const chrome =
			parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--window-chrome')) ||
			0;
		const ceiling = chrome + EDGE;
		const floor = window.innerHeight - b.height - EDGE;
		let side = placement;
		// A side with no room flips to the other before anything is clamped: clamping alone slides
		// the bubble over the control it labels.
		if (side === 'top' && t.top - b.height - GAP < ceiling) side = 'bottom';
		else if (side === 'bottom' && t.bottom + GAP > floor) side = 'top';
		if (side === 'right') {
			left = t.right + GAP;
			top = t.top + (t.height - b.height) / 2;
		} else if (side === 'bottom') {
			top = t.bottom + GAP;
			left = t.left + (t.width - b.width) / 2;
		} else {
			top = t.top - b.height - GAP;
			left = t.left + (t.width - b.width) / 2;
		}
		x = Math.max(EDGE, Math.min(left, window.innerWidth - b.width - EDGE));
		y = Math.max(ceiling, Math.min(top, floor));
		placed = true;
	}

	// Placed once it is in the DOM, then kept in step while it is open: any scroll (capture phase, so
	// a scroll in any ancestor counts) or resize moves the control, and the bubble has to follow.
	$effect(() => {
		if (!open) {
			placed = false;
			return;
		}
		/*
		 * And again when the words change, which `staysOnPress` makes necessary: the bubble is
		 * centred from its own measured width, so a label that becomes a shorter word while it is
		 * up ("Copy the filename" to "Copied") would sit at the old width's offset. Read here,
		 * because this effect is the one place that knows where the bubble goes.
		 */
		void label;
		void keys;
		place();
		const follow = () => place();
		window.addEventListener('scroll', follow, true);
		window.addEventListener('resize', follow);
		return () => {
			window.removeEventListener('scroll', follow, true);
			window.removeEventListener('resize', follow);
		};
	});

	/*
	 * Pressing the thing takes the label away.
	 *
	 * A tooltip is dismissed by the pointer leaving, and a control that opens something over itself
	 * (a dialog, the settings panel) takes the pointer out of the picture without crossing the
	 * trigger's edge, so no `pointerleave` fires and the bubble would stay up behind whatever
	 * opened, and after it closes.
	 *
	 * On the capture phase, so it happens before the control's own handler runs and the page
	 * changes underneath.
	 *
	 * `staysOnPress` is the named exception: a control whose answer is the word in this bubble puts
	 * nothing over itself. See the prop.
	 *
	 * The label is shown from a pointer move, not from entering. An element appearing under a
	 * stationary pointer fires enter (a panel closing rebuilds the control it was opened from, born
	 * under a hand that has not moved), so the label would come back with nobody hovering. A move
	 * is the only signal that somebody pointed at it, and moving onto something always produces
	 * one. A latch cannot do this, because the click that dismisses the panel releases it a moment
	 * before the control is rebuilt.
	 */

	// The delay is what stops a tooltip firing every time the pointer crosses a row of buttons on
	// its way somewhere else. Focus gets no delay: arriving by keyboard is deliberate, and waiting
	// on a deliberate action reads as lag.
	let timer: ReturnType<typeof setTimeout> | undefined;

	function show(delay: number) {
		clearTimeout(timer);
		timer = setTimeout(() => (open = true), motion.duration(delay));
	}

	function hide() {
		clearTimeout(timer);
		open = false;
	}

	$effect(() => {
		const holding = held;
		untrack(() => (holding ? show(0) : hide()));
	});

	/** What a press does: take the label away, unless the label is what the press is FOR. */
	function onPress() {
		if (staysOnPress) return;
		hide();
	}

	/** A pending "has focus really left?" check. Its own timer, so cancelling it cannot cancel the
	 *  one that opens the bubble. */
	let leaving: ReturnType<typeof setTimeout> | undefined;

	/*
	 * Focus leaving hides the label, but only once focus has actually left.
	 *
	 * `focusout` fires for a round trip as well as a departure, and something that borrows focus
	 * for an instant and hands it back produces the same event. `$lib/shell/clipboard`'s fallback, the
	 * only way to copy without a secure context, does exactly that through a textarea; taking the
	 * bubble down then would dismiss "Copied" before it was drawn. The fallback gives focus back,
	 * and this waits to see the end of the departure.
	 *
	 * A task rather than a microtask: that sequence is synchronous today, but `setTimeout 0` also
	 * covers a shell that defers a blur, for the cost of one turn of the loop.
	 *
	 * `contains` rather than an identity test on the trigger: the wrapper holds whatever was passed
	 * in, and focus landing anywhere inside it never left this control.
	 */
	function onFocusOut() {
		clearTimeout(leaving);
		leaving = setTimeout(() => {
			if (wrapEl?.contains(document.activeElement)) return;
			hide();
		}, 0);
	}

	/*
	 * Focus shows the label only when the focus came from the keyboard.
	 *
	 * The other half of the same rule as showing on a move. A panel RESTORES FOCUS to whatever
	 * opened it when it closes, which is right, and which means the control this tooltip labels
	 * gets focus back a moment after the panel goes away, with the pointer nowhere near it. Shown
	 * on any focus, the label would appear on its own, seconds after the click, with nobody
	 * hovering anything.
	 *
	 * `:focus-visible` is the browser's own answer to "did they mean to focus this": it matches a
	 * Tab, and it does not match focus that followed a mouse click or was moved by script. A
	 * keyboard user still gets the label instantly, which is the whole reason focus shows it.
	 */
	function onFocus(event: FocusEvent) {
		if (fingerLast) return;
		const target = event.target as HTMLElement | null;
		if (!target?.matches?.(':focus-visible')) return;
		show(0);
	}

	// A pending timer outlives the component otherwise: leave the pointer on a rail item and change
	// route, and it fires against something that is no longer there.
	$effect(() => () => {
		clearTimeout(timer);
		clearTimeout(leaving);
	});

	/*
	 * A key that presses the control is a press, and takes the label away as a click does.
	 *
	 * A plain button turns Enter and Space into a click, which `onclickcapture` already answers. A
	 * chooser does not: it opens its list on the key itself and cancels the click (Sort by on the
	 * screen bar), and it opens on the arrows as well. A bubble left up over the open list answers
	 * Escape before anything under it, so the first Escape would take the bubble and only the
	 * second shut the list. The arrows count only on a control that opens something, so
	 * a slider or a field keeps its label while it is being moved through.
	 */
	function pressedByKey(event: KeyboardEvent): boolean {
		if (event.key === 'Enter' || event.key === ' ') return true;
		if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return false;
		return !!(event.target as HTMLElement | null)?.closest?.('[aria-haspopup]');
	}

	function onKeydown(event: KeyboardEvent) {
		if (pressedByKey(event)) {
			onPress();
			return;
		}
		// Escape closes it without moving focus, so a keyboard user can get it out of the way of
		// whatever it is covering.
		if (event.key === 'Escape' && open) {
			event.stopPropagation();
			hide();
		}
	}
</script>

<!--
	The wrapper is a position anchor and an event catcher, not a control: the thing that can be
	operated is whatever was passed in, and it keeps its own role, label and focus behaviour. The
	handlers sit out here because hover and focus have to be caught for the whole group, including
	the moment focus arrives on the child. There is no role to give this, because it is not a
	widget: adding one would announce a control that is not there.
-->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<span
	class="wrap"
	class:stretch
	class:shrinks
	bind:this={wrapEl}
	onpointermove={(event: PointerEvent) => {
		if (event.pointerType !== 'touch') show(180);
	}}
	onpointerleave={hide}
	onfocusin={onFocus}
	onfocusout={onFocusOut}
	onkeydown={onKeydown}
	onpointerdowncapture={onPress}
	onclickcapture={onPress}
>
	<span class="target" aria-describedby={open ? id : undefined}>
		{@render children()}
	</span>

	{#if open}
		<span
			{id}
			use:portal
			bind:this={bubbleEl}
			role="tooltip"
			class="bubble"
			class:placed
			style:left="{x}px"
			style:top="{y}px"
		>
			{#if lead}<span class="lead" data-tone={tone}>{lead}</span>{' '}{/if}{label}{#if keys}<span
					class="keys">{keys}</span
				>{/if}{#if detail}<span class="detail" class:wide class:alone={!label && !lead}
					>{@render detail()}</span
				>{/if}
		</span>
	{/if}
</span>

<style>
	/*
	 * The second line, when there is one. A block under the words rather than beside them: what goes
	 * in here is a set of small things (chips) and a row of them on the same line as a sentence
	 * makes a bubble as wide as the screen.
	 */
	/* A picture cannot wrap: see `wide`. */
	.detail.wide {
		max-inline-size: none;
	}

	.detail {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-1);
		margin-block-start: var(--space-2);
		max-inline-size: 32ch;
	}

	/* Nothing above it to be spaced from. The gap is what separates the second line from the first,
	   and kept when there is no first line it is a band of empty bubble over the chips. */
	.detail.alone {
		margin-block-start: 0;
	}

	/* The keys, quieter than the words and set in the data face so `Ctrl + F` does not read as
	   prose. Spaced from the label rather than punctuated: a tooltip is one line and a separator
	   would be a third thing on it. */
	.keys {
		margin-inline-start: var(--space-2);
		color: var(--sift-ink-3);
		font: var(--text-data);
	}

	/*
	 * A wrapper must never be the reason its child cannot shrink, where the child can shrink at
	 * all. That is `shrinks`; the prop's note carries the argument, and this is where the
	 * declarations land. Its callers (the file name in the popout's action row, the download
	 * address in the File Info record) ask for it by name. It is not on every tooltip: around a
	 * fixed-size control a zero floor ellipsizes nothing and lets the control paint out of its
	 * wrapper.
	 *
	 * Only the minimum: a `max-inline-size: 100%` beside it binds nothing, and a rule that changes
	 * no pixel is one the next reader must reason about for nothing.
	 */
	.wrap {
		position: relative;
		display: inline-flex;
	}

	.target {
		display: inline-flex;
	}

	.wrap.shrinks,
	.wrap.shrinks .target {
		min-inline-size: 0;
	}

	.wrap.stretch,
	.wrap.stretch .target {
		width: 100%;
	}

	/* Rendered into <body> and pinned to the viewport at the coordinates measured in script, so it
	   is never clipped by a scrolling ancestor or covered by a neighbour. Hidden until placed, or it
	   would flash once at the top-left before the first measurement lands. */
	.lead[data-tone='share'] {
		color: var(--sift-ok);
	}

	.lead[data-tone='restrict'] {
		color: var(--sift-bad-text);
	}

	.lead[data-tone='both'] {
		color: var(--sift-warn);
	}

	/* Hidden is not an alarm: it is the ordinary state of everything behind the PIN, so it says
	   its piece in the plain ink rather than borrowing one of the three sharing colours, which mean
	   something else entirely. What marks it out is the weight, the same as the others. */
	.lead[data-tone='hidden'] {
		color: var(--sift-ink);
	}

	.bubble {
		position: fixed;
		z-index: var(--z-tooltip);
		width: max-content;
		/*
		 * Wide enough for the things it is actually asked to hold, and it wraps rather than being
		 * cut.
		 *
		 * A box sized for a control's name (two or three words) is too narrow: a tooltip is how a
		 * filename or an address that did not fit on its row is READ. Those are sixty or a hundred
		 * characters, and the box meant to explain them must not cut them off in turn.
		 *
		 * `min()` rather than a flat number: on a phone the window is narrower than the box, and a
		 * tooltip wider than the screen is positioned off the edge of it.
		 */
		max-width: min(52ch, calc(100vw - var(--space-8)));
		overflow-wrap: anywhere;
		padding: var(--space-1) var(--space-2);
		border-radius: var(--radius-sm);
		background: var(--sift-surface-2);
		border: 1px solid var(--sift-line);
		box-shadow: var(--elev-2);
		color: var(--sift-ink);
		font: var(--text-label);
		/* It labels the thing under the pointer; it must never be the thing under the pointer. */
		pointer-events: none;
		visibility: hidden;
	}

	/* It opens as every small surface does, with the stylesheet's `rise`. It leaves immediately: it is
	   usually taken away by a press on the control under it, and whatever that press opens must not
	   have a fading label over it. */
	.bubble.placed {
		visibility: visible;
		animation: rise var(--dur-fast) var(--ease);
	}

	/* Someone who has asked for less movement gets the tooltip without the fade, not without the
	   tooltip. */
	:global(:root[data-motion='reduce']) .bubble.placed {
		animation: none;
	}
</style>
