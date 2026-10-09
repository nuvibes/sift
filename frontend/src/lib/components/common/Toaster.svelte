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
	the gallery would draw every message twice; the toasts section fires a real one. */
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

	/* A finished download is said here on every screen, once an admin is signed in. */
	$effect(() => finishedDownloads.follow(session.adminUnlocked));
	/* The first-folder benchmark: that it runs, and what it set, in every admin window. */
	$effect(() => benchmarkRun.follow(session.adminUnlocked));

	const GLYPH: Record<ToastTone, IconName> = {
		info: 'info',
		success: 'check',
		error: 'error'
	};
</script>

<!-- Rendered once, by the layout; polite, since these report what already happened. -->
<div class="toaster" role="status" aria-live="polite">
	{#each toasts.items as toast (toast.id)}
		<!--
		They rise in and fade out, as every small surface does: a toast carries the only Undo.
		-->
		<!-- Pointing at a toast, or focusing inside it, stops its clock; release hands back exactly
		the time that was left (`hold`, `release`). -->

		<!-- svelte-ignore a11y_no_static_element_interactions: nothing here is OPERATED by a
			pointer; the handlers stop a clock. -->
		<div
			class="toast {toast.tone}"
			transition:surface
			animate:reflow
			onpointerenter={() => toasts.hold(toast.id)}
			onpointerleave={() => toasts.release(toast.id)}
			onfocusin={() => toasts.hold(toast.id)}
			onfocusout={() => toasts.release(toast.id)}
		>
			<!-- The toast's mark, or its tone's; the wrapper is what scoped CSS can reach. -->
			<span class="mark" class:fetching={toast.icon === 'download'}>
				<Icon name={toast.icon ?? GLYPH[toast.tone]} size={16} />
			</span>
			<!-- The bar sits under the words, so the row keeps its shape. -->
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

			<!-- Named by what they act on, so stacked buttons can be told apart. -->
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
	/* The download arrow travels down and fades, never turning, so it always says "down"
	   (`fetching`). Reduced motion holds it still. */
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

	/* The tone colours the glyph, not the words. */
	.message {
		flex: 1;
		display: grid;
		gap: var(--space-2);
		color: var(--sift-ink);
		/* min 0 and anywhere, so a long filename wraps whole inside the toast, never cut. */
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

	/* A ground as well as ink: the one control on a toast. */
	.close:hover {
		background: var(--sift-surface-4);
		color: var(--sift-ink);
	}

	/* A finger's reach around the cross on a phone, through Pressable's ring. */
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
