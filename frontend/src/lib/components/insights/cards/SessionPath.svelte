<script lang="ts">
	/*
	 * HOW ONE SESSION WENT: the pages of the longest session, top to bottom in the order they were
	 * opened, each a node on one line and a way back to that page, with how far into the session it
	 * was. The pages between the first few and the last are counted, not drawn. The server names
	 * each page for the reader now and leaves out anything hidden (`session_router.py`).
	 */
	import { HistorySentence } from '$lib/components/common';
	import { figureWords } from '$lib/components/insights/figures';
	import type { SessionPath } from '$lib/components/insights/cards/kinds';

	interface Props {
		path: SessionPath;
	}

	let { path }: Props = $props();

	/* The gap stands after the first pages: the server keeps the last two. */
	const gapAt = $derived(path.more > 0 ? path.steps.length - 2 : -1);
</script>

<div class="session">
	<p class="title">How one session went</p>
	<div class="track">
		<span class="rail"></span>
		<ol class="path">
			{#each path.steps as step, index (index)}
				{#if index === gapAt}
					<li class="gap">
						{`${figureWords(path.more, 'count')} ${path.more === 1 ? 'more page' : 'more pages'}`}
					</li>
				{/if}
				<li class="step">
					<span class="node"></span>
					<span class="name"><HistorySentence pieces={[step.piece]} /></span>
					<span class="after">{index === 0 ? 'Start' : `+${figureWords(step.after_ms, 'ms')}`}</span
					>
				</li>
			{/each}
		</ol>
	</div>
</div>

<style>
	.session {
		display: flex;
		flex: 1;
		flex-direction: column;
		gap: var(--space-3);
		min-block-size: 0;
	}

	.title {
		margin: 0;
		font: var(--text-micro);
		letter-spacing: var(--tracking-micro);
		text-transform: uppercase;
		color: var(--sift-ink-2);
	}

	/* One line down the left, a node on it at each page: elements with a ground, so a saved
	   picture paints them as the screen does. */
	.track {
		position: relative;
		display: grid;
	}

	.rail {
		position: absolute;
		inset-block: var(--space-2);
		inset-inline-start: calc(var(--space-1) - var(--chart-mark-gap) / 2);
		inline-size: var(--chart-mark-gap);
		background: var(--sift-accent-tint-2);
	}

	.path {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.gap {
		padding-inline-start: var(--space-4);
	}

	.step,
	.gap {
		position: relative;
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: var(--space-2);
		min-inline-size: 0;
	}

	.node {
		flex: none;
		align-self: center;
		inline-size: var(--space-2);
		aspect-ratio: 1;
		border-radius: 50%;
		background: var(--sift-accent-tint-1);
	}

	.name {
		flex: 1;
		overflow: hidden;
		font: var(--text-body-sm);
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.after,
	.gap {
		font: var(--text-label);
		color: var(--sift-ink-2);
		font-variant-numeric: tabular-nums;
	}

	.after {
		flex: none;
	}
</style>
