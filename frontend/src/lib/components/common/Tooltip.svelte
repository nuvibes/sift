<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Tooltip',
		category: 'primitive',
		role: 'the name of a control, shown on hover and on focus, never under a stationary pointer',
		basis: 'own',
		states: ['closed', 'open', 'with a shortcut']
	} satisfies DesignEntry;

	/* Whether the last press was a finger: a finger has no hover to take a label away, so its
	   moves and the focus following it never show one. A key clears it. */
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
	 * WHY NOT BITS-UI: it opens on `pointerenter`, and this one deliberately does not. An element
	 * appearing under a still pointer fires enter, so a rebuilt control would be labelled with
	 * nobody hovering. A short label on hover and keyboard focus; it labels, never explains.
	 * */
	import { keysFor } from '$lib/shell/shortcuts';
	import type { Snippet } from 'svelte';
	import { motion } from '$lib/shell/motion.svelte';
	import { untrack } from 'svelte';

	interface Props {
		/** The words, a few. Empty only when `detail` is the whole bubble. */
		label: string;
		/** A first word in its own colour ("Shared"); a prop, never markup in the label. */
		lead?: string;
		/** Let the detail be as wide as it needs, for a picture that cannot wrap. */
		wide?: boolean;
		/** Which colour the lead wears. */
		tone?: 'share' | 'restrict' | 'both' | 'hidden';
		/** Which side of the control to sit on. */
		placement?: 'top' | 'bottom' | 'right';
		/**
		 * A second line of markup (chips, marks, a swatch); never anything that can be operated.
		 */
		detail?: Snippet;
		/** Fill the wrapper's width, for a control that stretches to its column. */
		stretch?: boolean;
		/**
		 * Let the control be squeezed below its content, for one that ellipsizes; never a glyph.
		 */
		shrinks?: boolean;
		/** The id of a shortcut this control answers to; its keys are looked up and appended. */
		shortcut?: string;
		/** Keep the label up on a press, for a control whose answer is the label ("Copied"). */
		staysOnPress?: boolean;
		/** Shown while true, whatever the pointer and focus do: a list row the keyboard highlighted. */
		held?: boolean;
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

	// Rendered into body so no scroller clips it, and placed from the control's rectangle.
	let wrapEl: HTMLElement;
	let bubbleEl = $state<HTMLElement | undefined>();
	let x = $state(0);
	let y = $state(0);
	let placed = $state(false);

	/* Into body, or into the screen filling the window, asked at mount. */
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
		// The window's title strip sits above this layer, so the usable top is its foot.
		const chrome =
			parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--window-chrome')) ||
			0;
		const ceiling = chrome + EDGE;
		const floor = window.innerHeight - b.height - EDGE;
		let side = placement;
		// A side with no room flips before anything is clamped.
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

	// Kept in step with any scroll (capture phase) or resize while open.
	$effect(() => {
		if (!open) {
			placed = false;
			return;
		}
		/* And when the words change, since the bubble is centred from its own width. */
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

	/* A press takes the label away (capture phase), since what it opens fires no pointerleave.
	   It shows on a pointer move, never on enter: a rebuilt control is born under a still hand. */

	// The delay keeps a passing pointer quiet; focus gets none, being deliberate.
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

	/** A pending check that focus really left, on its own timer. */
	let leaving: ReturnType<typeof setTimeout> | undefined;

	/* Hide once focus has really left: the clipboard fallback borrows focus and hands it back. */
	function onFocusOut() {
		clearTimeout(leaving);
		leaving = setTimeout(() => {
			if (wrapEl?.contains(document.activeElement)) return;
			hide();
		}, 0);
	}

	/*
	 * Focus shows it only from the keyboard (:focus-visible), not focus a closing panel restores.
	 */
	function onFocus(event: FocusEvent) {
		if (fingerLast) return;
		const target = event.target as HTMLElement | null;
		if (!target?.matches?.(':focus-visible')) return;
		show(0);
	}

	// Cleared on unmount, or it fires against a component that is gone.
	$effect(() => () => {
		clearTimeout(timer);
		clearTimeout(leaving);
	});

	/* A key that presses or opens the control takes the label away, as a click does. */
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
		// Escape closes it without moving focus.
		if (event.key === 'Escape' && open) {
			event.stopPropagation();
			hide();
		}
	}
</script>

<!-- The wrapper anchors the bubble and catches hover and focus; it is not a widget. -->
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

	/* No first line, so no gap above. */
	.detail.alone {
		margin-block-start: 0;
	}

	/* The keys, quieter, in the data face. */
	.keys {
		margin-inline-start: var(--space-2);
		color: var(--sift-ink-3);
		font: var(--text-data);
	}

	/* See `shrinks`: only the minimum. */
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

	/* Hidden until placed, or it flashes at the top-left. */
	.lead[data-tone='share'] {
		color: var(--sift-ok);
	}

	.lead[data-tone='restrict'] {
		color: var(--sift-bad-text);
	}

	.lead[data-tone='both'] {
		color: var(--sift-warn);
	}

	/* Hidden is ordinary, not an alarm: plain ink, the same weight. */
	.lead[data-tone='hidden'] {
		color: var(--sift-ink);
	}

	.bubble {
		position: fixed;
		z-index: var(--z-tooltip);
		width: max-content;
		/* Wide enough to read a long filename, wrapping; min() keeps it inside a phone. */
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

	/* Rises in, leaves immediately: a press usually removes it. */
	.bubble.placed {
		visibility: visible;
		animation: rise var(--dur-fast) var(--ease);
	}

	/* Reduced motion drops the fade, not the tooltip. */
	:global(:root[data-motion='reduce']) .bubble.placed {
		animation: none;
	}
</style>
