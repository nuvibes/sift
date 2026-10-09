<script lang="ts">
	/* Windows Firewall on the computer running Sift, seen from another computer. */
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
