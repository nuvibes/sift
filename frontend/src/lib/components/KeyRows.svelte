<script lang="ts">
	/*
	 * A list of keys and what they do.
	 *
	 * ONE SHAPE, TWO PLACES. Theater has a panel on its bar that says what the numbers do there, and
	 * Settings has a section listing every shortcut in the application. They are the same object:
	 * a key on the left, a sentence on the right, aligned down two columns. Two files would be two
	 * descriptions of one keyboard, free to disagree.
	 *
	 * The rows are always DECLARED shortcuts, never strings: a hand-written key sheet can say the
	 * numbers go "1 to 4" while the code takes 1 to 9.
	 *
	 * The wrapper is the caller's. Theater sits this on a raised panel over a wall; Settings puts it
	 * under a heading in a column of reading. Neither of those is a fact about a list of keys.
	 *
	 * ONE KEY COLUMN ACROSS SEVERAL LISTS. A page that splits its keys under headings draws one of
	 * these per heading, and each would size its key column to its own widest key, so the sentences
	 * would start at a different x under every heading. `widest` hands every list the keys of the whole
	 * page, laid unseen in the first row's key cell, so each column is as wide as the widest key
	 * on the page and every sentence starts on one line.
	 */
	import type { Shortcut } from '$lib/shell/shortcuts';

	interface Props {
		shortcuts: readonly Shortcut[];
		/** Every key the page shows, where the page draws several lists that must share a column. */
		widest?: readonly string[];
	}

	let { shortcuts, widest = [] }: Props = $props();
</script>

<dl>
	{#each shortcuts as row, at (row.id)}
		<!-- The key and the sizer with nothing between them: any space would be part of the key. The
		     sizer comes AFTER the key, so the cell's first baseline is the key's own line; before it,
		     the empty block would set the baseline and the first key would sit a line below its
		     sentence. -->
		<dt>
			{row.shown}{#if at === 0 && widest.length > 0}<span class="sizer" aria-hidden="true"
					>{#each widest as key, which (`${which}:${key}`)}<span>{key}</span>{/each}</span
				>{/if}
		</dt>
		<dd>{row.does}</dd>
	{/each}
</dl>

<style>
	/* On the first baseline, so a key in the data face and its sentence in the body face read as
	   one line rather than two tops lined up. */
	dl {
		display: grid;
		grid-template-columns: auto 1fr;
		align-items: baseline;
		gap: var(--space-2) var(--space-4);
		margin: 0;
	}

	/* Takes its width into the column and nothing else: no height, no ink, and out of the
	   accessibility tree and find-in-page. */
	.sizer {
		display: block;
		block-size: 0;
		overflow: hidden;
		visibility: hidden;
	}

	.sizer > span {
		display: block;
	}

	/* The two faces are Theater's, where this list is read most: a key is DATA (tabular, so
	   `1 to 9` and `Shift + /` line up down the column) and the
	   sentence beside it is ordinary body text a step quieter. Settings inherits both rather than
	   choosing its own, which is the whole reason this is one component. */
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
