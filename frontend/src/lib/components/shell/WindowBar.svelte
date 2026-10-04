<script lang="ts">
	/*
	 * NOT ON THE GALLERY: it is `display: none` unless the root carries `data-window="overlaid"`,
	 * which only the packaged desktop shell ever stamps, so a gallery entry would be an empty box,
	 * and drawing it any other way would be a second implementation of it. What it is MADE of, the
	 * brand lockup, is on the gallery already.
	 */

	/*
	 * The window's own title bar: Sift's, drawn by the page, on Sift's own ground.
	 *
	 * ## Why a strip of its own
	 *
	 * The window is created with the operating system's title bar HIDDEN and its minimise, maximise
	 * and close overlaid on the page. Sitting on the application's own top bar (the row with the
	 * search box in it), those buttons would make that bar run flush to the top and to the right of
	 * the window for them to have the right colour behind them, and the content panel would lose
	 * the eight-pixel inset that says "this is a panel sitting on the rail" along two of its edges.
	 *
	 * This strip is what the buttons sit on, so the panel underneath is inset on every edge, in both
	 * shells, and the window has a title bar that says what application it is.
	 *
	 * ## It is Sift's chrome and not the operating system's
	 *
	 * The strip is drawn from the same tokens as everything else, so it follows the theme somebody
	 * picked. That is the whole difference between this and the grey strip the window would have had:
	 * a dark application under a light-grey caption bar reads as an application dropped into
	 * somebody else's window.
	 *
	 * ## Why it is drawn on EVERY screen, including the ones outside the application
	 *
	 * Because a title bar is a fact about the window, not about what is in it. The sign-in, setup,
	 * connect and not-answering screens have no top bar, and without a strip the window could not
	 * be moved at all on the four screens somebody meets first. One strip, always there, is one
	 * answer to that instead of two mechanisms that have to agree about which screens are which.
	 */
	import Logo from '$lib/components/Logo.svelte';
	import Button from '$lib/components/common/Button.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { bridge } from '$lib/bridge';
	import { rail } from './rail-state.svelte';

	/*
	 * BACK AND FORWARD, at the strip's left end, above the rail's mark: the application's own
	 * arrows, since the window has no browser around it to carry them.
	 *
	 * `arrows` is the layout saying the application's frame is on screen. The sign-in, setup,
	 * connect and not-answering screens are outside it and each has its own way back, if any.
	 * Drawn only when this window is the desktop application (the same answer that draws this
	 * strip at all), so a browser, on a desktop or a phone, keeps its own arrows and nothing here
	 * listens to them or draws a second pair.
	 *
	 * They go where the browser's would: `history.back()` and `forward()`, which the router follows
	 * as it follows the browser's own. Each is dimmed where there is nowhere to go, read from the
	 * window's history (`navigation`, which Chromium, and so the application, has), and read again
	 * every time the entry moves, whoever moved it.
	 */
	let { arrows = false }: { arrows?: boolean } = $props();

	const inTheApp = bridge.canDressTitleBar();

	/** The two facts read off the window's history, and the one event that moves them. */
	interface WindowHistory extends EventTarget {
		readonly canGoBack: boolean;
		readonly canGoForward: boolean;
	}

	let canGoBack = $state(false);
	let canGoForward = $state(false);

	$effect(() => {
		if (!arrows || !inTheApp) return;
		const entries = (window as unknown as { navigation?: WindowHistory }).navigation;
		if (!entries) {
			// Nothing to read the ends by: both stay pressable, and a press at an end does nothing.
			canGoBack = canGoForward = true;
			return;
		}
		const read = () => {
			canGoBack = entries.canGoBack;
			canGoForward = entries.canGoForward;
		};
		read();
		entries.addEventListener('currententrychange', read);
		return () => entries.removeEventListener('currententrychange', read);
	});
</script>

<!--
	The lockup is `aria-hidden`, and the arrows are the only thing here anybody operates.

	Dragging a window is a pointer gesture with no keyboard equivalent to announce, and the operating
	system's own keyboard move command is unaffected either way. The application is already named by
	the page's title, so a second announcement of "Sift" at the top of every screen would be read out
	before the screen itself on every navigation.
-->
<div class="window-bar">
	{#if arrows && inTheApp}
		<!-- A real control group: the strip is a drag region, so this corner is held out of it. -->
		<div class="arrows" class:tight={rail.iconsOnly} role="group" aria-label="Back and forward">
			<Tooltip label="Back" placement="bottom">
				<Button
					tone="ghost"
					size="small"
					icon="arrow_back"
					aria-label="Back"
					disabled={!canGoBack}
					onclick={() => history.back()}
				/>
			</Tooltip>
			<Tooltip label="Forward" placement="bottom">
				<Button
					tone="ghost"
					size="small"
					icon="arrow_forward"
					aria-label="Forward"
					disabled={!canGoForward}
					onclick={() => history.forward()}
				/>
			</Tooltip>
		</div>
	{/if}
	<span class="lockup" aria-hidden="true"><Logo variant="lockup" height={16} /></span>

	<!--
		THE BUTTONS' OWN CORNER, HELD OUT OF THE DRAG REGION.

		The difference between the window's width and `titlebar-area-width` is exactly what the
		minimise, maximise and close take. Marking it `no-drag` keeps this strip from claiming
		presses that belong to them. The fallback of `100vw` makes that width zero, which is right
		for a browser, where there are no such buttons and this strip is not drawn at all.
	-->
	<span class="controls"></span>
</div>

<style>
	.window-bar {
		display: none;
	}

	:global(:root[data-window='overlaid']) .window-bar {
		display: flex;
		align-items: center;
		justify-content: center;
		position: fixed;
		inset-block-start: 0;
		inset-inline: 0;
		/* Above everything, including a dialog's veil and the drop overlay. A window somebody cannot
		   move while a sheet is open is a window that is stuck, and the sheet is exactly when
		   somebody wants to put it somewhere else to read what is behind it. */
		z-index: var(--z-window-chrome);
		block-size: var(--window-chrome);
		/* No rule along its foot. It is the same ground as the rail below it, and the caption buttons
		   the operating system draws beside it have none, so a line here would run across the top of
		   the rail and stop short of them. */
		background: var(--sift-surface-1);
		-webkit-app-region: drag;
		app-region: drag;
	}

	/*
	 * CENTRED ON THE WINDOW, not on what is left over beside the buttons.
	 *
	 * The lockup is the only thing in a flex row that is centred, so the reserved corner below is
	 * absolutely positioned rather than being a sibling that would push it off-centre by half the
	 * caption width: about 69px, which is plainly visible on a mark that small.
	 */
	.lockup {
		display: flex;
	}

	:global(:root[data-window='overlaid']) .window-bar :global(.logo) {
		color: var(--sift-ink-2);
	}

	/*
	 * THE BACK ARROW'S OWN LEFT EDGE ON THE MARK'S LEFT EDGE, the one line the eye draws down the
	 * corner. What lines up is the drawn arrow, not the button round it: the button's padding
	 * ((32 - 18) / 2 = 7px) and the arrow's side bearing (the first 4px of its 18px box are
	 * empty) stand between the group's edge and the ink, so the group starts 11px before the
	 * mark.
	 *
	 * With the labels on the mark is the lockup, inset by the rail's body (`--space-3`) and the
	 * brand's own padding (`--space-2`). With icons only the rail is too narrow for the pair to
	 * start on the mark, and the pair is centred on the rail instead (below). `tight` is the rail's
	 * own answer to which, read from its state, so the two move together whichever reason took the
	 * labels away.
	 *
	 * Lower than the strip's centre by 15 percent of where the arrow was drawn: centred, its ink
	 * starts 13px down ((36 - 18) / 2 and the glyph's own 4px), so 2px more. Out of the drag
	 * region, or the strip would take the presses as the start of a move.
	 */
	:global(:root[data-window='overlaid']) .arrows {
		--arrow-lead: 11px;
		position: absolute;
		inset-block: 0;
		inset-inline-start: calc(var(--space-3) + var(--space-2) - var(--arrow-lead));
		translate: 0 2px;
		display: flex;
		align-items: center;
		gap: var(--space-1);
		-webkit-app-region: no-drag;
		app-region: no-drag;
	}

	/*
	 * WITH ICONS ONLY, BOTH ARROWS INSIDE THE RAIL'S WIDTH. Two small presses and the gap between
	 * them are 68px, wider than the 64px rail, so lined up on the mark the Forward arrow would
	 * hang past the rail over the corner of the page. So the pair is centred on the rail, as the
	 * mark under it is, and each press is half the rail less a small gap at the rail's edge (28px
	 * wide, at the small press's own height), so the two meet in the middle with the glyphs at
	 * their own size.
	 */
	:global(:root[data-window='overlaid']) .arrows.tight {
		inset-inline-start: 0;
		inline-size: var(--rail-width-collapsed);
		justify-content: center;
		gap: 0;
	}

	:global(:root[data-window='overlaid']) .arrows.tight :global(.btn) {
		--arrow-tight: calc(var(--rail-width-collapsed) / 2 - var(--space-1));
		inline-size: var(--arrow-tight);
	}

	:global(:root[data-window='overlaid']) .controls {
		position: absolute;
		inset-block: 0;
		inset-inline-end: 0;
		inline-size: calc(100vw - env(titlebar-area-width, 100vw));
		-webkit-app-region: no-drag;
		app-region: no-drag;
	}
</style>
