<script lang="ts">
	/* Saved cookies and tunnels are sealed with a key that only exists while Sift is running.
	 *
	 * So a restart leaves a session signed in and unable to read any of them. Without this the
	 * only sign would be a refusal, after filling in a form, telling somebody to log in with
	 * their password, which they had. This asks first, and it does not cost the session: the
	 * password unwraps the key back into memory and nothing else changes.
	 *
	 * The field is `UnlockField`, the one the bar across the top of every screen and a parked task
	 * on Activity offer too: one box, one request, one refusal, whichever door it is opened from.
	 */
	import { SectionHeading } from '$lib/components/common';
	import UnlockField from './UnlockField.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { explainAbsentRows } from '$lib/settings-ui/settings-anchor.svelte';

	let { onunlocked }: { onunlocked?: () => void } = $props();

	/* Drawn only while they are locked, so a search or a link naming it at any other time is told
	   there is nothing to unlock rather than that the row went missing. */
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
		<!--
			The heading names all THREE things this unlocks, and that is not tidying.

			"Cookies and tunnels" is two of them, and the third is the one further down the Stash-boxes
			page: the stash-box keys. Named for two, it would read as a panel about downloads to somebody
			whose lookups had all gone quiet, with the boxes below it each wearing a green "Key saved":
			both halves telling the truth about their own half, and neither saying the word that
			connected them.
		-->
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
	   `:focus-visible` rule's. `:global`, because the box is `TextInput`'s own element, compiled
	   in that file's scope. */
	.locked :global(.input) {
		inline-size: min(320px, 100%);
	}
</style>
