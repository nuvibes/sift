<script lang="ts">
	/* NOT ON THE GALLERY: it asks the server to weigh real picks. The gallery draws the steps a session hands over instead (`a-swap-step-by-step`). */
	/*
	 * What the picks add up to above the press that sends them, weighed by the server
	 * (`weighPicks`) 300 ms after the last change. Where the weigh groups the picks, they are the
	 * Manifest, with the time at the tunnel's last measured rate and what the other side learns;
	 * otherwise what is left out and why (`leftOutWords`). A stale or refused answer shows nothing.
	 */
	import { kindsWords, LEARNS_SENTENCE, learnsCounts, joined, throughWords } from './manifest';
	import SwapManifest from './SwapManifest.svelte';
	import { leftOutWords, sendingWords, weighPicks, type Chosen } from './swap';
	import type { Weight } from './wire';

	interface Props {
		chosen: Chosen[];
		both?: boolean;
		weight?: Weight | null;
		/** The tunnel chosen, and the rate it last measured, for how long the files take. */
		tunnel?: { name: string; rate_bps?: number | null } | null;
	}

	let { chosen, both = false, weight = $bindable(null), tunnel = null }: Props = $props();

	const WEIGH_AFTER_MS = 300;

	let asked = 0;

	$effect(() => {
		const picks = chosen;
		const mine = ++asked;
		if (picks.length === 0) {
			weight = null;
			return;
		}
		const timer = setTimeout(() => {
			weighPicks(picks).then(
				(answer) => {
					if (mine === asked) weight = answer;
				},
				() => {
					if (mine === asked) weight = null;
				}
			);
		}, WEIGH_AFTER_MS);
		return () => clearTimeout(timer);
	});

	const shown = $derived(weight && chosen.length > 0 ? weight : null);
	const groups = $derived(shown?.groups && shown.groups.length > 0 ? shown.groups : null);
	const sending = $derived(shown ? sendingWords(shown, chosen, both) : '');
	const kinds = $derived(
		groups && shown
			? joined(
					kindsWords(groups),
					throughWords(shown.bytes, tunnel?.rate_bps, tunnel?.name ?? null)
				)
			: ''
	);
	/* With the Manifest, a kept-out row says its own reason; only the files under nothing listed are
	   said here. */
	const leftOut = $derived(
		shown ? leftOutWords(groups ? [] : shown.left_out, shown.left_out_other) : []
	);
	const counts = $derived(groups ? learnsCounts(shown?.learns) : null);
</script>

{#if sending}
	<p class="weight">{sending}</p>
{/if}
{#if groups}
	{#if kinds}<p class="left-out">{kinds}</p>{/if}
	<SwapManifest {groups} label="What you are offering" />
{/if}
{#each leftOut as line (line)}
	<p class="left-out">{line}</p>
{/each}
{#if groups}
	<p class="weight">{LEARNS_SENTENCE}</p>
	{#if counts}<p class="left-out">{counts}</p>{/if}
{/if}

<style>
	.weight,
	.left-out {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
		font-variant-numeric: tabular-nums;
	}

	.left-out {
		color: var(--sift-ink-3);
	}
</style>
