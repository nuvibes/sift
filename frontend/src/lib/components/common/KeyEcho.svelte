<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'KeyEcho',
		category: 'composition',
		role: 'the state a key just changed, shown for a moment over the picture it changed',
		basis: 'own',
		states: ['lit', 'unlit']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: there is no behaviour to borrow. This is a glyph, a moment and a fade. The
	library's nearest thing is a toast, which reports something elsewhere. */

	/* What a key just did (R, L), shown on the picture for about a second. It echoes a counted
	   press, so a change through the drawer's control shows nothing; the hold ignores motion. */
	import Icon from '$lib/components/Icon.svelte';
	import type { IconName } from '$lib/design/icons';
	import { arrive } from '$lib/shell/motion.svelte';

	interface Props {
		/** The glyph for the state that is true now. */
		icon: IconName;
		/** The same state in words, for a screen reader and for the gallery. */
		label: string;
		/** The off answer, drawn dimmer: two repeat answers share a glyph. */
		muted?: boolean;
		/** The number or name behind the state ("+2 (54)", "2x"); absent is the glyph alone. */
		detail?: string;
		/** How many times the key was pressed; a count, since a second press may change nothing. */
		press?: number;
	}

	let { icon, label, muted = false, detail, press = 0 }: Props = $props();

	/** How long it stays before it starts going. About a second, with the fades either side of it. */
	const HOLD_MS = 900;

	let showing = $state(false);

	/* Plain variables: the effect below writes and reads them (see `appears`). */
	let seen: number | null = null;
	let timer: ReturnType<typeof setTimeout> | undefined;

	$effect(() => {
		const now = press;
		/* The count it mounted with is never echoed. */
		if (seen === null) {
			seen = now;
			return;
		}
		if (now === seen) return;
		seen = now;
		showing = true;
		clearTimeout(timer);
		timer = setTimeout(() => (showing = false), HOLD_MS);
	});

	// Cleared on unmount, or it fires against a component that is gone.
	$effect(() => () => clearTimeout(timer));
</script>

{#if showing}
	<!-- A status with the words as its name, so a screen reader hears the same answer. -->
	<div
		class="key-echo"
		class:unlit={muted}
		class:worded={detail !== undefined}
		role="status"
		aria-label={detail === undefined ? label : `${label} ${detail}`}
		transition:arrive={{ pace: 'fast' }}
	>
		<Icon name={icon} size={20} label="" />
		{#if detail !== undefined}
			<!-- Hidden: the badge's name already says it. -->
			<span class="detail" aria-hidden="true">{detail}</span>
		{/if}
	</div>
{/if}

<style>
	/* A glass badge on the media scrim; the caller places it, since the frames differ. */
	.key-echo {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: var(--space-2);
		inline-size: var(--space-8);
		block-size: var(--space-8);
		border-radius: var(--radius-md);
		background: var(--sift-scrim-strong);
		color: var(--sift-ink);
		/* It reports and is never reached for. */
		pointer-events: none;
	}

	/* With a detail it is a pill that grows, the square as its floor. */
	.key-echo.worded {
		inline-size: auto;
		min-inline-size: var(--space-8);
		padding-inline: var(--space-3);
	}

	/* Tabular figures, so a counting volume does not jitter. */
	.detail {
		font: var(--text-label);
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}

	/* The off answer in the quiet ink, as the drawer's unlit control. */
	.key-echo.unlit {
		color: var(--sift-ink-3);
	}
</style>
