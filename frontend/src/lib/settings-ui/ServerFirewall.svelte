<script lang="ts">
	/*
	 * Windows Firewall on the computer running Sift, seen from another computer.
	 *
	 * The same question the Network sharing section asks on the computer running Sift (does Windows
	 * let other devices through?), asked through the server, which asks the Sift app there. Drawn in
	 * General's group about that computer, and only while it shares the library: the answer changes
	 * nothing otherwise.
	 *
	 * THE ONE PRESS THAT CANNOT FINISH HERE. Opening the port raises Windows' own administrator
	 * prompt, and it appears on the computer running Sift. Nothing reached over a network can
	 * approve it, which is the property rather than a gap, so the sentence under the button says
	 * where to go, and the button waits while somebody walks over.
	 */
	import { onMount } from 'svelte';
	import { Button, Note } from '$lib/components/common';
	import {
		openServerFirewall,
		readServerFirewall,
		type ServerFirewall
	} from '$lib/desktop/server-shell';
	import FactRow from './FactRow.svelte';
	import { COPY } from './General.search';

	let { machine, port }: { machine: string | null; port: number } = $props();

	const WORDS = COPY.server.firewall;

	let firewall = $state<ServerFirewall | null>(null);
	let opening = $state(false);
	let problem = $state<string | null>(null);

	onMount(() => {
		void readServerFirewall().then((answer) => (firewall = answer));
	});

	async function open() {
		opening = true;
		problem = null;
		try {
			firewall = await openServerFirewall('private');
			if (firewall.state !== 'open') problem = WORDS.unchanged;
		} catch {
			problem = WORDS.unchanged;
		} finally {
			opening = false;
		}
	}

	const fact = $derived(
		firewall === null || firewall.state === 'unknown'
			? WORDS.unknown
			: firewall.state === 'open'
				? WORDS.open(port)
				: WORDS.closed
	);
</script>

<FactRow label={WORDS.label} {fact} />
{#if firewall?.state === 'closed'}
	<div class="firewall">
		<Button onclick={() => void open()} disabled={opening}>
			{opening ? WORDS.waiting(machine) : WORDS.action}
		</Button>
		<Note>{WORDS.approveThere}</Note>
		{#if problem !== null}
			<Note>{problem}</Note>
		{/if}
	</div>
{/if}

<style>
	/* The button and the sentence that says where it is approved, kept together, as the Network
	   sharing section keeps its own. */
	.firewall {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: var(--space-2);
		margin-block-start: var(--space-3);
	}
</style>
