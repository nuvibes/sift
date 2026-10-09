<script lang="ts">
	/* NOT ON THE GALLERY: it asks the server to weigh real picks. The gallery draws the steps a session hands over instead (`a-swap-step-by-step`). */
	/*
	 * What the picks add up to above the press that sends them, weighed by the server
	 * (`weighPicks`) 300 ms after the last change, with what is left out and why (`leftOutWords`).
	 * A stale or refused answer shows no figure.
	 */
	import { leftOutWords, sendingWords, weighPicks, type Chosen, type SwapWeight } from './swap';

	interface Props {
		chosen: Chosen[];
		both?: boolean;
		weight?: SwapWeight | null;
	}

	let { chosen, both = false, weight = $bindable(null) }: Props = $props();

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

	const sending = $derived(weight && chosen.length > 0 ? sendingWords(weight, chosen, both) : '');
	const leftOut = $derived(
		weight && chosen.length > 0 ? leftOutWords(weight.left_out, weight.left_out_other) : []
	);
</script>

{#if sending}
	<p class="weight">{sending}</p>
{/if}
{#each leftOut as line (line)}
	<p class="left-out">{line}</p>
{/each}

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
