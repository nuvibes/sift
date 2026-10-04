<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/*
	 * Choosing where some faces should go: into another group, or into one of their own.
	 *
	 * Merging and splitting are the same question asked about a destination, which is why this is
	 * one dialog and not two. "These belong with those" and "these do not belong with the rest of
	 * this lot" are the two halves of correcting a grouping, and somebody in the middle of doing it
	 * moves between them constantly.
	 *
	 * The faces are picked before this opens, so part of a group can move without the rest, which
	 * is the case the grouping's deliberate over-splitting produces most often: one pile that is
	 * mostly one person plus a stranger, and one pile that is the same person again.
	 *
	 * Groups have no names, so there is nothing to type and nothing to search. What identifies one
	 * is its faces, so the list is the faces, largest group first, and the current group is not in
	 * it: moving faces into the group they are already in is the one choice that cannot mean
	 * anything.
	 */
	import { ConfirmDialog, Empty, Pressable, Scroller, Skeleton } from '$lib/components/common';
	import { PILES_PER_PAGE, cropUrl, faceGroups, type FaceGroup } from '$lib/people/faces.svelte';

	interface Props {
		open?: boolean;
		/** How many faces are being moved. Said in the question, so nobody has to remember. */
		count: number;
		/** The group they are in now, left out of the choices. */
		fromPileId: string;
		/** Null means a group of their own. */
		onmove: (pileId: string | null) => void;
	}

	let { open = $bindable(false), count, fromPileId, onmove }: Props = $props();

	let groups = $state<FaceGroup[]>([]);
	let loading = $state(false);
	/** Undefined until something is picked, which is what keeps the button from acting on a guess. */
	let chosen = $state<string | null | undefined>(undefined);

	/* Loaded when the dialog opens rather than with the screen behind it. This is a list of every
	   other group on the install and most sessions never open it. */
	$effect(() => {
		if (!open) return;
		chosen = undefined;
		loading = true;
		void faceGroups('open', { limit: PILES_PER_PAGE, offset: 0 }).then((answer) => {
			groups = answer.groups.filter((group) => group.id !== fromPileId);
			loading = false;
		});
	});

	const noun = $derived(count === 1 ? '1 face' : `${counted(count)} faces`);
</script>

<ConfirmDialog
	bind:open
	title={`Where should ${noun} go?`}
	consequence="They keep their pictures either way. A group you arrange by hand stays as you left it: grouping them again won't take it apart, and neither will scanning those files again."
	confirmLabel="Move them"
	destructive={false}
	onconfirm={() => chosen !== undefined && onmove(chosen)}
>
	{#snippet extra()}
		<div class="choices">
			<Pressable
				class="choice new"
				feedback="none"
				radius="md"
				picked={chosen === null}
				onclick={() => (chosen = null)}
			>
				<span class="label">A group of their own</span>
				<span class="quiet small">Splits them out of this one.</span>
			</Pressable>

			{#if loading}
				<Skeleton lines={3} />
			{:else if groups.length === 0}
				<Empty scope="block">
					There's no other group to move them into. A group of their own is the only choice.
				</Empty>
			{:else}
				<Scroller>
					<ul>
						{#each groups as group (group.id)}
							<li>
								<Pressable
									class="choice"
									feedback="none"
									radius="md"
									picked={chosen === group.id}
									onclick={() => (chosen = group.id)}
								>
									<span class="faces">
										{#each group.faces.slice(0, 4) as face (face.track_id)}
											<img src={cropUrl(face)} alt="" loading="lazy" />
										{/each}
									</span>
									<span class="quiet small">
										{group.size === 1 ? '1 face' : `${counted(group.size)} faces`}
									</span>
								</Pressable>
							</li>
						{/each}
					</ul>
				</Scroller>
				<p class="quiet small">
					The largest groups first. Only the first {PILES_PER_PAGE} are listed — a library that has been
					swept has a group for every face that joined nothing.
				</p>
			{/if}
		</div>
	{/snippet}
</ConfirmDialog>

<style>
	.choices {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		max-inline-size: 46ch;
	}

	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}

	/* A group you pick, drawn as the card it is: faces, a name, a count. `Pressable` rather than the
	   shared button, because what decides the size is what is inside it. `:global` because the class
	   is handed to a component. */
	.choices :global(.choice) {
		inline-size: 100%;
		display: flex;
		align-items: center;
		gap: var(--space-2);
		padding: var(--space-2);
		border: 1px solid var(--sift-line-strong);
		background: var(--sift-surface-3);
		color: var(--sift-ink);
		font: var(--text-label);
		text-align: start;
	}

	.choices :global(.choice.new) {
		flex-direction: column;
		align-items: flex-start;
		gap: var(--space-1);
	}

	.faces {
		display: flex;
		gap: var(--space-1);
	}

	.faces img {
		inline-size: 40px;
		block-size: 40px;
		border-radius: var(--radius-sm);
		object-fit: cover;
		background: var(--sift-surface-2);
	}

	/* The quieter lines here are dressed by the `.quiet` utility in app.css; this is only the size
	   they are set at. */
	.small {
		font: var(--text-body-sm);
	}

	.label {
		font: var(--text-label);
	}
	/*
	 * The cap moves onto the box that SCROLLS, and the rule is `:global` because that box is rendered
	 * by the shared region rather than written here. Left on the content, a `max-block-size` with no
	 * `overflow` of its own simply CLIPS, and a scoped rule aimed at somebody else's element
	 * matches nothing at all, silently, which is the trap this shape keeps setting.
	 */
	.choices :global(.scroll-root) {
		max-block-size: 40vh;
	}
</style>
