<script lang="ts">
	/* NOT ON THE GALLERY: its Start restarts a real tunnel with a listener and hands out a real token. */
	/*
	 * How a swap goes out, and the press that starts it: whether stash-box ids travel, which tunnel
	 * it goes through, and Start.
	 *
	 * One component for both ways in, the Start a swap screen and swap mode's drawer, because what
	 * a swap is sent THROUGH is the same question whichever way its contents were chosen. Every
	 * tunnel is offered, each saying whether it can host: whether one can is only learned by
	 * starting a swap on it, so a tunnel nobody has tried is a real choice.
	 *
	 * Above Start, what the picks add up to (`SendingWeight`): the figure grows as things are picked
	 * in swap mode and stands on the Start a swap screen before the swap starts.
	 *
	 * ## Exchange
	 *
	 * With `both`, the swap goes both ways, an exchange: they offer too once they have joined, and what this side
	 * takes from them lands in a folder chosen here, from the same list Join a swap offers, with the
	 * sentence that says where in it. Start says what is missing when no folder is chosen.
	 */
	import { ApiError } from '$lib/api/client';
	import { Button, Checkbox, Problem, Select } from '$lib/components/common';
	import { Destinations } from '$lib/library/destinations.svelte';
	import { openSettings } from '$lib/settings-ui/settings-view';
	import SendingWeight from './SendingWeight.svelte';
	import SwapTunnelChooser from './SwapTunnelChooser.svelte';
	import {
		landingWords,
		startBlocked,
		startSwap,
		type Chosen,
		type SwapStarted,
		type SwapTunnel,
		type SwapWeight
	} from './swap';

	interface Props {
		/** What the swap is built from. Start says what is missing when this is empty. */
		chosen: Chosen[];
		onstarted: (started: SwapStarted) => void;
		/** Exchange: they offer too, and what is taken from them lands in a folder chosen here. */
		both?: boolean;
	}

	let { chosen, onstarted, both = false }: Props = $props();
	/* What the server weighs the picks at, and whether that leaves anything to start. The reason is
	   the figure's own line just above Start (`sendingWords`), so it is said once. */
	let weight = $state<SwapWeight | null>(null);
	const blocked = $derived(chosen.length > 0 ? startBlocked(weight, chosen, both) : null);
	/* What this form starts, in its words: under Exchange, every sentence says exchange. */
	const noun = $derived(both ? 'exchange' : 'swap');

	/* The folders received files can go in: read only for Exchange, so a swap that only sends asks
	   nothing it does not need. */
	const destinations = new Destinations();
	let folderId = $state('');
	let folderProblem = $state<string | undefined>(undefined);
	$effect(() => {
		if (both) void destinations.load();
	});
	const folderName = $derived(
		destinations.placed.find((one) => one.value === folderId)?.label ?? null
	);

	let shareBoxes = $state(true);
	let tunnels = $state<SwapTunnel[] | null>(null);
	let tunnelId = $state('');
	let problem = $state<string | null>(null);
	let busy = $state(false);

	/* The tunnels are the chooser's to read (`SwapTunnelChooser`), again whenever a setting moves;
	   they are bound here only to say when there are none. */

	async function start(): Promise<void> {
		if (chosen.length === 0) {
			problem = 'Choose what to offer first.';
			return;
		}
		if (both && !folderId) {
			folderProblem = 'Choose a folder to put received files in.';
			return;
		}
		if (!tunnelId) {
			problem = `Choose a tunnel for the ${noun} to go through.`;
			return;
		}
		busy = true;
		problem = null;
		folderProblem = undefined;
		try {
			onstarted(await startSwap(chosen, tunnelId, shareBoxes, both ? folderId : null));
		} catch (error) {
			const words =
				error instanceof ApiError ? (error.detail ?? error.message) : `The ${noun} couldn't start.`;
			if (error instanceof ApiError && error.field === 'dest_folder_id') folderProblem = words;
			else problem = words;
		} finally {
			busy = false;
		}
	}
</script>

<!-- One block with its own spacing, so the tick, the tunnel and Start stand apart wherever this is
     drawn: in swap mode's drawer a group is a column with no gap, and the three touched. -->
<div class="launch">
	{#if both}
		<label class="named" for="swap-receive-folder">Put received files in</label>
		<Select
			id="swap-receive-folder"
			bind:value={folderId}
			options={destinations.placed}
			placeholder="Choose a folder"
			invalid={folderProblem !== undefined}
			describedBy={folderProblem ? 'swap-receive-folder-error' : undefined}
		/>
		{#if folderProblem}
			<p class="error" id="swap-receive-folder-error" role="alert">{folderProblem}</p>
		{/if}
		{#if folderName}
			<p class="note">{landingWords(folderName, undefined, 'exchange')}</p>
		{/if}
	{/if}
	<span class="ticked">
		<Checkbox
			state={shareBoxes ? 'on' : 'off'}
			label="Share stash-box ids"
			onchange={(next) => (shareBoxes = next === 'on')}
		/>
		<span>Share stash-box ids</span>
	</span>

	{#if tunnels !== null && tunnels.length === 0}
		<p class="note">
			{both ? 'An exchange' : 'A swap'} goes through one of your tunnels, and there are none yet.
		</p>
		<div class="finish">
			<Button size="small" onclick={() => openSettings('sites', 'sites.tunnels')}
				>Open Sites and Tunnels settings</Button
			>
		</div>
	{/if}
	<div class:gone={tunnels !== null && tunnels.length === 0}>
		<SwapTunnelChooser
			side="host"
			bind:value={tunnelId}
			bind:tunnels
			onproblem={(words) => (problem = words)}
		/>
	</div>

	<SendingWeight {chosen} {both} bind:weight />

	<Problem message={problem} />

	<div class="finish">
		<Button tone="primary" {busy} disabled={blocked !== null} onclick={() => void start()}
			>Start</Button
		>
	</div>
</div>

<style>
	.launch {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	.ticked {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	.note {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
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

	.finish {
		display: flex;
		justify-content: flex-end;
	}

	/* No tunnels: the sentence above says so, and a chooser with nothing in it would say it twice. */
	.gone {
		display: none;
	}
</style>
