<script lang="ts">
	/* NOT ON THE GALLERY: it asks the server to weigh real picks. The gallery draws the steps a session hands over instead (`a-swap-step-by-step`). */
	/*
	 * What the picks add up to, said above the press that sends them: "You would send N files, X GB."
	 *
	 * Asked of the server each time the picks change (`weighPicks`), 300 ms after the last change,
	 * so the figure grows as things are picked and stands before anything is sent. It is the
	 * server's own read of the picks, the one the offer makes, so a file in Hidden adds nothing. An
	 * answer to picks that have changed since is dropped, and a refusal leaves no figure rather than
	 * a wrong one. One component for every place a swap is sent from: Start, swap mode's drawer, and
	 * the guest's own offer in an exchange.
	 *
	 * Under it, what the picks leave out and why, from the same answer (`leftOutWords`): each pick
	 * that wears a mark by its name ("Ava Example is kept local: 1,200 files aren't offered"), and
	 * the files a mark on something else keeps back. Never a file in Hidden: the server's read is
	 * made with the vault shut.
	 */
	import { leftOutWords, sendingWords, weighPicks, type Chosen, type SwapWeight } from './swap';

	interface Props {
		chosen: Chosen[];
		/** An exchange: picks that offer no file still receive, and the figure says so. */
		both?: boolean;
		/** The server's answer, bound out for Start to judge by (`startBlocked`). */
		weight?: SwapWeight | null;
	}

	let { chosen, both = false, weight = $bindable(null) }: Props = $props();

	/** How long the picks rest before they are weighed: a run of presses is one question. */
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

	/* What will not go, under what will: the quieter ink, since it explains the figure above. */
	.left-out {
		color: var(--sift-ink-3);
	}
</style>
