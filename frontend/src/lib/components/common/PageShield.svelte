<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'PageShield',
		category: 'surface',
		role: 'the clear sheet under an open menu that takes a press meant to close it, so the page never hears that press',
		basis: 'site:<div>',
		states: ['up']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* NOT ON THE GALLERY: it is clear and fills the window. It is drawn by every menu, chooser and
	   popover the gallery opens, under the open surface. */
	/* WHY NOT BITS-UI: the library has no such layer. Its modal menus take the pointer from the page
	   by setting `pointer-events: none` on `<body>`, and anything below that says `auto` escapes it:
	   its own right-click trigger does (every tile is one), and so do the player's edge controls. */
	/*
	 * A press outside an open surface closes that surface, and nothing else.
	 *
	 * Drawn by the surface itself, in its own portal and just before it, so it goes wherever the
	 * surface goes (into a fullscreened element too) and sits directly under it on the same layer.
	 * Whatever the page underneath says about its own pointer, the press lands here: the library
	 * sees it as a press outside and closes, and the tile, the row or the player under it never
	 * receives it.
	 *
	 * It outlives the surface by one gesture. The surface closes on the press, and the release and
	 * the click that follow still belong to that press; if the shield went with the surface they
	 * would land on whatever is underneath, and a click there opens it.
	 *
	 * The gesture ends at its CLICK, not at the release. A mouse's click comes in the same turn as
	 * its release, but a finger's does not: a phone's browser raises it as a separate tap once the
	 * finger is up, a turn or more later, so a shield that went at the release would be gone when
	 * the tap's click was aimed, and the tap that shut a sheet would press what lay under it. A press
	 * the browser takes over (a scroll: `pointercancel`) or turns into a hold (`contextmenu`) raises
	 * no click, and ends it immediately. `CLICK_LATEST` covers a click that never comes for any
	 * other reason, so the shield can never be left standing over a page with nothing open.
	 */
	interface Props {
		/** Whether the surface above it is open, or still on its way out. */
		up: boolean;
		/**
		 * One element the sheet leaves a hole over, so it stays pressable and pointable.
		 *
		 * For a surface opened by pointing at a row of triggers: moving along the row has to reach
		 * the next trigger, and a sheet over it would read as the pointer leaving.
		 */
		spare?: HTMLElement | null;
	}

	let { up, spare = null }: Props = $props();

	/** A press that began here and has not finished yet. */
	let held = $state(false);

	/** How long after the release the shield waits for a click that has not come, in ms: longer
	 *  than the slowest tap a phone's browser still turns into a click. */
	const CLICK_LATEST = 500;

	/** Ends the gesture in progress early, when a new one starts or the shield goes. */
	let endGesture: (() => void) | null = null;

	function press(): void {
		endGesture?.();
		held = true;
		let late: ReturnType<typeof setTimeout> | null = null;
		const listening: [string, () => void][] = [];
		const listen = (type: string, handler: () => void) => {
			window.addEventListener(type, handler, true);
			listening.push([type, handler]);
		};
		const done = () => {
			for (const [type, handler] of listening) window.removeEventListener(type, handler, true);
			listening.length = 0;
			if (late) clearTimeout(late);
			late = null;
			endGesture = null;
			// After the click has been handed out: it was aimed at the shield, so it lands there.
			setTimeout(() => (held = false), 0);
		};
		const released = () => {
			late = setTimeout(done, CLICK_LATEST);
		};
		listen('pointerup', released);
		listen('pointercancel', done);
		listen('contextmenu', done);
		listen('click', done);
		endGesture = done;
	}

	$effect(() => () => endGesture?.());

	/** Where the spared element is, in the window's coordinates, while the sheet is up. */
	let hole = $state<DOMRect | null>(null);

	$effect(() => {
		if (!(up || held) || !spare) {
			hole = null;
			return;
		}
		const target = spare;
		const measure = () => (hole = target.getBoundingClientRect());
		measure();
		/* The row can move while the sheet is up without the window changing: the sidebar folding
		   away resizes the column it stands in, and a scroll carries it. A change of size in the
		   row or in anything holding it re-measures, and the measure reads its place too. */
		const watch = new ResizeObserver(measure);
		for (let box: Element | null = target; box; box = box.parentElement) watch.observe(box);
		window.addEventListener('resize', measure);
		window.addEventListener('scroll', measure, true);
		return () => {
			watch.disconnect();
			window.removeEventListener('resize', measure);
			window.removeEventListener('scroll', measure, true);
		};
	});

	/*
	 * The window with the spared box cut out of it. Even-odd filling turns the second ring into a
	 * hole, and a clipped-out area takes no pointer, so presses and hovers there reach the element.
	 */
	const clip = $derived.by(() => {
		if (!hole) return undefined;
		const { left: l, top: t, right: r, bottom: b } = hole;
		return (
			`polygon(evenodd, 0 0, 100% 0, 100% 100%, 0 100%, 0 0, ` +
			`${l}px ${t}px, ${r}px ${t}px, ${r}px ${b}px, ${l}px ${b}px, ${l}px ${t}px)`
		);
	});
</script>

{#if up || held}
	<!-- Presentation only: it is announced to nobody and takes no focus. The surface above it is what
	     a keyboard and a screen reader use, and Escape closes it as before. -->
	<div
		class="page-shield"
		role="presentation"
		aria-hidden="true"
		style:clip-path={clip}
		onpointerdown={press}
		oncontextmenu={(event) => event.preventDefault()}
	></div>
{/if}

<style>
	/* The menu layer, under the surface that draws it (it comes first in the portal), and `auto`
	   whatever the body says, which is the whole point of it. Nothing is drawn. */
	.page-shield {
		position: fixed;
		inset: 0;
		z-index: var(--z-menu);
		pointer-events: auto;
		background: transparent;
	}
</style>
