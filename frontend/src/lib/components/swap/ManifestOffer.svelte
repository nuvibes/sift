<script lang="ts">
	/* NOT ON THE GALLERY: it reads the tunnels and weighs each answer on the server. The gallery draws its list, the Manifest with ticks (`a-swap-step-by-step`). */
	/*
	 * What the other device offered, as the Manifest, and the guest's answer to it: a tick per group
	 * and per file, the after number on each row ("203 -> 612"), the files already here held back
	 * with their two reasons apart, and what the ticks as they stand would bring, weighed by the
	 * server after each change (`weighAnswer`), since which files a skip drops is the server's rule.
	 *
	 * A person skipped goes as its index (`skipped`), any other group by its key (`skipped_groups`),
	 * and a single file by its key (`unticked`).
	 */
	import { onMount } from 'svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import { Button, Panel } from '$lib/components/common';
	import { counted } from '$lib/entity/entity-counts';
	import { deviceWords, filesAndBytes, heldWords, joined, throughWords } from './manifest';
	import SwapManifest from './SwapManifest.svelte';
	import { swapTunnels, weighAnswer } from './swap';
	import type { ManifestGroup, Session, Tunnel } from './wire';

	interface Props {
		session: Session;
		busy?: boolean;
		ontake: (skipped: number[], unticked: string[], skippedGroups: string[]) => void;
	}

	let { session, busy = false, ontake }: Props = $props();

	const groups = $derived<ManifestGroup[]>(session.manifest ?? []);
	const choose = { skipped: new SvelteSet<string>(), unticked: new SvelteSet<string>() };

	/* The answer, split as the take route reads it. */
	function answer(): { skipped: number[]; unticked: string[]; groups: string[] } {
		const people: number[] = [];
		const others: string[] = [];
		for (const key of choose.skipped) {
			const group = groups.find((one) => one.key === key);
			if (group?.kind === 'person' && group.index !== null) people.push(group.index);
			else others.push(key);
		}
		return {
			skipped: people.sort((a, b) => a - b),
			unticked: [...choose.unticked].sort(),
			groups: others.sort()
		};
	}

	/* What everything not already here comes to, until a tick changes; then the server's weighing. */
	const lacking = $derived({
		files: session.offer?.files ?? groups.reduce((sum, one) => sum + one.to_take, 0),
		bytes: session.offer?.bytes ?? 0
	});
	let weighed = $state<{ files: number; bytes: number } | null>(null);
	let asked = 0;
	$effect(() => {
		const said = answer();
		const mine = ++asked;
		if (said.skipped.length + said.unticked.length + said.groups.length === 0) {
			weighed = null;
			return;
		}
		weighAnswer(session.id, said.skipped, said.unticked, said.groups).then(
			(weight) => {
				if (mine === asked) weighed = weight;
			},
			() => {
				if (mine === asked) weighed = null;
			}
		);
	});
	const bringing = $derived(weighed ?? lacking);

	/* The rate the guest's tunnel last measured, for how long the ticked files take. */
	let tunnels = $state<Tunnel[]>([]);
	onMount(() => {
		swapTunnels().then(
			(read) => (tunnels = read),
			() => undefined
		);
	});
	const rate = $derived(tunnels.find((one) => one.name === session.tunnel_name)?.rate_bps ?? null);

	const from = $derived(deviceWords(session.peer_name, session.peer_device));
	const offered = $derived(
		filesAndBytes(session.offer?.offered_files ?? 0, session.offer?.offered_bytes ?? 0)
	);
	const lede = $derived(
		joined(
			from ? `From ${from}` : null,
			`${offered} offered`,
			`you lack ${counted(lacking.files)}`,
			throughWords(lacking.bytes, rate, null)
		)
	);
	const held = $derived(
		heldWords(
			groups.reduce((sum, one) => sum + one.held_same, 0),
			groups.reduce((sum, one) => sum + one.held_look, 0)
		)
	);

	function take(): void {
		const said = answer();
		ontake(said.skipped, said.unticked, said.groups);
	}

	/* Every group skipped, sent as the answer: nothing comes. */
	function skipAll(): void {
		for (const group of groups) if (!group.left_out) choose.skipped.add(group.key);
		take();
	}
</script>

<Panel label={from ? `What they offered, from ${from}` : 'What they offered'}>
	<p class="lede">{lede}</p>
	<SwapManifest {groups} label="What they offered" sessionId={session.id} {choose} />
	{#if held}<p class="note">{held}</p>{/if}
	<p class="bringing">
		{bringing.files > 0
			? `You would receive ${joined(filesAndBytes(bringing.files, bringing.bytes), throughWords(bringing.bytes, rate, null))}.`
			: 'You would receive nothing.'}
	</p>
	<div class="finish">
		<Button tone="quiet" disabled={busy} onclick={skipAll}>Skip all</Button>
		<Button tone="primary" {busy} disabled={bringing.files === 0} onclick={take}
			>{bringing.files === 0
				? 'Take these'
				: bringing.files === 1
					? 'Take 1 file'
					: `Take ${counted(bringing.files)} files`}</Button
		>
	</div>
</Panel>

<style>
	.lede,
	.note,
	.bringing {
		margin: 0;
	}

	.lede,
	.bringing {
		font-variant-numeric: tabular-nums;
	}

	.note {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.finish {
		display: flex;
		justify-content: flex-end;
		gap: var(--space-2);
	}
</style>
