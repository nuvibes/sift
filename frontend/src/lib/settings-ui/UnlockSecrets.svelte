<script lang="ts">
	/* Saved cookies and tunnels are sealed with a key that only exists while Sift is running. */
	import { SectionHeading } from '$lib/components/common';
	import UnlockField from './UnlockField.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { explainAbsentRows } from '$lib/settings-ui/settings-anchor.svelte';

	let { onunlocked }: { onunlocked?: () => void } = $props();

	/* Drawn only while they are locked, so a search or a link naming it at any other time is
	   told there is nothing to unlock rather than that the row went missing. */
	$effect(() =>
		explainAbsentRows((key) =>
			key === 'sites.unlock' && !session.secretsLocked
				? { because: 'Your saved keys, cookies and tunnels are already unlocked.' }
				: null
		)
	);
</script>

{#if session.secretsLocked}
	<section class="locked">
		<!-- The heading names all THREE things this unlocks, and that is not tidying. -->
		<SectionHeading id="sites.unlock">Unlock your saved keys, cookies and tunnels</SectionHeading>
		<p class="lede">
			Sift restarted since you last entered your password. You are still signed in, but your saved
			stash-box keys, Site cookies and tunnels stay locked until you enter it again. Nothing was
			lost.
		</p>

		<UnlockField {onunlocked} />
	</section>
{/if}

<style>
	.locked {
		margin-block-end: var(--space-6);
		padding: var(--space-4);
		/* The card's light, its edge under this transparent border (see `--sift-card`). */
		border: 1px solid transparent;
		border-radius: var(--radius-lg);
		background: var(--sift-card);
	}

	.lede {
		margin: 0 0 var(--space-4);
	}

	/* How wide this one box may grow, and nothing else: the focus ring is the global
	   `:focus-visible` rule's. */
	.locked :global(.input) {
		inline-size: min(320px, 100%);
	}
</style>
