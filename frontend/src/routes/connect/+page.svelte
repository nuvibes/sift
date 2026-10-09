<script lang="ts">
	/* Saying which computer the library is on. The desktop client's first screen in client mode. */
	import { onMount } from 'svelte';

	import { bridge } from '$lib/bridge';
	import { bridge as shell, type SavedServer } from '$lib/bridge';
	import {
		BackButton,
		Button,
		DoorCard,
		Field,
		Note,
		Problem,
		TextInput,
		Tooltip
	} from '$lib/components/common';

	let address = $state('');
	let error = $state<string | null>(null);
	let busy = $state(false);

	const inTheApp = bridge.canSaveServer();
	/* Whether there is a question BEFORE this one to go back to, which is the mode question. */
	const canGoBack = bridge.canSetUp();

	/* Every address the shell has been told, for the case this screen is up because the one it
	 * tried has moved or gone: the way back is another one on the list, or taking the dead one off
	 * it. Nothing here is the library's: an address is the one thing this computer keeps. */
	let servers = $state<SavedServer[]>([]);

	/* Whatever was tried last, so a second attempt starts from the address rather than from
	 * blank. */
	onMount(() => {
		void (async () => {
			const state = await shell.connectState();
			address = state.last ?? '';
			servers = state.servers;
			if (state.problem) error = state.problem;
		})();
	});

	function use(one: SavedServer) {
		address = one.origin;
		error = null;
	}

	async function forget(one: SavedServer) {
		servers = await shell.forgetServer(one.origin);
		if (address === one.origin) address = '';
	}

	async function goBack() {
		if (busy) return;
		busy = true;
		error = null;
		const settled = await bridge.setupBack();
		/* Nothing is put back on `ok`: the shell is already drawing the mode question. */
		if (settled.ok) return;
		error = settled.refusal;
		busy = false;
	}

	async function connect(event: SubmitEvent) {
		event.preventDefault();
		const typed = address.trim();
		if (!typed) return;
		busy = true;
		error = null;
		try {
			/* The shell answers what went wrong in its own words, because it is the side that knows:
			 * it normalised the address, it tried to reach it, and it saw what came back. */
			const refusal = await bridge.saveServer(typed);
			if (refusal !== null) error = refusal;
		} finally {
			busy = false;
		}
	}
</script>

<svelte:head>
	<title>Sift</title>
</svelte:head>

{#if inTheApp}
	<DoorCard
		heading="Which computer is your library on?"
		explain="Type the address of the computer running Sift. It's usually something like http://192.168.1.20:5171."
		onsubmit={connect}
	>
		<Field label="Address">
			{#snippet control({ id, describedBy })}
				<TextInput
					{id}
					type="text"
					inputmode="url"
					placeholder="http://192.168.1.20:5171"
					{describedBy}
					autocapitalize="none"
					spellcheck="false"
					bind:value={address}
				/>
			{/snippet}
		</Field>

		<!-- `Problem` announces itself, because somebody using a screen reader has no other way to
		     know the form came back with something to say. -->
		<Problem message={error} />

		<Button tone="primary" type="submit" disabled={busy}>
			{busy ? 'Connecting\u2026' : 'Connect'}
		</Button>

		{#if servers.length > 0}
			<!-- The addresses this computer has been pointed at. -->
			<ul class="saved" aria-label="Saved addresses">
				{#each servers as one (one.origin)}
					<li>
						<Button tone="ghost" size="small" onclick={() => use(one)}>{one.label}</Button>
						<Tooltip label="Forget {one.label}">
							<Button
								tone="ghost"
								size="small"
								icon="close"
								aria-label="Forget {one.label}"
								onclick={() => void forget(one)}
							/>
						</Tooltip>
					</li>
				{/each}
			</ul>
		{/if}

		{#if canGoBack}
			<!-- Back to the mode question. Reached here by choosing Client Install, and somebody who
			     picked it by mistake needs a way out of this screen other than closing the window. -->
			<BackButton label="How Sift runs" onback={() => void goBack()} />
		{/if}

		<!-- What a window onto another computer can do: everything that computer's own app can,
		     acting THERE, with the one exception Windows makes (its permission prompt is pressed at
		     the computer running Sift). -->
		<p class="quiet">
			Nothing is stored on this computer but the address. Your library, and everything in it, stays
			on the machine you point at.
		</p>
		<p class="quiet">
			From here you can do everything the Sift app on that computer can, its settings and a restart
			included, and it happens there. The one exception is Windows' own permission prompt, which is
			approved on the computer running Sift.
		</p>
	</DoorCard>
{:else}
	<DoorCard
		heading="This screen belongs to the Sift app"
		explain="It's where the desktop app asks which computer your library is on."
	>
		<!-- The same note the other door says its "already running" in (`/start`): one fact, one
		     shape, whichever of the two a browser lands on. -->
		<Note>
			You are looking at a Sift that's already running, so there's nothing to point anywhere. If you
			meant to open a different library, do it from the Sift app on your own computer.
		</Note>
	</DoorCard>
{/if}

<style>
	.saved {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}

	.saved li {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-2);
	}
</style>
