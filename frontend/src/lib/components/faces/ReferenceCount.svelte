<script lang="ts">
	/*
	 * How many reference faces one person has, said beside their name at the moment it matters.
	 *
	 * Attaching a face to somebody is where recognition is actually built: a confirmed face becomes
	 * one of that person's references, and matching a new face compares it against every reference
	 * they have. So four pictures and fifty behave very differently, and in a list of names they
	 * read identically. Somebody choosing between two people here has no way to tell that one of
	 * them will be found by herself in the next file and the other will not.
	 *
	 * A count and a word, not a meter. The person's own page draws the meter; this is annotation on
	 * a control somebody is in the middle of using, and it has to be readable without being looked
	 * at. Absent entirely for people with no references yet, because "0" beside a name that has
	 * never been used reports a fault where there is none: naming their first face is exactly
	 * what this screen is for.
	 */
	import { referenceVerdict, type ReferenceStrengths } from '$lib/people/faces.svelte';

	interface Props {
		personId: string;
		strengths: ReferenceStrengths | null;
	}

	let { personId, strengths }: Props = $props();

	const held = $derived(strengths?.people[personId] ?? 0);
	const verdict = $derived(referenceVerdict(personId, strengths));
</script>

{#if held > 0}
	<span class="count" class:weak={verdict === 'weak'}>
		{held}
		{held === 1 ? 'photo' : 'photos'}{verdict === 'weak' ? ' - weak' : ''}
	</span>
{/if}

<style>
	.count {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
		white-space: nowrap;
	}

	/* Warm rather than red. Nothing is broken: the person simply has few pictures of them, and
	   red in this app means a failure. */
	.weak {
		color: var(--sift-warn);
	}
</style>
