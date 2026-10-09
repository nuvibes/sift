<script lang="ts">
	/* Swap with another Sift: three doors, Start a swap, Join a swap and Exchange, and then the
	 * one session. */
	import { onDestroy } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { ApiError, isMissing } from '$lib/api/client';
	import { Button, Empty, Panel, Problem } from '$lib/components/common';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import { jobChanges, whenChanged } from '$lib/library/changes.svelte';
	import JoinSwap from '$lib/components/swap/JoinSwap.svelte';
	import OfferScreen from '$lib/components/swap/OfferScreen.svelte';
	import StartSwap from '$lib/components/swap/StartSwap.svelte';
	import SwapCode from '$lib/components/swap/SwapCode.svelte';
	import SwapProgress from '$lib/components/swap/SwapProgress.svelte';
	import SwapToken from '$lib/components/swap/SwapToken.svelte';
	import { takeExchangePicks, type ExchangePick } from '$lib/swap/exchange';
	import {
		answerCode,
		cutOffWords,
		endSwap,
		endedWords,
		isLive,
		landingWords,
		readSession,
		stageOf,
		takeOffer,
		type Chosen,
		type SwapSession
	} from '$lib/components/swap/swap';

	/** How often a running session is read between bells. */
	const POLL_MS = 2000;

	const sessionId = $derived(page.url.searchParams.get('session'));
	const door = $derived(page.url.searchParams.get('door'));
	/* Joined from the Exchange door: what the person meant, which the server cannot know. */
	const meantExchange = $derived(page.url.searchParams.get('exchange') === '1');

	let session = $state<SwapSession | null>(null);
	let problem = $state<string | null>(null);
	let compared = $state(false);
	let took = $state(false);
	let busy = $state(false);
	/* What the guest of a swap that sends and receives sends them, chosen beside the code. */
	let offering = $state<Chosen[]>([]);
	let offeringBoxes = $state(true);
	const picksHere = $derived(session?.two_way === true && session.role === 'guest');
	/* What the guest picked in swap mode before it pasted the token, waiting for this session. */
	let pickedBefore = $state<ExchangePick[]>([]);
	/* A swap that goes both ways is an exchange, and its screens say so. */
	const noun = $derived(session?.two_way ? 'exchange' : 'swap');

	const stage = $derived(session ? stageOf(session, compared, took) : null);

	function sayError(error: unknown, otherwise: string): string {
		return error instanceof ApiError ? (error.detail ?? error.message) : otherwise;
	}

	async function refresh(): Promise<void> {
		const id = sessionId;
		if (!id) return;
		try {
			const read = await readSession(id);
			if (id === sessionId) session = read;
		} catch (error) {
			if (isMissing(error)) {
				session = null;
				problem = "There's no such swap. It may have ended when Sift last restarted.";
			}
			// Anything else is the server briefly out of reach: the next poll or bell asks again.
		}
	}

	/* A new session in the address starts from nothing: its own code, its own answer. */
	$effect(() => {
		const id = sessionId;
		session = null;
		compared = false;
		took = false;
		problem = null;
		pickedBefore = id ? takeExchangePicks(id) : [];
		if (id) void refresh();
	});

	whenChanged(jobChanges, () => void refresh());

	let timer: ReturnType<typeof setInterval> | undefined;
	$effect(() => {
		const running = isLive(session);
		if (running && timer === undefined) timer = setInterval(() => void refresh(), POLL_MS);
		if (!running && timer !== undefined) {
			clearInterval(timer);
			timer = undefined;
		}
	});
	onDestroy(() => {
		if (timer !== undefined) clearInterval(timer);
	});

	function open(params: Record<string, string>): void {
		void goto(`/swap?${new URLSearchParams(params)}`);
	}

	async function answer(match: boolean): Promise<void> {
		if (!session) return;
		const sends = match && picksHere;
		if (sends && offering.length === 0) {
			problem = 'Choose what to send them first.';
			return;
		}
		busy = true;
		problem = null;
		try {
			session = await answerCode(
				session.id,
				match,
				sends ? { chosen: offering, shareBoxes: offeringBoxes } : undefined
			);
			compared = match;
		} catch (error) {
			problem = sayError(error, "That answer couldn't be sent.");
		} finally {
			busy = false;
		}
	}

	async function take(skipped: number[]): Promise<void> {
		if (!session) return;
		busy = true;
		try {
			session = await takeOffer(session.id, skipped);
			took = true;
		} catch (error) {
			problem = sayError(error, "That answer couldn't be sent.");
		} finally {
			busy = false;
		}
	}

	async function end(): Promise<void> {
		if (!session) return;
		busy = true;
		try {
			session = await endSwap(session.id);
		} catch (error) {
			problem = sayError(error, "The swap couldn't be ended.");
		} finally {
			busy = false;
		}
	}
</script>

{#snippet waiting(words: string)}
	<Panel label="The {noun}">
		<Empty busy scope="block">{words}</Empty>
		<div class="finish">
			<Button tone="quiet" {busy} onclick={() => void end()}>End the {noun}</Button>
		</div>
	</Panel>
{/snippet}

<PageFrame>
	{#snippet header()}
		<PageHeader title="Swap with another Sift" icon="swap_horiz" />
	{/snippet}

	<div class="swap">
		<Problem message={problem} />

		{#if sessionId}
			{#if session && stage}
				{#if stage === 'token' && session.token}
					<SwapToken token={session.token} sentence={session.sentence ?? ''} />
					<div class="finish">
						<Button tone="quiet" {busy} onclick={() => void end()}>End the {noun}</Button>
					</div>
				{:else if stage === 'connecting'}
					{@render waiting(session.role === 'host' ? 'Connecting to them' : 'Reaching them')}
				{:else if stage === 'code' && session.code}
					{#if picksHere}
						<StartSwap
							pickOnly
							initial={pickedBefore}
							bind:chosen={offering}
							bind:shareBoxes={offeringBoxes}
						/>
					{:else if meantExchange && session.role === 'guest'}
						<!-- Pasted under Exchange, and their token is a swap that only sends. -->
						<p class="lands">
							This swap only sends: you receive their files and send nothing back.
						</p>
					{/if}
					<SwapCode code={session.code} device={session.peer_device} {busy} onanswer={answer} />
				{:else if stage === 'waiting-for-them'}
					{@render waiting(
						session.role === 'host'
							? 'Waiting for them to choose what to take'
							: 'Waiting for what they offer'
					)}
				{:else if stage === 'offer' && session.offer}
					<!-- Where what is taken will land, now there is a session to name the folder by. -->
					<p class="lands">{landingWords('the folder you chose', session.short_id)}</p>
					<OfferScreen screen={session.offer} sessionId={session.id} {busy} ontake={take} />
					<div class="finish">
						<Button tone="quiet" {busy} onclick={() => void end()}>End the {noun}</Button>
					</div>
				{:else if stage === 'starting'}
					{@render waiting('Starting')}
				{:else if stage === 'progress'}
					<SwapProgress {session} {busy} onend={() => void end()} />
				{:else if stage === 'cut-off'}
					<!-- Not ended: a tunnel dropped under it, and the same token joins it again. The
					     host shows the token again, since a port the provider moved remakes it. -->
					<Panel label="The {noun}">
						<p class="ended">{cutOffWords(session)}</p>
						<div class="finish">
							<Button tone="quiet" {busy} onclick={() => void end()}>End the {noun}</Button>
						</div>
					</Panel>
					{#if session.role === 'host' && session.token}
						<SwapToken token={session.token} sentence={session.sentence ?? ''} />
					{/if}
				{:else if stage === 'ended'}
					<Panel label="The {noun} has ended">
						<p class="ended">{endedWords(session)}</p>
						<div class="finish">
							<Button onclick={() => void goto('/swap')}>Start or join another swap</Button>
						</div>
					</Panel>
				{/if}
			{:else if !problem}
				<Empty busy scope="block">Reading the swap</Empty>
			{/if}
		{:else if door === 'start'}
			<StartSwap onstarted={(started) => open({ session: started.session_id })} />
		{:else if door === 'join'}
			<JoinSwap onjoined={(id) => open({ session: id })} />
		{:else if door === 'both'}
			<StartSwap both onstarted={(started) => open({ session: started.session_id })} />
		{:else if door === 'both-join'}
			<JoinSwap exchange onjoined={(id) => open({ session: id, exchange: '1' })} />
		{:else}
			<div class="doors">
				<Panel label="Start a swap">
					<p class="door">
						Choose People, Sites, tags, Collections or Photo Sets to offer, and send the token Sift
						gives you.
					</p>
					<div class="finish">
						<Button tone="primary" onclick={() => open({ door: 'start' })}>Start a swap</Button>
					</div>
				</Panel>
				<Panel label="Join a swap">
					<p class="door">Paste the token they sent you, and choose where their files go.</p>
					<div class="finish">
						<Button onclick={() => open({ door: 'join' })}>Join a swap</Button>
					</div>
				</Panel>
				<Panel label="Exchange">
					<p class="door">
						You each send files to the other. One of you starts it and sends the token Sift gives
						you, and the other joins with that token.
					</p>
					<div class="finish both">
						<Button onclick={() => open({ door: 'both-join' })}>Join an exchange</Button>
						<Button onclick={() => open({ door: 'both' })}>Start an exchange</Button>
					</div>
				</Panel>
			</div>
		{/if}
	</div>
</PageFrame>

<style>
	.swap {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
	}

	.doors {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 18rem), 1fr));
		gap: var(--space-4);
	}

	.door,
	.ended {
		margin: 0;
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

	/* Exchange's two presses, side by side at the right, wrapping on a narrow door. */
	.finish.both {
		flex-wrap: wrap;
		gap: var(--space-2);
	}

	/* The doors stand side by side at one height, and each one's press sits at its foot, so
	   three sentences of different lengths do not leave three buttons at three heights. */
	.doors .finish {
		align-self: end;
	}
</style>
