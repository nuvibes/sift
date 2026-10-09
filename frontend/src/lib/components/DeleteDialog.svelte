<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/*
	 * The two-tier delete, the safe tier chosen when it opens and listed first; the disk tier,
	 * which has no undo, asks again. Disk is an admin's; writability is the server's to refuse.
	 * Asked on open: pictures inside a ZIP are never deleted from disk, so that card gives way for
	 * them.
	 */
	import { api } from '$lib/api/client';
	import { libraryChanges, whenChanged } from '$lib/library/changes.svelte';
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
		/** In every sentence: "delete these?" with no number deletes thirty meaning one. */
		count: number;
		canDeleteFromDisk: boolean;
		unavailableReason?: string;
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

	/* Nothing yet reads as an ordinary selection. */
	let reach = $state<DeleteReach | null>(null);

	/* Asked on every open; a late answer for another selection is dropped. */
	$effect(() => {
		if (!open || !canDeleteFromDisk || ids.length === 0) return;
		const asked = ids;
		reach = null;
		void api
			.post<DeleteReach>('/assets/delete/check', { body: { asset_ids: asked } })
			.then((answer) => {
				if (asked !== ids) return;
				reach = answer;
				if (answer.inside_archives >= count) mode = 'sift';
			})
			.catch(() => {
				// Unknown: the card stays and the route says why if it refuses.
			});
	});

	whenChanged(libraryChanges, () => {
		if (!open || !canDeleteFromDisk || ids.length === 0) return;
		const asked = ids;
		void api
			.post<DeleteReach>('/assets/delete/check', { body: { asset_ids: asked } })
			.then((answer) => {
				if (asked !== ids || JSON.stringify(answer) === JSON.stringify(reach)) return;
				reach = answer;
				if (answer.inside_archives >= count) mode = 'sift';
			})
			.catch(() => {
				// The answer as drawn stays.
			});
	});

	const allInside = $derived(
		reach !== null && reach.inside_archives > 0 && reach.inside_archives >= count
	);
	const someInside = $derived(reach !== null && reach.inside_archives > 0 && !allInside);
	const offersDisk = $derived(canDeleteFromDisk && !allInside);
	const unavailable = $derived(allInside ? (reach?.why ?? undefined) : unavailableReason);

	// The safe tier, every time it opens; never remembered.
	let mode = $state<DeleteMode>('sift');

	let confirming = $state(false);

	/* Reset on every open; only what was WRITTEN survives (`remembered.svelte`). */
	let dontAskAgain = $state(false);

	$effect(() => {
		if (open) {
			mode = 'sift';
			dontAskAgain = false;
		}
	});

	const things = $derived(count === 1 ? 'file' : `${counted(count)} files`);

	/* Says BOTH halves: gone from the disk AND from Sift. */
	const diskConsequence = "This deletes the file from your disk and from Sift. It can't be undone.";

	const title = $derived(
		mode === 'sift' ? `Remove ${things} from Sift?` : `Delete ${things} from disk?`
	);

	/* For archived pictures the safe tier's way back is Try again under Organize > Skipped. */
	const consequence = $derived(
		mode === 'disk'
			? `This deletes ${count === 1 ? 'the file' : `all ${counted(count)} files`} from your disk. There's no Trash and nothing to restore from.`
			: allInside
				? `The ZIP file stays as it is, and later scans leave ${count === 1 ? 'this picture' : 'these pictures'} out. Try again under Organize > Skipped brings ${count === 1 ? 'it' : 'them'} back.`
				: `Your ${count === 1 ? 'file stays' : 'files stay'} on disk. You can add ${count === 1 ? 'it' : 'them'} back by re-scanning.`
	);

	const leftInside = $derived(
		someInside && reach
			? `${reach.inside_archives === 1 ? 'One of these is a picture' : `${counted(reach.inside_archives)} of these are pictures`} inside a ZIP file, and ${reach.inside_archives === 1 ? 'it stays' : 'they stay'}.`
			: undefined
	);

	const confirmLabel = $derived(mode === 'sift' ? 'Remove from Sift' : 'Delete from disk');

	/* `confirming` is set, and the two dialogs are never open together. */
	function answered() {
		if (mode === 'sift') {
			onconfirm('sift');
			return;
		}
		/* Read when needed, since another tab can change it; the two cards are never skipped. */
		if (deleteConfirmationSkipped()) {
			onconfirm('disk');
			return;
		}
		confirming = true;
	}

	/** The box is written only here, after the press. */
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
				<!--
				Each card wears its action's glyph; `cancel` OUTLINED, since filled means a failed
				job.
				-->
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

<!-- The second question: plainer, the number and "permanently". -->
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
		Turning the guard off, on the guard itself; remembered only once the red button is pressed.
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
	.tiers {
		margin: 0 0 var(--space-5);
	}

	.again {
		/* No top margin: the sentence above gives its space up instead (`.asked-again`). */
		margin: 0 0 var(--space-3);
	}

	/*
	 * Written twice to outrank `.sheet .consequence` without naming `.sheet` (the anchored-globals
	 * gate).
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
