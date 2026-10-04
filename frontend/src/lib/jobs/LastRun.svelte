<script lang="ts">
	/*
	 * A long run's last finished run, in a line: how long ago in the page's words, the exact moment on
	 * the hover, and what the run did in its own sentence.
	 *
	 * Given a TASK, it reads that task's row on Tasks, so the page and the Tasks screen say one run
	 * to the second and the line moves when the queue does. Given a RUN, it draws the one the page's
	 * own read carries. Inline, so the page chooses the block it sits in. See `last-run.ts`.
	 */
	import { onMount } from 'svelte';
	import { Tooltip } from '$lib/components/common';
	import { taskList } from '$lib/jobs/tasks.svelte';
	import {
		hoverSaid,
		lineSaid,
		runWords,
		sayRun,
		type RunOnRecord,
		type RunWords
	} from './last-run';

	interface Props {
		/** The task whose last run to say, by its id on Tasks. */
		task?: string;
		/** Or the run itself, for a run that is not a task. */
		run?: RunOnRecord | null;
		words?: RunWords;
	}

	let { task, run, words = runWords() }: Props = $props();

	let now = $state(Math.floor(Date.now() / 1000));

	onMount(() => {
		if (task) void taskList.ensure();
		/* "just now" becomes "1 minute ago" while the page stays open. */
		const tick = setInterval(() => (now = Math.floor(Date.now() / 1000)), 30_000);
		return () => clearInterval(tick);
	});

	const shown = $derived<RunOnRecord | null>(
		task ? (taskList.row(task)?.last ?? null) : (run ?? null)
	);
</script>

{#if shown}
	{@const own = lineSaid(shown)}
	<!-- The space is its own node outside the hover's box: whitespace at the end of an inline box is
	     not drawn, so a line would read "ran just now.Saved". -->
	<Tooltip label={hoverSaid(shown)}><span>{sayRun(shown, words, now)}</span></Tooltip
	>{#if own}{' '}{own}{/if}
{/if}
