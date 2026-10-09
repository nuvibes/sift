<script lang="ts">
	/*
	 * HOW ONE VISIT WENT: the pages of the longest one, top to bottom in the order they were
	 * opened, each on one line with its picture as the node (a person's face, a file's picture, a
	 * Site's mark; a dot for a page that is a place, as Browse), its name a way back to it, and how
	 * far into the visit it was. The pages between the first few and the last are counted, not
	 * drawn. The server names each page for the reader now and leaves out anything hidden
	 * (`session_router.py`).
	 */
	import { Avatar, HistorySentence } from '$lib/components/common';
	import { figureWords } from '$lib/components/insights/figures';
	import type { SessionPath } from '$lib/components/insights/cards/kinds';
	import { pictureOf } from '$lib/components/insights/cards/year/pictures';

	interface Props {
		path: SessionPath;
	}

	let { path }: Props = $props();

	/* The gap stands after the first pages: the server keeps the last two. */
	const gapAt = $derived(path.more > 0 ? path.steps.length - 2 : -1);
</script>

<div class="session">
	<p class="title">How one visit went</p>
	<div class="track">
		<span class="rail"></span>
		<ol class="path">
			{#each path.steps as step, index (index)}
				{@const picture = pictureOf(step.piece)}
				{#if index === gapAt}
					<li class="gap">
						<span class="node"><span class="dot"></span></span>
						{`${figureWords(path.more, 'count')} ${path.more === 1 ? 'more page' : 'more pages'}`}
					</li>
				{/if}
				<li class="step" class:ends={index === 0 || index === path.steps.length - 1}>
					{#if picture}
						<span class="node pictured">
							<Avatar
								src={picture}
								name={step.piece.text}
								shape="face"
								mark={step.piece.kind === 'site'}
								decorative
							/>
						</span>
					{:else}
						<span class="node"><span class="dot"></span></span>
					{/if}
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
		justify-content: center;
		gap: var(--space-3);
		min-block-size: 0;
		overflow: hidden;
	}

	.title {
		margin: 0;
		font: var(--text-micro);
		letter-spacing: var(--tracking-micro);
		text-transform: uppercase;
		color: var(--sift-ink-2);
	}

	/* One line down the left through the middle of every node: elements with a ground, so a saved
	   picture paints them as the screen does. */
	.track {
		position: relative;
		display: grid;
	}

	.rail {
		position: absolute;
		inset-block: var(--space-4);
		inset-inline-start: calc(var(--list-cover) / 2 - var(--chart-mark-gap) / 2);
		inline-size: var(--chart-mark-gap);
		background: var(--sift-accent-tint-2);
	}

	.path {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.step,
	.gap {
		position: relative;
		display: grid;
		grid-template-columns: var(--list-cover) minmax(0, 1fr) auto;
		align-items: center;
		gap: var(--space-3);
		min-inline-size: 0;
	}

	.node {
		display: grid;
		place-items: center;
		inline-size: var(--list-cover);
		aspect-ratio: 1;
	}

	/* A page's picture as its node, round, ringed in the card's ground so the rail stops at it. */
	.pictured {
		overflow: hidden;
		border-radius: 50%;
		background: var(--sift-accent-shade-2);
		box-shadow: var(--elev-2);
	}

	.pictured > :global(.avatar) {
		inline-size: 100%;
	}

	/* A place's node: a dot on the rail. */
	.dot {
		inline-size: var(--space-2);
		aspect-ratio: 1;
		border-radius: 50%;
		background: var(--sift-accent-tint-1);
	}

	.name {
		overflow: hidden;
		font: var(--text-body-sm);
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	/* The first page and the last read a step heavier: where it began and where it ended. */
	.ends .name {
		font: var(--text-h3);
	}

	.after,
	.gap {
		font: var(--text-label);
		color: var(--sift-ink-2);
		font-variant-numeric: tabular-nums;
	}
</style>
