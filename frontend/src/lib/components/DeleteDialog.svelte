<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/*
	 * The two-tier delete, asked as a question, and for the tier that cannot be taken back, asked
	 * again.
	 *
	 * The wording is the safety mechanism. The two tiers are two labelled choices with their
	 * consequences next to them, rather than a checkbox on one action. The safe one is selected
	 * when the dialog opens, listed first, and is what the confirm button does if nobody touches
	 * anything. The destructive one has to be chosen, turns the button red and changes its label to
	 * say what it will really do.
	 *
	 * Deleting from disk has no undo and no bin, so choosing it asks a second, separate question
	 * that states the number and says the word: two deliberate answers on two screens.
	 *
	 * One verb from the press to the answer. The row that opens this says Remove (see
	 * `grid/verbs.ts`), which is the tier chosen when it opens: the file leaves Sift and stays on
	 * disk. Delete is the word only for the tier where the file stops existing.
	 *
	 * Delete-from-disk is offered only to an admin; `unavailableReason` is the line in its place.
	 * Whether a file's folder will take a write cannot be known here: a root's reply carries no
	 * writability, a file carries no root, an unreachable file counts as a successful disk delete
	 * on purpose so its index row can be cleaned up, and the filesystem's answer can change between
	 * reading and acting. So the server refuses in a sentence and the caller says it; see
	 * `grid/actions.svelte.ts` `remove` and `announceSkipped`.
	 *
	 * ONE THING CAN BE KNOWN, and is asked when the sheet opens: whether the files are pictures
	 * inside a ZIP file (`/assets/delete/check`). Sift does not change a person's archive, so
	 * the disk tier is refused for one, and a sheet that offered it would be promising what the
	 * server will then refuse. Every one inside an archive: the disk card gives way to the
	 * server's own sentence, and the safe tier says the scan leaves them out (it does not bring
	 * them back, unlike a loose file). Some: the disk card says how many stay. The routes refuse
	 * on their own either way; this is what the screen knows, never the guard.
	 */
	import { api } from '$lib/api/client';
	import type { components } from '$lib/api/schema';
	import {
		Checkbox,
		ChoiceCard,
		ChoiceGroup,
		ConfirmDialog,
		Pressable
	} from '$lib/components/common';
	import {
		deleteConfirmationSkipped,
		rememberDeleteConfirmation
	} from '$lib/shell/remembered.svelte';

	export type DeleteMode = 'sift' | 'disk';

	interface Props {
		open?: boolean;
		/** How many things this is about. Stated in every sentence below, because "delete these?"
		 * with no number is how somebody deletes thirty things meaning to delete one. */
		count: number;
		/** Whether removing the files from the disk is possible at all here. */
		canDeleteFromDisk: boolean;
		/** Why it is not, when it is not. Shown instead of the option. */
		unavailableReason?: string;
		/** The files themselves, so the sheet can ask which are inside an archive. */
		ids?: string[];
		onconfirm: (mode: DeleteMode) => void;
	}

	type DeleteReach = components['schemas']['DeleteReach'];

	let {
		open = $bindable(false),
		count,
		canDeleteFromDisk,
		unavailableReason,
		ids = [],
		onconfirm
	}: Props = $props();

	/* What the server said about these files, or nothing yet. Nothing reads as an ordinary
	   selection: the disk card is offered and the route refuses a member in its own words. */
	let reach = $state<DeleteReach | null>(null);

	/* Asked on every open, never kept: the same sheet opens on another selection next time. A late
	   answer for an earlier selection is dropped by comparing the ids it was asked about. */
	$effect(() => {
		if (!open || !canDeleteFromDisk || ids.length === 0) return;
		const asked = ids;
		reach = null;
		void api
			.post<DeleteReach>('/assets/delete/check', { body: { asset_ids: asked } })
			.then((answer) => {
				if (asked !== ids) return;
				reach = answer;
				// A disk card chosen before the answer came is no longer there to have been chosen.
				if (answer.inside_archives >= count) mode = 'sift';
			})
			.catch(() => {
				// Unknown: the card stays offered and the delete route says why if it refuses.
			});
	});

	/** Every file is a picture inside an archive: the disk tier is not offered at all. */
	const allInside = $derived(
		reach !== null && reach.inside_archives > 0 && reach.inside_archives >= count
	);
	/** Some are: offered, with how many it leaves. */
	const someInside = $derived(reach !== null && reach.inside_archives > 0 && !allInside);
	const offersDisk = $derived(canDeleteFromDisk && !allInside);
	const unavailable = $derived(allInside ? (reach?.why ?? undefined) : unavailableReason);

	// The safe tier, always, every time the dialog opens. Not remembered between opens: a choice
	// that persists is a choice somebody made once and is now making every time without noticing.
	let mode = $state<DeleteMode>('sift');

	/** The second question, for the tier with no undo. Never open at the same time as the first. */
	let confirming = $state(false);

	/* Ticked on the second question, and acted on only if the answer is then CONFIRMED.
	 *
	 * Reset every time the dialog opens, exactly as the tier above is and for the same reason: a box
	 * left ticked from a cancelled dialog is an agreement nobody gave. What survives the reset is
	 * what was WRITTEN. See `remembered.svelte`, which is where the browser's answer lives. */
	let dontAskAgain = $state(false);

	$effect(() => {
		if (open) {
			mode = 'sift';
			dontAskAgain = false;
		}
	});

	const things = $derived(count === 1 ? 'file' : `${counted(count)} files`);

	/* Says BOTH halves, and the second half is the one that matters.
	 *
	 * "This deletes the file from your disk" alone leaves the other tier's difference unstated, so
	 * the two cards read as "take it off my screen" against "take it off my disk", and somebody who
	 * wanted the file gone everywhere could reasonably think the safe one leaves a copy in Sift. It
	 * goes from both, and that is what makes it the tier with no way back. */
	const diskConsequence = "This deletes the file from your disk and from Sift. It can't be undone.";

	const title = $derived(
		mode === 'sift' ? `Remove ${things} from Sift?` : `Delete ${things} from disk?`
	);

	/* For pictures inside an archive the safe tier's second sentence is different and so is its
	   way back: a scan leaves them out, and Try again under Organize > Skipped lets one in again. */
	const consequence = $derived(
		mode === 'disk'
			? `This deletes ${count === 1 ? 'the file' : `all ${counted(count)} files`} from your disk. There's no Trash and nothing to restore from.`
			: allInside
				? `The ZIP file stays as it is, and later scans leave ${count === 1 ? 'this picture' : 'these pictures'} out. Try again under Organize > Skipped brings ${count === 1 ? 'it' : 'them'} back.`
				: `Your ${count === 1 ? 'file stays' : 'files stay'} on disk. You can add ${count === 1 ? 'it' : 'them'} back by re-scanning.`
	);

	/* Under the disk card when part of the selection is inside an archive: those stay. */
	const leftInside = $derived(
		someInside && reach
			? `${reach.inside_archives === 1 ? 'One of these is a picture' : `${counted(reach.inside_archives)} of these are pictures`} inside a ZIP file, and ${reach.inside_archives === 1 ? 'it stays' : 'they stay'}.`
			: undefined
	);

	const confirmLabel = $derived(mode === 'sift' ? 'Remove from Sift' : 'Delete from disk');

	/* The first answer. The safe tier is done with; the destructive one has one more question.
	 *
	 * `confirming` is set rather than the action being run, and the two dialogs are never open
	 * together: the first closes itself on confirm. A second dialog stacked on a first is two
	 * veils, two focus traps, and an Escape that dismisses something nobody can see. */
	function answered() {
		if (mode === 'sift') {
			onconfirm('sift');
			return;
		}
		/* The second question, unless this browser has been told to stop asking it.
		 *
		 * Read at the moment it is needed rather than held in a variable when the dialog opens: the
		 * answer can be changed while this dialog is on screen (by the row that turns it back on,
		 * on another tab) and a copy taken at open time would be a stale one.
		 *
		 * What is NOT skipped is the dialog behind this call. The two cards are where the choice
		 * between Sift and the disk is made, and that is the guard that stops a tidy-up deleting
		 * files; the second question only restates a decision already taken, which is the one that
		 * can be agreed away. */
		if (deleteConfirmationSkipped()) {
			onconfirm('disk');
			return;
		}
		confirming = true;
	}

	/** The second answer. The box is written only here, after the press. See `dontAskAgain`. */
	function confirmed() {
		if (dontAskAgain) rememberDeleteConfirmation();
		onconfirm('disk');
	}
</script>

<ConfirmDialog
	bind:open
	{title}
	{consequence}
	{confirmLabel}
	destructive={mode === 'disk'}
	onconfirm={answered}
>
	{#snippet extra()}
		<div class="tiers">
			<ChoiceGroup
				label="What to do with {things}"
				layout="column"
				value={mode}
				onchange={(next) => (mode = next as DeleteMode)}
			>
				<!-- Each card wears the glyph the app already draws that action with, so the two are
				     told apart before either sentence is read. `cancel` OUTLINED for the safe one:
				     the filled circle is the mark this app uses for a job that failed, and a solid
				     red-weight disc beside "Remove from Sift" would say something went wrong. -->
				<ChoiceCard
					value="sift"
					icon="cancel"
					name="Remove from Sift"
					note="Your {count === 1 ? 'file stays' : 'files stay'} on disk."
				/>

				{#if offersDisk}
					<ChoiceCard
						value="disk"
						icon="delete"
						name="Delete from disk"
						note={diskConsequence}
						footnote={leftInside}
					/>
				{/if}
			</ChoiceGroup>

			{#if !offersDisk && unavailable}
				<p class="unavailable">{unavailable}</p>
			{/if}
		</div>
	{/snippet}
</ConfirmDialog>

<!--
	The second question. Deliberately plainer than the first: no choices on it, nothing to read past,
	one sentence naming the number and the word "permanently". Somebody who arrives here has already
	said what they want: what is left is to be sure they meant it.
-->
<ConfirmDialog
	bind:open={confirming}
	title="Permanently delete {things}?"
	consequence="{count === 1
		? 'This file'
		: `These ${counted(count)} files`} will be removed from your disk
		straight away. Sift keeps no copy and this can't be undone."
	confirmLabel="Delete permanently"
	consequenceClass="asked-again"
	destructive={true}
	onconfirm={confirmed}
>
	{#snippet extra()}
		<!--
			Turning the guard off, offered on the guard itself: somebody who finds this question in
			the way is looking at it when they think so, and a setting under Editing turns it back
			on. Above the two buttons, where it is seen rather than read as an afterthought. The row
			is the control, as on the compress sheet, since a 16-pixel box beside a sentence is a
			thing to aim at.

			Not remembered until the red button is pressed: a ticked box on a cancelled dialog is
			not an agreement to anything.
		-->
		<div class="again">
			<Pressable
				class="tick"
				feedback="wash"
				radius="md"
				aria-pressed={dontAskAgain}
				onclick={() => (dontAskAgain = !dontAskAgain)}
			>
				<Checkbox state={dontAskAgain ? 'on' : 'off'} mark />
				<span>I understand this is permanent. Don't ask me again.</span>
			</Pressable>
		</div>
	{/snippet}
</ConfirmDialog>

<style>
	/* The choices are `ChoiceGroup`'s and each card is `ChoiceCard`'s: the box, the chosen mark and
	   the gap between them all live there. What is left here is the space under the group. */
	.tiers {
		margin: 0 0 var(--space-5);
	}

	/* The tick row, on the second dialog. The gap under it is the group's job above; here it is the
	   space between the sentence that states the consequence and the offer to stop hearing it. */
	.again {
		/*
		 * Directly under the question. The space above is the sheet's: `.consequence` keeps
		 * `--space-6` below itself for a button row. A negative margin here would lift the row
		 * outside the sheet's scroll region and clip it, so the sentence gives the space up instead
		 * (`.asked-again` below, via `consequenceClass`) and this row keeps no top margin, so
		 * question, answer row and buttons read as one block.
		 */
		margin: 0 0 var(--space-3);
	}

	/*
	 * The consequence sentence of the second question, drawn inside the Modal and reached through
	 * the class handed to `consequenceClass`, the same arrangement as the hidden sheet's
	 * `.subject`. It keeps `--space-2` under itself instead of `--space-6`. The class is written
	 * twice on purpose: the shared rule is `.sheet .consequence` (app.css), a selector of equal
	 * weight loses to it on order, and naming `.sheet` here would reach a class this file does not
	 * write (the anchored-globals gate).
	 */
	:global(.consequence.asked-again.asked-again) {
		margin-block-end: var(--space-2);
	}

	.again :global(.tick) {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		inline-size: 100%;
		padding: var(--space-2) var(--space-3);
		text-align: start;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	.unavailable {
		margin: var(--space-2) 0 0;
		padding: var(--space-3);
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}
</style>
