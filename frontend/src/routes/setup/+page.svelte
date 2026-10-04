<script lang="ts">
	/*
	 * First run: create the one admin.
	 *
	 * Reachable only while there is no account. Once one exists this screen would be a way for
	 * whoever reached it first to claim somebody else's instance, so it redirects, and the server
	 * refuses a second setup regardless, which is what actually prevents it.
	 */
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { needsSetup } from '$lib/shell/session.svelte';
	import AuthCard from '../login/AuthCard.svelte';

	let checked = $state(false);

	onMount(async () => {
		try {
			if (!(await needsSetup())) {
				await goto('/login');
				return;
			}
		} catch {
			// Fall through and let the form's own failure explain itself.
		}
		checked = true;
	});
</script>

<svelte:head>
	<title>Sift</title>
</svelte:head>

{#if checked}
	<AuthCard mode="setup" />
{/if}
