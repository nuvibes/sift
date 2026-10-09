<script lang="ts">
	/* NOT ON THE GALLERY: the settings panel is the whole window when it is open, and the layout renders the
	   one there is. A second copy on the gallery would cover the page it is meant to be reviewed on,
	   and what it holds (the settings shell and its panes) is a screen rather than a part. */

	/* Settings over whatever you were doing: a card with the app visible around it, not a page. */
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { onMount } from 'svelte';
	import { Button } from '$lib/components/common';
	import { keystrokeIsUnanswered } from '$lib/shell/layers';
	import { arrive, fromEdge } from '$lib/shell/motion.svelte';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import Veil from '$lib/components/common/Veil.svelte';
	import SettingsPane from '$lib/settings-ui/SettingsPane.svelte';
	import SettingsShell from '$lib/settings-ui/SettingsShell.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { firstSectionFor, isKnownSection } from '$lib/settings-ui/sections';
	import { showSettingsSection } from '$lib/settings-ui/settings-view';

	interface Props {
		section: string;
		onclose: () => void;
	}

	let { section, onclose }: Props = $props();

	const current = $derived(isKnownSection(section) ? section : firstSectionFor(session.isAdmin));

	let panel = $state<HTMLElement | null>(null);

	onMount(() => {
		const returnTo = document.activeElement as HTMLElement | null;
		panel?.focus();
		return () => returnTo?.focus?.();
	});

	function trap(event: KeyboardEvent) {
		if (event.key !== 'Tab' || !panel) return;
		const focusable = panel.querySelectorAll<HTMLElement>(
			'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])'
		);
		if (focusable.length === 0) {
			event.preventDefault();
			panel.focus();
			return;
		}
		const first = focusable[0];
		const last = focusable[focusable.length - 1];
		const active = document.activeElement;

		if (event.shiftKey && (active === first || active === panel)) {
			event.preventDefault();
			last.focus();
		} else if (!event.shiftKey && active === last) {
			event.preventDefault();
			first.focus();
		}
	}

	function onKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') {
			// A sheet over Settings answers Escape itself (`keystrokeIsUnanswered`).
			if (!keystrokeIsUnanswered(event)) return;
			event.preventDefault();
			event.stopPropagation();
			onclose();
			return;
		}
		trap(event);
	}

	/* A pushed page from the trailing edge on a phone; a rising card elsewhere. */
	function comes(node: Element) {
		return phoneWidth.yes ? fromEdge(node, { edge: 'end' }) : arrive(node, { pace: 'base', y: 12 });
	}
</script>

<svelte:window onkeydown={onKeydown} />

<!-- A button, so clicking beside the panel closes it, as the asset veil does. -->
<Veil label="Close settings" layer="settings" {onclose} />

<div
	class="panel"
	role="dialog"
	aria-modal="true"
	aria-label="Settings"
	tabindex="-1"
	bind:this={panel}
	transition:comes
>
	<!-- The corner is THIS box: on the button it would be placed against the tooltip's wrapper. -->
	<div class="close-slot">
		<Tooltip label="Close settings" shortcut="app.dismiss">
			<Button
				tone="ghost"
				class="close"
				icon="close"
				aria-label="Close settings"
				onclick={onclose}
			/>
		</Tooltip>
	</div>

	<div class="inner">
		<SettingsShell {current} showingSection={true} onselect={showSettingsSection}>
			<SettingsPane section={current} />
		</SettingsShell>
	</div>
</div>

<style>
	/* A sheer veil and a margin: the app behind is what makes this a dialog. */
	.panel {
		position: fixed;
		/* Two declarations, since the top clears the desktop title strip. */
		inset-block-start: calc(var(--window-chrome) + clamp(var(--space-4), 5vh, 56px));
		inset-block-end: clamp(var(--space-4), 5vh, 56px);
		inset-inline: var(--space-4);
		margin-inline: auto;
		max-inline-size: 1180px;
		z-index: var(--z-settings-sheet);
		display: flex;
		justify-content: center;
		overflow: hidden;
		border-radius: var(--radius-xl);
		border: 1px solid var(--sift-line);
		background: var(--sift-surface-1);
		--data-row-base: var(--sift-surface-1);
		box-shadow: var(--elev-3);
	}

	/* No ring on the panel itself: any key would light one around it. */
	.panel:focus-visible {
		box-shadow: var(--elev-3);
	}

	/* Does NOT scroll: the list and the section scroll on their own. */
	.inner {
		flex: 1;
		min-inline-size: 0;
		min-block-size: 0;
		overflow: hidden;
	}

	/* Inside the card's corner, larger than standard, the shared button's square box. */
	.close-slot {
		position: absolute;
		--radius-md: calc(var(--radius-xl) - var(--space-4));
		inset-block-start: var(--space-4);
		inset-inline-end: var(--space-4);
		z-index: var(--z-settings-close);
	}

	.close-slot :global(.close) {
		inline-size: 40px;
		block-size: 40px;
		border: 1px solid var(--sift-line);
		background: var(--sift-surface-2);
		color: var(--sift-ink-2);
		transition:
			color var(--dur-instant) var(--ease),
			border-color var(--dur-instant) var(--ease);
	}

	.close-slot :global(.close:hover) {
		color: var(--sift-ink);
		border-color: var(--sift-ink-3);
	}

	/* On a phone the card is the screen. */
	@media (max-width: 767px) {
		.panel {
			inset: 0;
			border: 0;
			border-radius: 0;
		}

		/* Level with the section's way back (`SettingsShell`). */
		.close-slot {
			inset-block-start: calc(var(--space-4) + var(--safe-top));
			inset-inline-end: calc(var(--space-4) + var(--safe-right));
		}

		.close-slot :global(.close) {
			inline-size: var(--touch-target);
			block-size: var(--touch-target);
		}
	}
</style>
