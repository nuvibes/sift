<script lang="ts">
	/*
	 * Hidden: everything the vault is concealing, and the only place to look at it as a set.
	 *
	 * The top bar's control opens and shuts it, and that is not the same job. Unlocking puts hidden
	 * things back into the grid they were taken out of (scattered through thousands of others, in
	 * date order, indistinguishable from everything else), so without this the only way to review
	 * what you had hidden would be to remember it. This screen answers "what is in there".
	 *
	 * The same grid as Browse, asking the same endpoint, with one parameter the server understands.
	 * It grants nothing: `hidden=true` filters the page to concealed rows, and concealed rows are
	 * only ever sent to somebody who has already unlocked. Locked, this screen is empty: not
	 * refused, empty, which is the same thing the rest of the application says about the vault and
	 * the reason it says nothing at all.
	 *
	 * Named for what it does. "Vault" is what the API, the database and the code call it, and that
	 * stays; see `$lib/shell/vault.svelte` for why the two names both exist.
	 */
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

	/*
	 * Arriving here with no PIN asks for one at once.
	 *
	 * Nothing can be hidden, or shown again, without a PIN, so the first press on Hidden is where
	 * one is made. Asked once per arrival: put off, the screen says why it is empty and the button on
	 * it asks again.
	 */
	$effect(() => {
		if (vault.loaded && !vault.pinSet) vaultPrompt.ask();
	});

	/* Constant, like Favorites and for the same reason: this screen IS the query. Taking it from the
	 * address would make `/hidden?hidden=false` a real address saying the opposite of its own title. */
	const QUERY = { hidden: 'true' };

	/*
	 * The page's own search, the box every wall wears (`WallControls`), searching THIS screen. Its
	 * words ride in `q`, the address's free text, which the grid already reads on top of this
	 * screen's own query; the top bar's box searches the whole library, so without this there would
	 * be no way to look for one thing among everything hidden. Kept in the address like every
	 * wall's words (`WallWords`), so Back and a link carry them and the bar's chip can take them off.
	 */
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

	/*
	 * What an empty answer means, which depends on why it is empty.
	 *
	 * Locked and empty look identical from here: the server sends no rows either way, deliberately.
	 * The client does know whether this browser has unlocked, though, and saying "unlock to see them"
	 * is worth more than "nothing here" to somebody who hid something an hour ago and is now being
	 * told, apparently, that they did not.
	 *
	 * This is a hint about the CONTROL, not a report about the contents. It does not say whether
	 * anything is in there, because that is the one thing the vault is keeping back.
	 */
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
		Shut, this screen is one question and one answer, so it is drawn as one, rather than a
		grid with a line telling you to go and use the control in the top bar, on the one screen
		whose entire purpose is what is behind that control. The same frame and title the open
		screen has, so unlocking fills the page rather than replacing it; the answer is the page's
		empty state, with the lock in the disc every empty page draws its glyph in.
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
