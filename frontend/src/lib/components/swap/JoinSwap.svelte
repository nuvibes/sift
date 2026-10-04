<script lang="ts">
	/* NOT ON THE GALLERY: its Join dials another device through a real tunnel. The gallery draws the steps a session hands over instead (`a-swap-step-by-step`). */
	/*
	 * Join a swap: paste the token they sent, and choose where what arrives is put.
	 *
	 * The box is the Downloads screen's paste box in shape (a two-line box and one button at its
	 * foot) because a token is pasted the way a link is. It is read as ONE string: a chat window
	 * that wrapped it, a space where a hyphen was, a trailing newline all come out (`tokenFrom`).
	 *
	 * The folder is chosen from the same list the Downloads screen's "Download folder" offers, less its
	 * default row: a swap has no default, and a received file with nowhere to land is refused
	 * before anything is dialled rather than after it has crossed. Under the folder, the sentence
	 * that says where in it things land: one Swap folder for the whole swap, split by People and
	 * Sites.
	 *
	 * And the tunnel this side dials through, chosen here in the open (`SwapTunnelChooser`), the one
	 * chosen last time offered first. A join never goes out through a route nobody chose for it.
	 *
	 * A refusal is said under the part of the form it is about, where the fix is: a token that does
	 * not read under the token, a missing folder under the folder, and a tunnel on the host's own
	 * VPN server under the Tunnel chooser (the route names the part in `ApiError.field`). One that
	 * is about no part (the keys locked, a fault) is said over Join.
	 *
	 * ## Under Exchange
	 *
	 * The same form, the same join: pasting a token is one path whichever door it came through. With
	 * `exchange` the panel is called Exchange and says the rest, that what to send them is chosen once
	 * the code is compared. Whether the swap goes both ways is the token-holder's, said in the hello.
	 */
	import { onMount } from 'svelte';
	import { ApiError } from '$lib/api/client';
	import {
		Button,
		Field,
		Panel,
		Problem,
		SectionHeading,
		Select,
		TextArea
	} from '$lib/components/common';
	import { Destinations } from '$lib/library/destinations.svelte';
	import SwapTunnelChooser from './SwapTunnelChooser.svelte';
	import { joinSwap, landingWords, tokenFrom, type SwapTunnel } from './swap';

	interface Props {
		onjoined: (sessionId: string) => void;
		/** Pasted under Exchange: the panel says so, and what to expect once the code matches. */
		exchange?: boolean;
		/** In a panel of its own (the Swap page), or bare inside something that already frames it
		 *  (swap mode's drawer, under Receive). */
		framed?: boolean;
	}

	let { onjoined, framed = true, exchange = false }: Props = $props();
	/* What this form joins, in its words: under Exchange, every sentence says exchange. */
	const noun = $derived(exchange ? 'exchange' : 'swap');

	let token = $state('');
	let folderId = $state('');
	let tunnelId = $state('');
	let tunnels = $state<SwapTunnel[] | null>(null);
	/** The parts of the form a refusal is said under; null for one about no part. */
	type Part = 'token' | 'folder' | 'tunnel';
	/** The route's names for them: the join's own body fields. */
	const PART_OF_FIELD: Record<string, Part> = {
		token: 'token',
		dest_folder_id: 'folder',
		tunnel_id: 'tunnel'
	};
	let problem = $state<{ part: Part | null; words: string } | undefined>(undefined);
	const saidUnder = (part: Part | null) => (problem?.part === part ? problem.words : undefined);
	let busy = $state(false);

	const destinations = new Destinations();
	destinations.follow();
	onMount(() => void destinations.load());

	const folders = $derived(destinations.placed);
	const folderName = $derived(folders.find((one) => one.value === folderId)?.label ?? null);

	async function join(): Promise<void> {
		if (!tokenFrom(token)) {
			problem = { part: 'token', words: 'Paste the token they sent you.' };
			return;
		}
		if (!folderId) {
			problem = { part: 'folder', words: 'Choose a folder to put received files in.' };
			return;
		}
		if (!tunnelId) {
			problem = { part: 'tunnel', words: `Choose a tunnel for the ${noun} to go through.` };
			return;
		}
		busy = true;
		problem = undefined;
		try {
			onjoined((await joinSwap(token, folderId, tunnelId)).session_id);
		} catch (error) {
			problem =
				error instanceof ApiError
					? {
							part: (error.field && PART_OF_FIELD[error.field]) || null,
							words: error.detail ?? error.message
						}
					: { part: null, words: `The ${noun} couldn't start.` };
		} finally {
			busy = false;
		}
	}
</script>

{#snippet form()}
	<form
		class="join"
		onsubmit={(event) => {
			event.preventDefault();
			void join();
		}}
	>
		<Field label="Paste the token" error={saidUnder('token')}>
			{#snippet control({ id, describedBy, invalid })}
				<TextArea
					{id}
					rows={3}
					{invalid}
					bind:value={token}
					{describedBy}
					placeholder="ABCD-EFGH-..."
					spellcheck={false}
					autocomplete="off"
				/>
			{/snippet}
		</Field>
		<label class="named" for="swap-folder">Put received files in</label>
		<Select
			id="swap-folder"
			bind:value={folderId}
			options={folders}
			placeholder="Choose a folder"
			invalid={saidUnder('folder') !== undefined}
			describedBy={saidUnder('folder') ? 'swap-folder-error' : undefined}
		/>
		{#if saidUnder('folder')}
			<p class="error" id="swap-folder-error" role="alert">{saidUnder('folder')}</p>
		{/if}
		{#if folderName}
			<p class="lands">{landingWords(folderName, undefined, noun)}</p>
		{/if}
		<SwapTunnelChooser
			side="guest"
			bind:value={tunnelId}
			bind:tunnels
			error={saidUnder('tunnel')}
		/>
		{#if tunnels !== null && tunnels.length === 0}
			<p class="lands">
				{exchange ? 'An exchange' : 'A swap'} goes through one of your tunnels, and there are none yet.
			</p>
		{/if}
		<Problem message={saidUnder(null)} />
		<div class="finish">
			<Button type="submit" tone="primary" {busy}>{exchange ? 'Join the exchange' : 'Join'}</Button>
		</div>
	</form>
{/snippet}

{#if framed}
	<Panel label={exchange ? 'Join an exchange' : 'Join a swap'}>
		{#if exchange}
			<SectionHeading>Join an exchange</SectionHeading>
			<p class="lands">
				Paste the token they sent you. Once you've compared the code, you choose what to send them.
			</p>
		{/if}
		{@render form()}
	</Panel>
{:else}
	{@render form()}
{/if}

<style>
	.join {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	.named {
		font: var(--text-label);
		color: var(--sift-ink-2);
	}

	/* A field's error, as `Field` draws one. */
	.error {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-bad-text);
	}

	.lands {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.finish {
		display: flex;
		justify-content: flex-end;
	}
</style>
