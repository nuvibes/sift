<script lang="ts">
	/* WHY NO HOVER: the one hover rule dresses the shared button, whose motion is its own. */
	/*
	 * NOT ON THE GALLERY: it is `display: none` unless the root carries `data-window="overlaid"`,
	 * which only the packaged desktop shell ever stamps, so a gallery entry would be an empty box;
	 * what it is MADE of, the brand lockup, is on the gallery already.
	 */

	/*
	 * The window's own title bar, drawn by the page from Sift's tokens: the system's minimise,
	 * maximise and close sit on it, and it is on every screen so the window can always be moved.
	 */
	import Logo from '$lib/components/Logo.svelte';
	import Button from '$lib/components/common/Button.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { bridge } from '$lib/bridge';
	import { openSettings } from '$lib/settings-ui/settings-view';
	import { session } from '$lib/shell/session.svelte';
	import { updates } from '$lib/shell/updates.svelte';
	import { rail } from './rail-state.svelte';

	/*
	 * BACK AND FORWARD for the desktop application, which has no browser around it to carry them;
	 * `arrows` is the layout saying the application's frame is on screen.
	 */
	let { arrows = false }: { arrows?: boolean } = $props();

	const inTheApp = bridge.canDressTitleBar();

	/* Dismissing the banner does not hide this: it is the standing reminder. The banner loads the state. */
	const waiting = $derived(arrows && inTheApp && session.isAdmin && updates.waiting);

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

<!-- The lockup is `aria-hidden`: the page's title already names the application. -->
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

	<!-- The system buttons' own corner, held out of the drag region (zero wide in a browser). -->
	{#if waiting}
		<span class="update">
			<Tooltip label="Sift {updates.state?.latest_version} is available" placement="bottom">
				<Button
					tone="ghost"
					shape="circle"
					size="small"
					icon="upgrade"
					aria-label="Open Updates and Info: Sift {updates.state?.latest_version} is available"
					onclick={() => openSettings('updates', 'updates.version')}
				/>
			</Tooltip>
		</span>
	{/if}
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
		/* What the system's minimise, maximise and close take at the right end. */
		--captions: calc(100vw - env(titlebar-area-width, 100vw));
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
		inline-size: var(--captions);
		-webkit-app-region: no-drag;
		app-region: no-drag;
	}

	/* Immediately left of minimise, and out of the drag region so the press is not a move. */
	:global(:root[data-window='overlaid']) .update {
		position: absolute;
		inset-block: 0;
		inset-inline-end: var(--captions);
		display: flex;
		align-items: center;
		-webkit-app-region: no-drag;
		app-region: no-drag;
	}

	:global(:root[data-window='overlaid']) .update :global(.btn.ghost),
	:global(:root[data-window='overlaid']) .update :global(.btn.ghost:hover:not(:disabled)) {
		color: var(--sift-accent-text);
	}
</style>
