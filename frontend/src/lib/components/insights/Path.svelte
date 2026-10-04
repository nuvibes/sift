<script lang="ts">
	/*
	 * Get to know Sift's learning paths: each path's name, the sentence that says what it teaches,
	 * how far along it is, and its goals in order. Drawn by the Get to know Sift section of Settings.
	 *
	 * The feel of a habit-forming app and none of its machinery: short, one thing at a time,
	 * progress you can see, a small celebration when a goal is reached, and no guilt. So there are
	 * no points, no levels, no streak and no weekly chore, and nothing counts what was missed.
	 *
	 * It reads its own answer: the section holds only the sentence that says what it is for. A
	 * failure draws one quiet line and nothing else is affected.
	 *
	 * Every path's sentence and every goal's help are the server's, drawn from their pieces; a
	 * path's and a goal's names arrive whole. What is written here is the furniture: how far along
	 * a path is. See `your-path.ts`.
	 *
	 * NOT ON THE GALLERY: it fetches on mount, so an entry for it would draw whatever the
	 * library happened to hold rather than a fixed example. What it draws is on the gallery:
	 * `PathSteps` for the goals, and `SectionHeading`, `ProgressBar`, `Problem` and `Skeleton`.
	 */
	import { onMount } from 'svelte';

	import HistorySentence from '$lib/components/common/HistorySentence.svelte';
	import Problem from '$lib/components/common/Problem.svelte';
	import ProgressBar from '$lib/components/common/ProgressBar.svelte';
	import SectionHeading from '$lib/components/common/SectionHeading.svelte';
	import Skeleton from '$lib/components/common/Skeleton.svelte';
	import PathSteps from './PathSteps.svelte';
	import { celebrate, pathProgress, readPath, type PathAnswer } from './your-path';

	let answer = $state<PathAnswer | null>(null);
	let failed = $state(false);

	onMount(() => {
		let here = true;
		readPath()
			.then((found) => {
				if (!here) return;
				answer = found;
				celebrate(found);
			})
			.catch(() => {
				if (here) failed = true;
			});
		return () => {
			here = false;
		};
	});
</script>

{#if answer}
	<div class="paths">
		{#each answer.paths as path (path.id)}
			{@const done = path.goals.filter((goal) => goal.done).length}
			<section class="path" aria-labelledby="path-{path.id}">
				<SectionHeading id="path-{path.id}">{path.title}</SectionHeading>
				<p class="sentence"><HistorySentence pieces={path.sentence} /></p>
				<div class="progress">
					<ProgressBar value={done} max={path.goals.length} label={path.title} />
					<span class="count">{pathProgress(path)}</span>
				</div>
				<PathSteps steps={path.goals} />
			</section>
		{/each}
	</div>
{:else if failed}
	<Problem message="Get to know Sift couldn't be loaded. Try again in a moment." />
{:else}
	<Skeleton lines={3} />
{/if}

<style>
	.paths {
		display: flex;
		flex-direction: column;
		gap: var(--space-8);
	}

	.path {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	.sentence {
		margin: 0;
		font: var(--text-body);
		color: var(--sift-ink-2);
	}

	/* The bar, and how many of the goals are done at its end. */
	.progress {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		align-items: center;
		gap: var(--space-3);
	}

	.count {
		font: var(--text-body-sm);
		font-variant-numeric: tabular-nums;
		color: var(--sift-ink-3);
	}
</style>
