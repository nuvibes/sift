<script lang="ts">
	/* WHY NO HOVER: the one hover rule dresses the shared button, whose motion is its own. */
	/*
	 * NOT ON THE GALLERY: it is `display: none` unless the root carries `data-window="overlaid"`,
	 * which only the packaged desktop shell ever stamps.
	 */

	/* The window's own title bar, on every screen so the window can always be moved. */
	import Logo from '$lib/components/Logo.svelte';
	import Button from '$lib/components/common/Button.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { bridge } from '$lib/bridge';
	import { openSettings } from '$lib/settings-ui/settings-view';
	import { session } from '$lib/shell/session.svelte';
	import { updates } from '$lib/shell/updates.svelte';
	import { rail } from './rail-state.svelte';

	/* Back and forward for the desktop app, which has no browser to carry them. */
	let { arrows = false }: { arrows?: boolean } = $props();

	const inTheApp = bridge.canDressTitleBar();

	/* The standing reminder, kept when the banner is dismissed; the banner loads the state. */
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
		/* Above every veil, so the window can still be moved while a sheet is open. */
		z-index: var(--z-window-chrome);
		block-size: var(--window-chrome);
		/* What the system's minimise, maximise and close take at the right end. */
		--captions: calc(100vw - env(titlebar-area-width, 100vw));
		/* No rule along its foot: it shares the rail's ground; the caption buttons have none. */
		background: var(--sift-surface-1);
		-webkit-app-region: drag;
		app-region: drag;
	}

	/* Centred on the window: the reserved corner is absolute, so it cannot push the lockup off. */
	.lockup {
		display: flex;
	}

	:global(:root[data-window='overlaid']) .window-bar :global(.logo) {
		color: var(--sift-ink-2);
	}

	/*
	 * The back arrow's ink lines up on the mark's left edge: the button's 7px padding and the
	 * glyph's 4px side bearing make 11px. Out of the drag region, or presses would start a move.
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

	/* With icons only the pair is centred on the 64px rail, each press half its width. */
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
