<script lang="ts">
	/* Settings > Stash-boxes: the shared databases Sift can look a file up in, and what each may
	 * fill in when it answers. */
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

<!--
	The section on the one Recognition layout: the switch and where it stands, when lookups run, More
	settings, then the list of stash-boxes and what they are allowed to write.
-->
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
