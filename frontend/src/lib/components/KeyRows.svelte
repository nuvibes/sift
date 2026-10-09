<script lang="ts">
	/*
	 * A list of DECLARED keys and what they do, for Theater's panel and Settings alike. `widest`
	 * lays every key on the page unseen in the first row, so several lists share one key column.
	 */
	import type { Shortcut } from '$lib/shell/shortcuts';

	interface Props {
		shortcuts: readonly Shortcut[];
		widest?: readonly string[];
	}

	let { shortcuts, widest = [] }: Props = $props();
</script>

<dl>
	{#each shortcuts as row, at (row.id)}
		<!-- The sizer comes AFTER the key, so the cell's first baseline is the key's own line. -->
		<dt>
			{row.shown}{#if at === 0 && widest.length > 0}<span class="sizer" aria-hidden="true"
					>{#each widest as key, which (`${which}:${key}`)}<span>{key}</span>{/each}</span
				>{/if}
		</dt>
		<dd>{row.does}</dd>
	{/each}
</dl>

<style>
	dl {
		display: grid;
		grid-template-columns: auto 1fr;
		align-items: baseline;
		gap: var(--space-2) var(--space-4);
		margin: 0;
	}

	/* Width only: no height, no ink, out of the accessibility tree and find-in-page. */
	.sizer {
		display: block;
		block-size: 0;
		overflow: hidden;
		visibility: hidden;
	}

	.sizer > span {
		display: block;
	}

	dt {
		color: var(--sift-ink);
		font: var(--text-data);
		white-space: nowrap;
	}

	dd {
		margin: 0;
		color: var(--sift-ink-2);
		font: var(--text-body);
	}
</style>
