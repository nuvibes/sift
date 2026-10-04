<script lang="ts">
	/* NOT ON THE GALLERY: it draws nothing. It holds swap mode's two rules about who may keep it. */
	/*
	 * Who swap mode belongs to, and when it ends, watched from outside the signed-in shell.
	 *
	 * Mounted once at the top of the layout, beside the window bar, and not inside the shell with
	 * the drawer: signing out and locking Sift take the shell down in the same moment the session
	 * changes, so a rule mounted inside it never sees the change and the picks kept in this tab
	 * would come back at the next sign-in.
	 */
	import { session } from '$lib/shell/session.svelte';
	import { vault } from '$lib/shell/vault.svelte';
	import { swapMode } from './mode.svelte';

	// The mode learns who is signed in and takes back what a full page load left in this tab
	// (`adopt`); nobody signed in, or somebody else, throws it away. Not before the page knows.
	$effect(() => {
		if (session.viewer === undefined) return;
		swapMode.adopt(session.viewer?.id ?? null, session.isAdmin);
	});

	/* Locking Hidden away leaves the mode: its picks may name Hidden things, and a name kept in this
	   tab's session storage past the lock would be the one thing the lock exists to withhold. */
	let wasUnlocked = false;
	$effect(() => {
		const unlocked = vault.unlocked;
		if (wasUnlocked && !unlocked && swapMode.on) swapMode.leave();
		wasUnlocked = unlocked;
	});
</script>
