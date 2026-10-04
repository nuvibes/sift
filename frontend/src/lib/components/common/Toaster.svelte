<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Toaster',
		category: 'surface',
		role: 'the queue of short messages and the live region that announces them',
		basis: 'own',
		states: ['info', 'ok', 'bad']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* NOT ON THE GALLERY: one of these exists, in the layout, for the whole app. Importing it into
	   the gallery would put a SECOND one on the page and every message would be drawn twice. What it
	   draws is already shown there: the toasts section fires a real one, and this is what catches
	   it. */
	/* WHY NOT BITS-UI: bits-ui has no toast. What is here is a live region and a queue: announcement order and
	   how long a message stays, neither of which is a widget. */
	import Icon from '$lib/components/Icon.svelte';
	import ProgressBar from '$lib/components/common/ProgressBar.svelte';
	import { reflow, surface } from '$lib/shell/motion.svelte';
	import HistorySentence from './HistorySentence.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import type { IconName } from '$lib/design/icons';
	import type { ToastTone } from '$lib/shell/toasts.svelte';
	import { finishedDownloads } from '$lib/shell/toasts-downloads.svelte';
	import { benchmarkRun } from '$lib/shell/toasts-benchmark.svelte';
	import { session } from '$lib/shell/session.svelte';

	/* A finished download is said here, on every screen, rather than by the Downloads screen
	   alone; once an admin is signed in. See `toasts-downloads.svelte.ts`. */
	$effect(() => finishedDownloads.follow(session.adminUnlocked));
	/* The benchmark Sift runs by itself on the first library folder: that it is running, and what it
	   set, in every admin window. See `toasts-benchmark.svelte.ts`. */
	$effect(() => benchmarkRun.follow(session.adminUnlocked));

	const GLYPH: Record<ToastTone, IconName> = {
		info: 'info',
		success: 'check',
		error: 'error'
	};
</script>

<!--
	Rendered once, by the layout. Polite rather than assertive: these announce things that already
	happened, and interrupting someone mid-sentence to tell them a tag was saved is not politeness.
-->
<div class="toaster" role="status" aria-live="polite">
	{#each toasts.items as toast (toast.id)}
		<!--
			They rise into place as they arrive and fade as they go, as every small surface does.

			A toast that blinks into existence is one somebody's eye never catches, and these are
			the only place an undo is ever offered, so a message missed is an action that cannot be
			taken back. Leaving matters as much as arriving: three of them can be stacked, and one
			vanishing instantly makes the two below it jump up a row for no visible reason.
		-->
		<!--
			Pointing at a toast, or reaching into it with the keyboard, stops its clock.

			Four seconds is enough to read a short sentence and is not enough to read one, decide it
			was not what you wanted, and reach the Undo before it goes. Both halves matter: the
			pointer is how it is usually read, and the keyboard is the only way somebody who cannot
			use a pointer reaches the button at all. `pointerenter`/`pointerleave` rather than
			`mouseenter`, so a finger holds it too.

			The counting is the store's: it hands back exactly the time that was left rather than
			starting over, so a toast held three seconds into its four has one second when it is let
			go. See `hold` and `release`.
		-->
		<!-- svelte-ignore a11y_no_static_element_interactions: nothing here is OPERATED by a
		     pointer. The handlers stop a clock; the message is still a message, the buttons inside it
		     are still the only things that do anything, and giving this box a role would announce a
		     second live region inside the one the toaster already is. -->
		<div
			class="toast {toast.tone}"
			transition:surface
			animate:reflow
			onpointerenter={() => toasts.hold(toast.id)}
			onpointerleave={() => toasts.release(toast.id)}
			onfocusin={() => toasts.hold(toast.id)}
			onfocusout={() => toasts.release(toast.id)}
		>
			<!-- The toast's own mark where it has one, its tone's otherwise. The wrapper is what the
			     stylesheet can reach: `Icon` renders its own element carrying that component's hash,
			     and a class handed to a component is not reached by the caller's scoped CSS. -->
			<span class="mark" class:fetching={toast.icon === 'download'}>
				<Icon name={toast.icon ?? GLYPH[toast.tone]} size={16} />
			</span>
			<!-- The bar sits UNDER the words rather than beside them, so the message keeps the width
			     it has in every other toast and the row does not change shape when work starts. -->
			<span class="message">
				<!-- The runs as a History line draws them: a thing it names is the way to it. -->
				<span><HistorySentence pieces={toast.pieces} /></span>
				{#if toast.progress}
					<ProgressBar
						value={toast.progress.value}
						max={toast.progress.max}
						label={toast.message}
					/>
				{/if}
			</span>

			<!--
				Named by what they act on, not by what they do. Three of these can be stacked, and
				"Dismiss, Dismiss, Dismiss" is a list of buttons a screen reader cannot tell apart:
				the message is the only thing that distinguishes them.
			-->
			{#if toast.action}
				<button
					class="action"
					aria-label="{toast.action.label}: {toast.message}"
					onclick={() => {
						toast.action?.run();
						toasts.dismiss(toast.id);
					}}>{toast.action.label}</button
				>
			{/if}

			<button
				class="close"
				aria-label="Dismiss: {toast.message}"
				onclick={() => toasts.dismiss(toast.id)}
			>
				<Icon name="close" size={16} />
			</button>
		</div>
	{/each}
</div>

<style>
	/*
	 * The mark does not TURN, and it does move. Those are two different things and the difference is
	 * the whole reason this works.
	 *
	 * A turning mark would rotate the ARROW, and an arrow that points down for only part of every
	 * revolution has stopped saying "down".
	 *
	 * The mark is the bare `download` arrow, the one this application uses for downloading
	 * everywhere, and a bare arrow can be moved in the one direction it is already pointing. So it
	 * travels down and fades, and comes back at the top, which is the thing an arrow means, done
	 * repeatedly, rather than the thing a ring means done sideways. The glyph never leaves the
	 * vertical, so it says "down" at every instant of it.
	 *
	 * Keyed on the MARK rather than on the toast: what moves is the download arrow, wherever a toast
	 * has one, and a toast that is announcing rather than working does not carry it. See `icon` in
	 * `toasts.svelte.ts`, which exists for exactly the handful of messages about work in flight.
	 *
	 * `fetching` is one of the motions `app.css` names, at the pace of anything that repeats while
	 * work runs.
	 *
	 * Reduced motion stops it: the arrow simply sits still, which is the correct answer for a mark
	 * whose whole content is movement. There is nothing left to say quietly, and a static arrow
	 * beside the word "Downloading..." says it in words instead.
	 */
	.mark {
		display: inline-flex;
		flex: none;
	}

	.mark.fetching {
		animation: fetching var(--dur-loop) var(--ease) infinite;
	}

	:global(:root[data-motion='reduce']) .mark.fetching {
		animation: none;
	}

	.toaster {
		position: fixed;
		right: var(--space-4);
		bottom: var(--space-4);
		z-index: var(--z-toast);
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		/* The strip is only a place to put them. Clicks go to the page underneath it. */
		pointer-events: none;
	}

	.toast {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		max-inline-size: 380px;
		padding: var(--space-3) var(--space-4);
		border-radius: var(--radius-md);
		background: var(--sift-surface-2);
		box-shadow: var(--elev-2);
		font: var(--text-body);
		color: var(--sift-ink);
		pointer-events: auto;
	}

	.toast.success {
		color: var(--sift-ok);
	}

	.toast.error {
		color: var(--sift-bad-text);
	}

	/* The tone colours the glyph, not the words. Body text stays the colour body text is: a
	   sentence in red is harder to read and says nothing the icon has not already said. */
	.message {
		flex: 1;
		display: grid;
		gap: var(--space-2);
		color: var(--sift-ink);
		/* A long word breaks rather than running out of the toast. A downloaded file's name is ONE
		   word to the layout ("Instagram810000000_10000000001..." has no space in it) and a flex
		   item's floor is its longest word, so the message would push straight past the toast's
		   right edge and under the close button. `min-inline-size: 0` lets the message be narrower
		   than that word; `overflow-wrap: anywhere` is what then breaks it. Wrapped, not cut with
		   an ellipsis: the toast is the one place the name is said, and the end of a download's
		   name (the number) is the part that tells two of them apart. In the primitive, so no
		   caller has to remember it. */
		min-inline-size: 0;
		overflow-wrap: anywhere;
	}

	.action {
		flex: none;
		border: 0;
		padding: 0;
		background: none;
		color: var(--sift-accent-text);
		font: var(--text-label);
		cursor: pointer;
	}

	.close {
		flex: none;
		display: flex;
		border: 0;
		padding: 0;
		background: none;
		color: var(--sift-ink-3);
		cursor: pointer;
		border-radius: var(--radius-sm);
		transition:
			background var(--dur-instant) var(--ease),
			color var(--dur-instant) var(--ease);
	}

	/* A ground as well as the ink. The registers refuse a colour change on its own (it is invisible
	   to anybody not looking straight at it) and this is the one control on a toast, so missing it
	   means missing the only way to dismiss the thing by hand. */
	.close:hover {
		background: var(--sift-surface-4);
		color: var(--sift-ink);
	}

	/* A finger's reach around the cross on a phone, without the toast growing: the ring `Pressable`
	   draws, for the reason given there. */
	@media (max-width: 767px) {
		.close {
			position: relative;
		}

		.close::after {
			content: '';
			position: absolute;
			inset-block: min(0px, calc((100% - var(--touch-target)) / 2));
			inset-inline: min(0px, calc((100% - var(--touch-target)) / 2));
		}
	}

	/* Above the tabs, which are the bottom of the screen on a phone. */
	@media (max-width: 767px) {
		.toaster {
			right: var(--space-3);
			left: var(--space-3);
			bottom: calc(56px + var(--space-3));
		}

		.toast {
			max-inline-size: none;
		}
	}
</style>
