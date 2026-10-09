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
	by `pointer-events: none` on `<body>`, which anything saying `auto` escapes. */
	/* A press outside an open surface closes it and nothing else, drawn in the surface's own portal.
	 * It outlives the surface until the press's click (a phone's tap comes a turn later), a cancel
	 * or a hold; `CLICK_LATEST` bounds a click that never comes. */
	interface Props {
		/** Whether the surface above it is open, or still on its way out. */
		up: boolean;
		/** One element left pressable through a hole, for a row of hover triggers. */
		spare?: HTMLElement | null;
	}

	let { up, spare = null }: Props = $props();

	/** A press that began here and has not finished yet. */
	let held = $state(false);

	/** How long after the release to wait for a click, past the slowest tap. */
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
		/* The row can move without the window changing, so its size and its holders are watched. */
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

	/* The window with the spared box cut out by even-odd filling. */
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
	<!-- Presentation only; the surface above is what keyboards and screen readers use. -->
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
	/* Under the surface, `auto` whatever the body says; nothing is drawn. */
	.page-shield {
		position: fixed;
		inset: 0;
		z-index: var(--z-menu);
		pointer-events: auto;
		background: transparent;
	}
</style>
