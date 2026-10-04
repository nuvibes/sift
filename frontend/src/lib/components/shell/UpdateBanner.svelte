<script lang="ts">
	import { Button, ShellBanner } from '$lib/components/common';
	/* A quiet line across the top when a newer Sift exists.
	 *
	 * It is a notice, not a demand: one sentence, a link to the section that explains what to do,
	 * and a way to make it go away. Dismissing it is per version, so hiding this one does not mean
	 * never hearing about a security fix: the next release brings it back on its own.
	 *
	 * It renders nothing at all unless there is genuinely something to say. A check that failed, a
	 * version that is current, or an installation with no outbound network all draw no banner,
	 * which is why nothing here has a loading or an error state.
	 */
	import { onMount } from 'svelte';
	import { session } from '$lib/shell/session.svelte';
	import { openSettingsInstead } from '$lib/settings-ui/settings-view';
	import { updates } from '$lib/shell/updates.svelte';

	onMount(() => {
		// Only an admin can act on this, and only an admin is allowed to ask. A guest drawing a
		// banner about a command they cannot run would be telling them to go and find somebody.
		if (session.isAdmin) void updates.load();
	});
</script>

{#if session.isAdmin && updates.shouldNotify}
	<ShellBanner>
		Sift {updates.state?.latest_version} is available.
		<!-- Still an anchor to a real address: opening it in a tab and copying the link both keep
		     working, and a plain click opens the panel over whatever this banner interrupted. -->
		<a href="/settings/updates" onclick={(event) => openSettingsInstead(event, 'updates')}>
			See what is new and how to update
		</a>.
		{#snippet action()}
			<Button onclick={() => updates.dismiss()} aria-label="Hide this notice">Dismiss</Button>
		{/snippet}
	</ShellBanner>
{/if}
