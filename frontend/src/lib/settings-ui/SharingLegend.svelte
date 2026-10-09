<script lang="ts">
	/* What the badges on a thumbnail mean, written out once, where somebody can go and look. */
	import Icon from '$lib/components/Icon.svelte';
	import { HIDDEN_WORDS, markFor, type Mark } from '$lib/library/sharing-marks';

	/* The legend is about the marks on a FILE, which is the only place both forms appear. */
	const ON_A_FILE = { file: true };

	/** Every combination a mark can be in, in the order they are worth reading. */
	const CASES: { mark: Mark; note: string }[] = [
		{
			mark: markFor({ shared: true, shared_here: true }, ON_A_FILE)!,
			note: 'Solid: set on this file only. Open Sharing on the file to change it.'
		},
		{
			mark: markFor({ restricted: true, restricted_here: true }, ON_A_FILE)!,
			note: 'Solid: set on this file only. Keeping it private overrides sharing set on a folder, tag or collection it is in.'
		},
		{
			mark: markFor({ shared: true, restricted: true, shared_here: true }, ON_A_FILE)!,
			note: 'Shared with one guest and kept private from another. Open Sharing to see which.'
		},
		{
			mark: markFor({ shared: true }, ON_A_FILE)!,
			note: 'Outlined: set on something the file is in, like a folder, tag or collection. Change it there. A folder or tag also shows the solid mark in its own row.'
		},
		{
			mark: markFor({ restricted: true }, ON_A_FILE)!,
			note: 'Outlined: something this file is in is kept private, not the file itself.'
		}
	];
</script>

<ul class="legend">
	{#each CASES as row (row.mark.words + String(row.mark.filled))}
		<li>
			<span
				class="on-a-tile tile-badge tile-badge-square"
				class:restricted={row.mark.kind === 'restricted'}
				class:both={row.mark.kind === 'both'}
			>
				<Icon name={row.mark.icon} size={16} filled={row.mark.filled} />
			</span>
			<span class="words">
				<span class="what">
					<span class="verb" data-kind={row.mark.kind}>{row.mark.verb}</span>
					{row.mark.rest}
				</span>
				<span class="note">{row.note}</span>
			</span>
		</li>
	{/each}

	<li>
		<span class="on-a-tile tile-badge tile-badge-square">
			<Icon name="visibility_off" size={16} filled />
		</span>
		<span class="words">
			<span class="what">{HIDDEN_WORDS}</span>
			<span class="note">
				Appears only where a hidden file is shown: the Hidden screen, or anywhere once you unlock
				Hidden.
			</span>
		</span>
	</li>
</ul>

<p class="note foot">
	Only you see these marks. A guest never sees who anything is shared with, even for files shared
	with them. At the smallest tile size the marks are left off.
</p>

<style>
	.legend {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		margin: var(--space-3) 0 0;
		padding: 0;
		list-style: none;
	}

	li {
		display: flex;
		align-items: flex-start;
		gap: var(--space-3);
	}

	/* The mark exactly as a tile draws it, dark ground and all, because that is the thing being
	 * described: a legend showing a bare glyph on the page background is describing something
	 * the reader has never seen. */
	/* The geometry is the tile's own, through `--tile-chip-*`: this is a picture OF the mark
	   somebody sees on a thumbnail, so a legend drawn a couple of pixels off is a legend that
	   teaches the wrong glyph. */
	/* The shape and the square are `.tile-badge` / `.tile-badge-square` in `app.css`. */
	.on-a-tile {
		color: var(--sift-ok);
	}

	.on-a-tile.restricted {
		color: var(--sift-bad-text);
	}

	.on-a-tile.both {
		color: var(--sift-warn);
	}

	.words {
		display: flex;
		flex-direction: column;
		gap: 2px;
		min-inline-size: 0;
		/* The row help's reading measure, which the pane gives its paragraphs: these sentences are
		   spans in a list, so the pane's rule does not reach them and they would run the width. */
		max-inline-size: var(--reading-measure);
	}

	.what {
		font: var(--text-body);
		color: var(--sift-ink);
	}

	/* The status word in the colour it wears everywhere else. */
	.verb[data-kind='shared'] {
		color: var(--sift-ok);
	}

	.verb[data-kind='restricted'] {
		color: var(--sift-bad-text);
	}

	.verb[data-kind='both'] {
		color: var(--sift-warn);
	}

	.note {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.foot {
		margin: var(--space-4) 0 0;
	}
</style>
