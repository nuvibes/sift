<script lang="ts">
	/*
	 * The code both screens show once the two devices have said hello, and the two answers to it.
	 *
	 * Six characters, drawn large, because they are read aloud or typed into a chat and compared by
	 * eye. The code is worked out on each side from the connection itself, so two screens showing
	 * the same six characters are two ends of one connection, and nobody is in the middle of it.
	 * Nothing about either library is shown until the person here says they match.
	 */
	import { Button, Panel } from '$lib/components/common';

	interface Props {
		code: string;
		/** The other device's id, when the hello has named it. */
		device?: string | null;
		busy?: boolean;
		onanswer: (match: boolean) => void;
	}

	let { code, device = null, busy = false, onanswer }: Props = $props();
</script>

<Panel label="The code">
	<p class="lede">Compare this code with them before you go on.</p>
	<p class="code" aria-label="The code, {code.split('').join(' ')}">{code}</p>
	{#if device}
		<p class="device">Their device id is {device}.</p>
	{/if}
	<div class="answers">
		<Button tone="quiet" disabled={busy} onclick={() => onanswer(false)}>They don't match</Button>
		<Button tone="primary" disabled={busy} onclick={() => onanswer(true)}>They match</Button>
	</div>
</Panel>

<style>
	.lede,
	.device {
		margin: 0;
	}

	/* The one large thing on the screen. Spaced so each character is read on its own. */
	.code {
		margin: var(--space-2) 0;
		font: var(--text-display-lg);
		letter-spacing: 0.3em;
		font-variant-numeric: tabular-nums;
	}

	.device {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	/* Buttons on the right, the one that goes on last. */
	.answers {
		display: flex;
		justify-content: flex-end;
		gap: var(--space-2);
	}
</style>
