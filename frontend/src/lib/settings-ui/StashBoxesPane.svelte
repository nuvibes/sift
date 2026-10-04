<script lang="ts">
	/*
	 * Settings > Stash-boxes: the shared databases Sift can look a file up in, and what each may
	 * fill in when it answers.
	 *
	 * ## Its own section
	 *
	 * Forty-three declared settings, and they are all this: filed under a pane about connections
	 * they would be the largest thing on it, three scrolls down, under a name for something
	 * smaller.
	 *
	 * It sits under Recognition, beside Faces and Smart Search, because it is the same job by a
	 * different route: those two work out what a file holds from the file itself, and this asks
	 * somebody who already knows. It holds only stash-boxes: naming a file's song has its own
	 * section, Music.
	 *
	 * The pane and the list are two files on purpose: `StashBoxes.svelte` is the list of boxes
	 * and is where all of that behaviour lives. `Enrichment.svelte` owns the section's registered
	 * settings and draws the Recognition layout; this is the section around both.
	 */
	import EnrichmentSection from './Enrichment.svelte';
	import StashBoxesSection from './StashBoxes.svelte';
	import { StashBoxes } from './stash-boxes.svelte';
	import UnlockSecrets from './UnlockSecrets.svelte';

	/* One reader, shared with the section below, so unlocking can refresh the thing it just
	   started: a saved key is only usable once the master key is back. */
	const boxes = new StashBoxes();
	boxes.follow();
</script>

<p class="intro">
	A stash-box is a shared database of who is in what. Add one with your API key, and Sift fills in
	your files' details so you don't have to type them.
</p>

<UnlockSecrets onunlocked={() => void boxes.load()} />

<!-- The section on the one Recognition layout: the switch and where it stands, when lookups run,
     More settings, then the list of stash-boxes and what they are allowed to write. The list is
     drawn by the section's settings rather than beside them, so the switch comes first and the
     list follows the rows that decide what it is used for. -->
<EnrichmentSection {boxes}>
	{#snippet list()}
		<StashBoxesSection {boxes} />
	{/snippet}
</EnrichmentSection>

<style>
	.intro {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
		margin-block-end: var(--space-6);
	}
</style>
