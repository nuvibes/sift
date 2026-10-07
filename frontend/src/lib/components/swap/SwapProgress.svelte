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
	 * ## Both ways
	 *
	 * A swap that sends and receives draws each direction under the drawer's own words, Send and
	 * Receive: its files and its size, or what it waits for while it is not moving yet. Under both,
	 * one estimate for the whole (`timeLeftBothWays`): the two move at the same time, so the whole
	 * takes as long as the slower.
	 */
	import { Button, Empty, Panel, ProgressBar, SectionHeading } from '$lib/components/common';
	import { size, sizeOf } from '$lib/library/facts';
	import {
		receivedWords,
		timeLeft,
		timeLeftBothWays,
		type SwapDirection,
		type SwapSession
	} from './swap';

	interface Props {
		session: SwapSession;
		busy?: boolean;
		onend: () => void;
	}

	let { session, busy = false, onend }: Props = $props();

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
			bar: 'How much of what you receive has arrived',
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
		<ProgressBar value={fraction} label="How much of the swap has arrived" />
		<p class="figures">
			<span>{bytes}</span>
			<span>{timeLeft(session)}</span>
		</p>
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
