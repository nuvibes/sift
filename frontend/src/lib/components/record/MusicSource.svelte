<script lang="ts">
	/*
	 * WHERE A FILE'S SONG NAME CAME FROM, in one line under its Music field.
	 *
	 * Sift writes a song's name in three ways and a person in a fourth, and the field alone cannot
	 * say which: a title and an artist read the same whoever put them there. The line says:
	 *
	 *   - **A Site's page**: the page a file was downloaded from named its song. "Named from
	 *     {Site}'s page", or "its download page" where the file is not filed under exactly one Site
	 *     (the page was that download's; with two Sites here, naming one would be a guess).
	 *   - **AcoustID**: the lookup, which only runs when somebody turned it on and gave a key.
	 *     "Named from AcoustID", the Site's line in the same shape. Not "named by" Sift: the
	 *     vocabulary gate refuses that pair.
	 *   - **Another file**: the two files share a song and this one took the other's name. The
	 *     other file is a link, opened in place; where this account may not see it the server sends
	 *     no file, and the line says "another file" rather than naming what it cannot show.
	 *
	 * A name somebody TYPED draws nothing. It is the ordinary case, it is theirs, and a line saying
	 * "typed by you" under every field somebody filled in would be noise.
	 *
	 * ## Undo, and only where it means something
	 *
	 * A shared name is a guess Sift made from two fingerprints, so it can be taken back, and taking
	 * it back keeps it off, so pairing the files again does not bring it back. The press is handed in
	 * rather than done here, because it is the SAME undo the file's History line offers for the same
	 * act: one door, reached from two places. A name from a Site's page or from AcoustID is what that
	 * source said, and the way to change it is to type over it.
	 */
	import Button from '$lib/components/common/Button.svelte';
	import AssetLink from '$lib/components/common/AssetLink.svelte';
	import type { MusicFrom } from '$lib/player/music';

	interface Props {
		/** The file view's `music_source`: `typed`, `site`, `acoustid`, `shared`, or null. */
		source: string | null;
		/** The file a shared name came from, where this account may see it. */
		from?: MusicFrom | null;
		/** The one Site the file is filed under, where there is exactly one. */
		site?: string | null;
		/** Take a shared name back. Absent, no Undo is drawn. */
		onundo?: () => void;
		/** Waiting on the undo that was asked for. */
		undoing?: boolean;
	}

	let { source, from = null, site = null, onundo, undoing = false }: Props = $props();

	/* Undo only on a shared name: the one source that is Sift's own guess. */
	const canUndo = $derived(source === 'shared' && onundo !== undefined);
</script>

{#if source === 'site' || source === 'acoustid' || source === 'shared'}
	<p class="source">
		<span class="said">
			{#if source === 'site'}
				Named from {site ? `${site}'s page` : 'its download page'}
			{:else if source === 'acoustid'}
				Named from AcoustID
			{:else if from}
				Shared with <AssetLink id={from.id}>{from.name}</AssetLink>
			{:else}
				Shared with another file
			{/if}
		</span>
		{#if canUndo}
			<!-- The History row's own Undo, drawn the same way, because it is the same act. -->
			<Button icon="undo" tone="ghost" size="small" busy={undoing} onclick={() => onundo?.()}
				>Undo</Button
			>
		{/if}
	</p>
{/if}

<style>
	/* A caption under the value: quieter than the value it explains, on one line where it fits. */
	.source {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
		margin: var(--space-1) 0 0;
		font: var(--text-label);
		color: var(--sift-ink-3);
	}

	.said {
		min-inline-size: 0;
		overflow-wrap: anywhere;
	}
</style>
