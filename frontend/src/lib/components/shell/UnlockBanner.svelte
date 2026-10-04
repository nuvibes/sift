<script lang="ts">
	/* NOT ON THE GALLERY: it is drawn only while a session Sift restarted under has its saved keys
	   locked, a condition the gallery cannot produce and should not fake. The `ShellBanner`,
	   `PasswordInput` and `Button` it is made of are on the gallery. */

	/*
	 * "Sift restarted, so your saved keys, cookies and tunnels are locked."
	 *
	 * A restart keeps the session signed in and seals every saved key, so work that needs one parks
	 * and waits. A Blocked tab that says only that something is waiting on you reads as stuck,
	 * when the password is the whole of the answer and nothing else asks for it.
	 *
	 * So the moment a session's keys are locked, the bar asks, with the field in it: the same
	 * field `Settings > Connections` offers (`UnlockField`). Admins only, because only an admin's
	 * key opens what the work needs.
	 *
	 * Put aside by unlocking, or by Not now. Not now is not never: the bar comes back the moment
	 * work stops for the key (the queue's count of tasks parked for the password goes up), because
	 * that is when the answer to "not now" has changed. A reload does not ask again: Not now is
	 * remembered against the run of the server it was pressed under, so it holds until the next
	 * restart. Unlocking forgets it, so the next restart asks again.
	 */
	import { Button, ShellBanner } from '$lib/components/common';
	import UnlockField from '$lib/settings-ui/UnlockField.svelte';
	import { imports } from '$lib/library/imports.svelte';
	import { unlock } from '$lib/shell/unlock.svelte';

	// Null until the queue has been read: nothing counted yet is not "the parked work has gone".
	$effect(() => unlock.follow(imports.page === null ? null : imports.passwordWanted));
</script>

<!-- The same shape the other notices across the top draw, through `ShellBanner`. It wraps: the
     sentence and the field share a line where there is room, and the field takes its own line
     under the sentence on a phone. -->
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
	/* The field and Not now together at the end of the line, and at the end of their own line once
	   the bar wraps: presses sit on the right. */
	.act {
		display: flex;
		flex: 1 1 auto;
		justify-content: flex-end;
	}
</style>
