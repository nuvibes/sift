<script lang="ts">
	/* Hidden: everything the vault is concealing, and the only place to look at it as a set. */
	import AssetGrid from '$lib/components/AssetGrid.svelte';
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import WallControls from '$lib/components/entity/WallControls.svelte';
	import { WallWords, emptyWallSays, wordsIn } from '$lib/components/shell/wall-words';
	import { Empty } from '$lib/components/common';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import UnlockButton from '$lib/components/vault/UnlockButton.svelte';
	import { vault, vaultPrompt } from '$lib/shell/vault.svelte';

	/* Arriving here with no PIN asks for one immediately. */
	$effect(() => {
		if (vault.loaded && !vault.pinSet) vaultPrompt.ask();
	});

	/* Constant, like Favorites and for the same reason: this screen IS the query. */
	const QUERY = { hidden: 'true' };

	/* The page's own search, the box every wall wears (`WallControls`), searching THIS screen. */
	let term = $state(wordsIn(page.url));
	const words = new WallWords();
	const typedHere = $derived(wordsIn(page.url));
	$effect(() => {
		const arrived = typedHere;
		untrack(() => {
			if (words.echoed(arrived)) return;
			term = arrived;
		});
	});

	/* What an empty answer means, which depends on why it is empty. */
	const empty =
		'Nothing is hidden. Anything you hide — a person, a collection, a folder — shows up here.';
</script>

<svelte:head><title>Hidden</title></svelte:head>

{#if vault.unlocked}
	<AssetGrid
		query={QUERY}
		icon="visibility_off"
		title="Hidden"
		empty={emptyWallSays('hidden files', typedHere, false, empty)}
		pinnable
	>
		{#snippet tools()}
			<WallControls
				noun="file"
				plural="hidden files"
				bind:term
				onsettled={(typed) => words.write(page.url, typed)}
			/>
			<!-- The way back, on the screen it applies to. The top bar keeps its own: that one is also
			     the panic button and has to be reachable from wherever somebody is standing. -->
			<UnlockButton />
		{/snippet}
	</AssetGrid>
{:else}
	<!--
		Shut, this screen is one question and one answer, so it is drawn as one, rather than a grid
		with a line telling you to go and use the control in the top bar, on the one screen whose
		entire purpose is what is behind that control.
	-->
	<PageFrame>
		{#snippet header()}
			<PageHeader title="Hidden" icon="visibility_off" />
		{/snippet}
		<Empty scope="page" icon="lock">
			{#if vault.loaded && !vault.pinSet}
				Hiding things takes a PIN, and it's what shows them again. You haven't made one yet.
			{:else}
				Enter your PIN to view your hidden items.
			{/if}
			{#snippet action()}
				<UnlockButton big />
			{/snippet}
		</Empty>
	</PageFrame>
{/if}
