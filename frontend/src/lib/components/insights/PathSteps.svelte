<script lang="ts">
	/*
	 * One learning path's goals, as a list: each one a way to where it is done.
	 *
	 * A goal reached is ticked and carries the DAY the record says it was first done (or the day a
	 * milestone was reached), so a goal reached before this screen existed reads as done on its
	 * first visit, with its real date. A goal not reached carries its one sentence of help instead,
	 * in the server's words and drawn from its pieces: nothing is composed here.
	 */
	import HistorySentence from '$lib/components/common/HistorySentence.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { followSettingsLink } from '$lib/settings-ui/settings-link';
	import { dayOf } from '$lib/shell/when';
	import type { PathGoal } from './your-path';

	interface Props {
		steps: readonly PathGoal[];
	}

	let { steps }: Props = $props();
</script>

<ul class="steps">
	{#each steps as step (step.id)}
		<li class:done={step.done}>
			<span class="mark">
				<Icon name={step.done ? 'check_circle' : 'chevron_right'} size={20} />
			</span>
			<div class="what">
				<!-- A step whose place is in Settings opens the panel there: this list is drawn inside the panel
				     on Get to know Sift, where a page load to another settings address would leave it empty. -->
				<a href={step.href} onclick={(event) => followSettingsLink(event, step.href)}
					>{step.title}</a
				>
				{#if !step.done}
					<p class="help"><HistorySentence pieces={step.help} /></p>
				{/if}
			</div>
			{#if step.done && step.done_at !== null}
				<span class="when">{dayOf(step.done_at)}</span>
			{/if}
		</li>
	{/each}
</ul>

<style>
	.steps {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	/* The mark, the words, and the day at the far end: actions and dates right, content left. */
	li {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto;
		align-items: baseline;
		gap: var(--space-2);
	}

	.mark {
		align-self: start;
		color: var(--sift-ink-3);
	}

	.done .mark {
		color: var(--sift-ok);
	}

	.what {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		min-inline-size: 0;
	}

	a {
		font: var(--text-body);
		color: var(--sift-ink);
	}

	.help {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.when {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
		white-space: nowrap;
	}

	/* A list of places on a phone is rows a finger's height apart: each step's link reaches the
	   touch target by its own padding, and the rows give up the gap that padding now holds. */
	@media (max-width: 767px) {
		.steps {
			gap: 0;
		}

		a {
			display: inline-block;
			padding-block: calc((var(--touch-target) - 1lh) / 2);
		}
	}
</style>
