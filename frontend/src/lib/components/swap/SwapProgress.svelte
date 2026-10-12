<script lang="ts">
	/*
	 * A swap moving: how many files and how much of them, and how long is left.
	 *
	 * The estimate is the session's own. It divides what is left by the rate this session measured
	 * over its last ten seconds (never a figure borrowed from another transfer or from the tunnel)
	 * and until the first ten seconds are in it says what every estimate says while it measures,
	 * "Not enough to say yet". See `timeLeft`. The server keeps that pace through a window that
	 * moved nothing (the last files checked while nothing is in flight), so once it has said how
	 * long, it keeps saying it to the end.
	 *
	 * The bytes are a size on its way to a total (`sizeOf`): both in the total's unit, to one
	 * decimal, so the figure keeps moving to the last tenth.
	 *
	 * ## What the receiver says
	 *
	 * Both of its figures, received and filed (`receivedWords`): a file is received when its last
	 * piece is in and checks, and filed once the landing has put it in the library, one at a time
	 * after that. The sender's side says what it sent, as it always has.
	 *
	 * ## The Manifest under it
	 *
	 * The same rows the offer drew, each group's state the server's (crossing, landed, sent), the
	 * file crossing now on its group's row, the rate over its streams and the time gone by this
	 * screen's own clock: nothing here asks the server more often than the page already does.
	 *
	 * ## Both ways
	 *
	 * A swap that sends and receives draws each direction under the drawer's own words, Send and
	 * Receive: its files and its size, or what it waits for while it is not moving yet. Under both,
	 * one estimate for the whole (`timeLeftBothWays`): the two move at the same time, so the whole
	 * takes as long as the slower.
	 */
	import { Button, Empty, Fold, Panel, ProgressBar, SectionHeading } from '$lib/components/common';
	import { counted } from '$lib/entity/entity-counts';
	import { elapsedWords, joined, rateWords, refusedWords } from './manifest';
	import SwapManifest from './SwapManifest.svelte';
	import { size, sizeOf } from '$lib/library/facts';
	import { receivedWords, timeLeft, timeLeftBothWays, type SwapDirection } from './swap';
	import type { Session } from './wire';

	interface Props {
		session: Session;
		busy?: boolean;
		onend: () => void;
	}

	let { session, busy = false, onend }: Props = $props();

	/* A clock of this screen's own for the time gone; the session is read at the page's pace. */
	let now = $state(Date.now() / 1000);
	$effect(() => {
		const timer = setInterval(() => (now = Date.now() / 1000), 1000);
		return () => clearInterval(timer);
	});
	const pace = $derived(
		joined(rateWords(session.rate_bps, session.streams), elapsedWords(session.moving_since, now))
	);
	const refused = $derived(session.refused ?? []);
	const refusedCount = $derived(session.refused_count ?? refused.length);
	const refusedSummary = $derived(
		refusedCount === 1
			? '1 file refused on import'
			: `${counted(refusedCount)} files refused on import`
	);

	/* An exchange is a swap that sends and receives, and says so. */
	const noun = $derived(session.two_way ? 'exchange' : 'swap');
	const files = $derived(
		session.role === 'host'
			? `${session.sent_files.toLocaleString()} of ${session.wanted_files.toLocaleString()} files sent`
			: receivedWords(session.received_files, session.sent_files, session.wanted_files)
	);
	const bytes = $derived(
		session.wanted_bytes
			? (sizeOf(session.sent_bytes, session.wanted_bytes) ?? '')
			: (size(session.sent_bytes) ?? '')
	);
	const fraction = $derived(
		session.wanted_bytes ? Math.min(100, (session.sent_bytes / session.wanted_bytes) * 100) : null
	);

	interface Way {
		heading: string;
		verb: 'sent' | 'received';
		bar: string;
		direction: SwapDirection | null | undefined;
		waiting: string;
	}

	/* Each direction from this side, and what it waits for while it is not moving yet: its offer
	   answered by them, or theirs answered here (Take these) and the first file on its way. */
	const ways = $derived<Way[]>([
		{
			heading: 'Send',
			verb: 'sent',
			bar: 'How much of what you send has gone',
			direction: session.sending,
			waiting: 'Waiting for them to choose what to take'
		},
		{
			heading: 'Receive',
			verb: 'received',
			bar: 'How much of what you receive is here',
			direction: session.receiving,
			waiting: session.answered ? 'Starting' : 'Waiting for what they offer'
		}
	]);

	function filesOf(way: Way, one: SwapDirection): string {
		if (way.verb === 'received') {
			return receivedWords(one.received_files, one.files, one.wanted_files);
		}
		return `${one.files.toLocaleString()} of ${one.wanted_files.toLocaleString()} files ${way.verb}`;
	}

	function bytesOf(one: SwapDirection): string {
		return one.wanted_bytes ? (sizeOf(one.bytes, one.wanted_bytes) ?? '') : (size(one.bytes) ?? '');
	}

	function fractionOf(one: SwapDirection): number | null {
		return one.wanted_bytes ? Math.min(100, (one.bytes / one.wanted_bytes) * 100) : null;
	}
</script>

<Panel label="The {noun}">
	{#if session.two_way}
		{#each ways as way (way.heading)}
			<section class="way">
				<SectionHeading>{way.heading}</SectionHeading>
				{#if way.direction?.moving}
					<p class="files">{filesOf(way, way.direction)}</p>
					<ProgressBar value={fractionOf(way.direction)} label={way.bar} />
					<p class="figures"><span>{bytesOf(way.direction)}</span></p>
				{:else}
					<Empty busy scope="block">{way.waiting}</Empty>
				{/if}
			</section>
		{/each}
		<p class="figures whole"><span>{timeLeftBothWays(session)}</span></p>
	{:else}
		<p class="files">{files}</p>
		<ProgressBar value={fraction} label="How much of the swap is done" />
		<p class="figures">
			<span>{bytes}</span>
			<span>{timeLeft(session)}</span>
		</p>
	{/if}
	{#if pace}<p class="figures">{pace}</p>{/if}
	{#if session.manifest && session.manifest.length > 0}
		<SwapManifest
			groups={session.manifest}
			label="What the {noun} carries"
			sessionId={session.id}
			now={session.now ?? null}
		/>
	{/if}
	{#if refusedCount > 0}
		<Fold summary={refusedSummary}>
			<ul class="refused">
				{#each refused as one, at (at)}
					<li><span>{one.title}</span> <span class="why">{refusedWords(one.reason)}</span></li>
				{/each}
			</ul>
			{#if refusedCount > refused.length}
				<p class="why">And {counted(refusedCount - refused.length)} more.</p>
			{/if}
		</Fold>
	{/if}
	<div class="finish">
		<Button tone="quiet" {busy} onclick={onend}>End the {noun}</Button>
	</div>
</Panel>

<style>
	.files,
	.figures {
		margin: 0;
	}

	.figures {
		display: flex;
		justify-content: space-between;
		gap: var(--space-3);
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
		font-variant-numeric: tabular-nums;
	}

	.finish {
		display: flex;
		justify-content: flex-end;
	}

	.refused {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.why {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	/* A direction: its heading, its files, its bar and its size, as one group. */
	.way {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	/* The one estimate for both ways, at the right as a swap one way has it. */
	.whole {
		justify-content: flex-end;
	}
</style>
