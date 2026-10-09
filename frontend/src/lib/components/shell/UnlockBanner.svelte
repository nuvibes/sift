<script lang="ts">
	/* NOT ON THE GALLERY: it is drawn only while a session Sift restarted under has its saved keys
	   locked, a condition that cannot be produced on demand. */

	/* A restart seals every saved key, so work parks; the bar asks for the password right there.
	 * Not now holds until this server run ends, and returns when more work parks for the key. */
	import { Button, ShellBanner } from '$lib/components/common';
	import UnlockField from '$lib/settings-ui/UnlockField.svelte';
	import { imports } from '$lib/library/imports.svelte';
	import { unlock } from '$lib/shell/unlock.svelte';

	// Null until the queue has been read: nothing counted yet is not "the parked work has gone".
	$effect(() => unlock.follow(imports.page === null ? null : imports.passwordWanted));
</script>

<!-- Wraps: the field takes its own line under the sentence on a phone. -->
{#if unlock.shown(imports.passwordWanted)}
	<ShellBanner testId="unlock-banner" wraps>
		Sift restarted, so your saved keys, cookies and tunnels are locked. Enter your password to
		unlock them.
		{#snippet action()}
			<span class="act">
				<UnlockField layout="inline">
					{#snippet beside()}
						<Button onclick={() => unlock.notNow(imports.passwordWanted)}>Not now</Button>
					{/snippet}
				</UnlockField>
			</span>
		{/snippet}
	</ShellBanner>
{/if}

<style>
	/* Presses sit at the end of the line, and of their own line once it wraps. */
	.act {
		display: flex;
		flex: 1 1 auto;
		justify-content: flex-end;
	}
</style>
