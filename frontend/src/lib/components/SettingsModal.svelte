<script lang="ts">
	/* NOT ON THE GALLERY: the settings panel is the whole window when it is open, and the layout renders the
	   one there is. A second copy on the gallery would cover the page it is meant to be reviewed on,
	   and what it holds (the settings shell and its panes) is a screen rather than a part. */

	/*
	 * Settings, over whatever you were doing.
	 *
	 * Changing a setting is almost never what somebody came to do; it happens in the middle of
	 * something else, because a preview was the wrong size or a download went to the wrong folder.
	 * A page for it would take away the screen that prompted it.
	 *
	 * So it is a card with a margin, the app visible around and behind it. The margin is what makes
	 * it read as something on the app, with a way back, rather than a page that replaced it; taking
	 * the whole window feels like navigating away.
	 *
	 * The sections are the same the page renders, from the same list and component; only this frame
	 * differs.
	 */
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
		/** Which section to show. An unknown one falls back rather than drawing an empty panel. */
		section: string;
		onclose: () => void;
	}

	let { section, onclose }: Props = $props();

	const current = $derived(isKnownSection(section) ? section : firstSectionFor(session.isAdmin));

	let panel = $state<HTMLElement | null>(null);

	onMount(() => {
		// Focus moves in, or a keyboard user is left operating the screen behind a panel covering it.
		const returnTo = document.activeElement as HTMLElement | null;
		panel?.focus();
		// And back to whatever opened it, rather than to the top of the page.
		return () => returnTo?.focus?.();
	});

	/** Keep Tab inside the panel while it is open. */
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
			// Only when nothing above has answered it. A sheet drawn over Settings (the confirm
			// before a folder is removed, a picker, a menu) answers Escape itself, and Settings
			// underneath must stay open. See `keystrokeIsUnanswered`.
			if (!keystrokeIsUnanswered(event)) return;
			// Swallowed, so one press closes the panel rather than also reaching a control inside it.
			event.preventDefault();
			event.stopPropagation();
			onclose();
			return;
		}
		trap(event);
	}

	/*
	 * How the panel comes and goes. On a phone it is a page pushed over More, so it comes in from the
	 * trailing edge and goes back out to it, the way a phone pushes a page and takes it away again,
	 * and More is under it the whole time. Anywhere wider it is a card that rises a little over the
	 * screen it was opened on, and fades as it goes.
	 */
	function comes(node: Element) {
		return phoneWidth.yes ? fromEdge(node, { edge: 'end' }) : arrive(node, { pace: 'base', y: 12 });
	}
</script>

<svelte:window onkeydown={onKeydown} />

<!-- A button, so clicking beside the panel closes it (the gesture everybody already has for
     "I am done with this") and so that it is announced as something dismissible rather than as
     decoration. The same shape the asset dialog's veil uses, for the same reason. -->
<Veil label="Close settings" layer="settings" {onclose} />

<!-- It comes up from below rather than appearing in place (from the side on a phone, `comes`).
     Settings is somewhere you dip into and come back from, and a panel that rises over the screen
     says it is on top of what you were doing rather than instead of it. -->
<div
	class="panel"
	role="dialog"
	aria-modal="true"
	aria-label="Settings"
	tabindex="-1"
	bind:this={panel}
	transition:comes
>
	<!--
		The way out sits with the content rather than in a corner of the window, so it is beside what
		you were reading instead of a long way across the screen. Escape does the same thing.
	-->
	<!-- The corner is THIS file's box, and the button stands in it. Positioned on the button itself,
	     it would be placed against the tooltip's own wrapper (an inline box with a position of its
	     own and no width, at the panel's start), so it would sit outside the panel's left edge,
	     clipped, and the panel would have no visible way out on any window. -->
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
	/*
	 * Dark enough to push the app back, sheer enough to leave it there: seeing the library behind
	 * the panel is what makes this a dialog on top of the app, so closing puts back what was there.
	 * An opaque veil would read as a page.
	 *
	 * A card with the app around it, not the whole window. Settings is a place (a dozen sections,
	 * some long), so it wants most of the window, but all of it would make it a page with nothing
	 * of the app left to come back to. The margin is the point of the shape.
	 */
	.panel {
		position: fixed;
		/*
		 * A card of a readable size, centred, not a card the width of the monitor. Bounding the
		 * card and letting the margin collapse into it keeps the empty space outside the panel on a
		 * wide screen, where it is the app showing through.
		 *
		 * The top is measured from below the desktop window's title strip and the bottom from the
		 * window's own edge, which is why these are two declarations rather than `inset-block`: at
		 * 5vh of a 700px window the margin is 35px and the strip is 36. Zero in a browser.
		 */
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
		/* The ground a row's hover actions are laid on, so they cover the cells under them in the
		   sheet's own colour rather than the page's. */
		--data-row-base: var(--sift-surface-1);
		box-shadow: var(--elev-3);
	}

	/* Focus is put here on open so the keyboard is inside the panel rather than on the screen
	   behind. Pressing any key then marks whatever holds the focus as keyboard-reached, which would
	   draw an accent ring around the whole panel; the ring belongs on the control somebody walked to. */
	.panel:focus-visible {
		box-shadow: var(--elev-3);
	}

	/* The card does not scroll; what is inside it does. Otherwise the close button scrolls away with
	   the content and a long section leaves no way out but Escape. */
	/* Does NOT scroll. The section list and the section each scroll on their own, and a scroller
	   around both of them is a third one that carries the list, which is navigation, up and off
	   the top while somebody reads a long section. */
	.inner {
		flex: 1;
		min-inline-size: 0;
		min-block-size: 0;
		overflow: hidden;
	}

	/*
	 * Inside the card, in its corner: outside it, against the veil, it would read as belonging to
	 * the app behind.
	 *
	 * `:global` because the class is handed to the shared button. Bigger than the app's standard
	 * control on purpose: it is the way out of a panel covering everything, aimed at without
	 * looking. Escape also closes it.
	 *
	 * SQUARE, the shared button's own box shape with its own corner, not a circle. The close is a
	 * control in the card's top strip like every other press on
	 * the panel, and every other press there is the box.
	 */
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
		/* The change steps over --dur-instant rather than happening between frames. */
		transition:
			color var(--dur-instant) var(--ease),
			border-color var(--dur-instant) var(--ease);
	}

	.close-slot :global(.close:hover) {
		color: var(--sift-ink);
		border-color: var(--sift-ink-3);
	}

	/* On a phone the card is the screen: a margin there costs the only column there is, and the app
	   behind it is not visible anyway at that width. The phone's one width (`phoneWidth`), where the
	   shell inside becomes one column, so no window between two widths is a card with a margin
	   holding a phone's one-column shell. */
	@media (max-width: 767px) {
		.panel {
			inset: 0;
			border: 0;
			border-radius: 0;
		}

		/* At the end of the section's top strip, level with its way back (`SettingsShell`): the
		   shell's own inset from the top and the side, past the notch, and a finger's size. */
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
