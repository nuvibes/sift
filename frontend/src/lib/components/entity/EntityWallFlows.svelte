<script lang="ts">
	/*
	 * The sheets the entity verbs open, the drawing half of wall-verbs.svelte.ts; each is the one
	 * the
	 * rest of the application uses.
	 * NOT ON THE GALLERY: it draws nothing at all until a verb opens something, and each of the
	 * sheets it draws has its own entry.
	 */
	import { ConfirmDialog } from '$lib/components/common';
	import RenameDialog from './RenameDialog.svelte';
	import HiddenDialog from '$lib/components/HiddenDialog.svelte';
	import ShareDialog from '$lib/components/ShareDialog.svelte';
	import VisibilityDialog from '$lib/components/VisibilityDialog.svelte';
	import MergeEntities from '$lib/components/entity/MergeEntities.svelte';
	import type { WallVerbs } from './wall-verbs.svelte';

	interface Props {
		/** The wall's verbs, made during its setup. This draws whatever it has open. */
		verbs: WallVerbs;
	}

	let { verbs }: Props = $props();
</script>

<!-- Renaming as a confirmation with a box: a name is shared vocabulary. -->

<RenameDialog
	bind:open={verbs.renameOpen}
	named={verbs.renaming?.name ?? ''}
	consequence="Everybody's screens and searches say the new name. Nothing else about it changes."
	bind:value={verbs.renameTo}
	maxlength={verbs.facts.nameLimit}
	onconfirm={() => void verbs.rename()}
/>

<ShareDialog
	bind:open={verbs.shareOpen}
	targets={verbs.sharing}
	onapplied={() => verbs.applied()}
/>
<VisibilityDialog bind:open={verbs.reachOpen} target={verbs.reaching} />
<!-- Why this is hidden, from the card's own Hidden mark, for everybody: hiding is personal. -->
<HiddenDialog bind:open={verbs.hiddenOpen} target={verbs.hiddenAbout} />

<ConfirmDialog
	bind:open={verbs.confirmOpen}
	title={verbs.deleteTitle}
	consequence={verbs.deleteConsequence}
	confirmLabel={verbs.deleteLabel}
	onconfirm={() => void verbs.remove()}
/>

<!-- The person page's merge sheet. No count is handed over (see `WallRow.count`); the sheet
settles its keeper untracked, as this array is rebuilt on every redraw. -->

{#if verbs.facts.mergeable}
	<MergeEntities
		people={verbs.merging.map((row) => ({
			id: row.id,
			name: row.name,
			files: row.count,
			picture: row.picture
		}))}
		kind={verbs.facts.mergeable}
		withButton={false}
		bind:open={verbs.mergeOpen}
		onmerged={() => {
			verbs.mergeOpen = false;
			verbs.applied();
		}}
	/>
{/if}
