<script lang="ts">
	/*
	 * Signing in. Sends anybody who arrives at a fresh instance to setup instead: there is no
	 * account to sign in to yet, and a login form that can only fail is a dead end.
	 */
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { needsSetup, session } from '$lib/shell/session.svelte';
	import AuthCard from './AuthCard.svelte';

	let checked = $state(false);

	/*
	 * Whether somebody is already signed in is not known yet when this mounts: the layout asks the
	 * server and the answer arrives after. Sampling it in `onMount` therefore always reads "signed
	 * out", and a signed-in person who refreshes this address or opens it from a bookmark is shown a
	 * sign-in form. Watching it instead means the redirect happens whenever the answer lands.
	 */
	$effect(() => {
		if (session.isSignedIn) void goto('/browse', { replaceState: true });
	});

	onMount(async () => {
		try {
			if (await needsSetup()) {
				await goto('/setup');
				return;
			}
		} catch {
			// If the question cannot be asked, show the login form: it is the right screen for
			// every instance except a brand new one, and its own failure will say more.
		}
		checked = true;
	});
</script>

<svelte:head>
	<title>Sift</title>
</svelte:head>

{#if checked}
	<AuthCard mode="login" />
{/if}
