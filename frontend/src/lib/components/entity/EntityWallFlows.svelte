<script lang="ts">
	/*
	 * The sheets the entity verbs open, for any wall of named things.
	 *
	 * The drawing half of `wall-verbs.svelte.ts`: that file decides what each verb DOES and holds
	 * what is open, this puts the sheets on the screen. They are split because the handlers
	 * have to exist before a menu is built and a component's exports do not exist until it has
	 * mounted, so a wall makes the class during setup and draws this beside its cards.
	 *
	 * Every sheet here is the one the rest of the application already uses. Nothing is a second
	 * copy: a share from a tag's People tab opens the same panel a share from the People wall does,
	 * so the two cannot come to describe a share differently.
	 *
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

<!--
	Renaming, as a confirmation with a box in it rather than a sheet of its own.

	A name here is shared vocabulary (it changes what everybody's screens and searches say) so
	the sentence that says so is the same thing `ConfirmDialog` exists to make unskippable. Every
	wall renames here, so a wall on a tab renames exactly as the wall a thing lives on does.
-->
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
<!-- Why this is hidden, a different question from who it is shared with, opened from the card's
     own Hidden mark. Offered to everybody: hiding is personal, so a guest has their own vault. -->
<HiddenDialog bind:open={verbs.hiddenOpen} target={verbs.hiddenAbout} />

<ConfirmDialog
	bind:open={verbs.confirmOpen}
	title={verbs.deleteTitle}
	consequence={verbs.deleteConsequence}
	confirmLabel={verbs.deleteLabel}
	onconfirm={() => void verbs.remove()}
/>

<!-- No button of its own: the wall opens it from the menu and the bar. The same sheet a person's
     own page draws, so a merge is weighed the same way wherever it is asked for.

     Each row carries its optional `picture`, filled by `RelatedWall` through the same builders
     every other picker uses; a wall whose rows have no cover columns hands nothing over and its
     cards fall back to a letter.

     The count is deliberately withheld (see `WallRow.count`): a picture in a filtered context is
     the same picture, but a count in a filtered context is a different number.

     The array is rebuilt on every redraw of the wall behind the sheet, because it is `.map`ped
     here. The sheet makes that safe by settling its keeper and its count untracked, so a repaint
     cannot throw away a choice just made. Said here as well because this line produces the
     condition. -->
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
