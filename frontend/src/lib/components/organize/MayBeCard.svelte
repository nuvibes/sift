<script lang="ts">
	/*
	 * "These groups may be her": one card per person, the unnamed groups that may be them, closest
	 * first, drawn on Needs your input beside her standing questions.
	 *
	 * ## A group, not a face
	 *
	 * Every other card here compares one face with a person, so a group whose faces each fall just
	 * short of the line for asking would never come up at all, however plainly the group as a whole looked
	 * like her. The server compares the group's middle with her pictures (and adds the groups her
	 * folder proposes), and this card asks about them together. See `FaceService.groups_that_may_be`.
	 *
	 * ## The tick is the answer's scope
	 *
	 * Each group starts ticked or not as the server says (close enough, or proposed by her folder),
	 * and somebody unticks any that are not her. Yes is about the TICKED groups: the faces each row
	 * shows are confirmed (somebody looked at them) and the rest of each group is asked about.
	 * No is about the whole card: none of these groups is her, and every face in them is refused
	 * as her, which is what keeps the question from coming back after the groups are rebuilt.
	 *
	 * The ticks are held here and nowhere else: they describe one look at one card, and a reload of
	 * the list after an answer draws the groups that are left with the server's own starting ticks.
	 *
	 * ## Looking at a group face by face
	 *
	 * A group's crops open its review ("47 faces that may be her", `MayBeReview`), the same kind of
	 * face-by-face review a person's card opens, where each face has its own Yes and No. The card's
	 * menu opens the closest group's.
	 *
	 * ## Why a group is on the card, said once
	 *
	 * The reasons are about the card as much as about each group (her folder, a stash-box's
	 * pictures), so each is said once above the groups; a group row carries only its own numbers.
	 */
	import { goto } from '$app/navigation';

	import FaceCovers from '$lib/components/faces/FaceCovers.svelte';
	import Answers from '$lib/components/organize/Answers.svelte';
	import DecisionCard from '$lib/components/organize/DecisionCard.svelte';
	import { Checkbox, Pressable } from '$lib/components/common';
	import { counted } from '$lib/entity/entity-counts';
	import {
		FACES_ON_A_GROUP_ROW,
		type MayBeGroup,
		type ToCheckItem
	} from '$lib/people/faces.svelte';

	interface Props {
		/** The `may_be` card: the person is its id and name, the groups are `groups`. */
		card: ToCheckItem;
		/** Some card on the list is answering, so none may be pressed. */
		busy: boolean;
		/** This card is the one answering. */
		answering: boolean;
		/** Yes: the ticked groups, and the faces this card showed of them. */
		onyes: (pileIds: string[], trackIds: string[]) => void;
		/** No: every group on the card. */
		onno: (pileIds: string[]) => void;
	}

	let { card, busy, answering, onyes, onno }: Props = $props();

	/** What somebody changed from the server's starting tick, by group. */
	let changed = $state<Record<string, boolean>>({});

	const name = $derived(card.person_name ?? '');

	function chosen(group: MayBeGroup): boolean {
		return changed[group.pile_id] ?? group.ticked;
	}

	function toggle(group: MayBeGroup): void {
		changed[group.pile_id] = !chosen(group);
	}

	const ticked = $derived(card.groups.filter((group) => chosen(group)));

	/** The card's question, whole: the verb and the noun agree with the count. */
	const asking = $derived(
		card.groups.length === 1
			? `This group may be ${name}`
			: `These ${card.groups.length.toLocaleString()} groups may be ${name}`
	);

	/** What to do with the ticks, in words that fit how many groups there are. */
	const guide = $derived(
		card.groups.length === 1
			? `Deselect it if it isn't ${name}.`
			: `Closest first. Deselect any that aren't ${name}.`
	);

	function faces(count: number): string {
		return count === 1 ? '1 face' : `${count.toLocaleString()} faces`;
	}

	/* How close the group as a whole comes to her, as a percentage: the way every other surface in
	   this feature says it. Only where a likeness is the reason: her own pictures, or a stash-box's
	   pictures of her. A group proposed by her folder alone carries a number nobody asked about, and
	   printed beside the folder's sentence it would read as the evidence. */
	function likeness(group: MayBeGroup): string | null {
		if (group.likeness === null || group.likeness === undefined) return null;
		const measured = group.reasons.some(
			(reason) => reason.kind === 'likeness' || reason.kind === 'stash-box'
		);
		if (!measured) return null;
		return `${Math.round(group.likeness * 100)}%`;
	}

	/*
	 * Why the groups are on the card, in words, said once above them: one sentence per folder that
	 * proposed any of them, and one where the likeness is to a stash-box's pictures rather than to
	 * hers. The server marks a group that looks like somebody known by starter pictures alone
	 * (`STARTER_REASON`), which is weaker evidence than her own faces, and somebody answering needs
	 * to know that about the number. The reason names the boxes the pictures came from, and the
	 * sentence says them; "a stash-box" is only for pictures filed before a starter kept its box.
	 */
	const reasons = $derived.by(() => {
		const folders = new Set<string>();
		const boxes = new Set<string>();
		let starters = false;
		for (const group of card.groups) {
			for (const reason of group.reasons) {
				if (reason.kind === 'folder' && reason.folder_name) folders.add(reason.folder_name);
				if (reason.kind === 'stash-box') {
					starters = true;
					for (const box of reason.box_names ?? []) boxes.add(box);
				}
			}
		}
		const said = [...folders].map(
			(folder) => `Some of these files are in the folder ${folder}, which Sift added to ${name}.`
		);
		if (starters) {
			// "FansDB's", "FansDB's and StashDB's", or "a stash-box's" where no box is recorded.
			const named = [...boxes].sort();
			const last = named.pop() ?? 'a stash-box';
			const before = named.length ? `${named.map((box) => `${box}'s`).join(', ')} and ` : '';
			said.push(
				`Compared with ${before}${last}'s pictures of ${name}, not with faces you confirmed.`
			);
		}
		return said;
	});

	/* A group's own share of a folder that proposed it: "41 of its 47 files are in that folder". */
	function inFolder(group: MayBeGroup): string | null {
		const reason = group.reasons.find((one) => one.kind === 'folder' && one.folder_name);
		if (!reason) return null;
		return `${counted(reason.in_folder ?? 0)} of its ${counted(reason.group_files ?? 0)} files are in ${reason.folder_name}`;
	}

	/* The group's own review, face by face: who it may be, and which group. */
	function reviewHref(group: MayBeGroup): string {
		return `/organize/may-be/${encodeURIComponent(card.id)}/${encodeURIComponent(group.pile_id)}`;
	}

	function yes(): void {
		onyes(
			ticked.map((group) => group.pile_id),
			ticked.flatMap((group) => group.faces.map((face) => face.track_id))
		);
	}
</script>

<DecisionCard>
	<!-- Why these groups are here, said once for the card rather than under every group. -->
	{#each reasons as sentence (sentence)}
		<p class="why">{sentence}</p>
	{/each}

	<ul class="groups">
		{#each card.groups as group (group.pile_id)}
			<li class="group">
				<!-- The whole row is the control and the box only reports it: a 16-pixel target is a
				     thing to aim at. The same shape the tick rows on the dialogs wear. -->
				<Pressable
					class="tick"
					feedback="wash"
					radius="md"
					aria-pressed={chosen(group)}
					disabled={busy}
					onclick={() => toggle(group)}
				>
					<Checkbox state={chosen(group) ? 'on' : 'off'} mark />
					<span
						>{faces(group.size)}{#if likeness(group)}<span class="sure"
								>&nbsp;&middot; {likeness(group)}</span
							>{/if}</span
					>
				</Pressable>
				<!-- The crops open this group's own review, face by face, where the question "is
				     this her?" is asked of each face. These are the faces a Yes confirms, so every
				     one sent is drawn. -->
				<FaceCovers
					faces={group.faces}
					most={FACES_ON_A_GROUP_ROW}
					href={reviewHref(group)}
					label={`Review the ${faces(group.size)} that may be ${name}, face by face`}
				/>
				{#if inFolder(group)}
					<p class="fact">{inFolder(group)}</p>
				{/if}
			</li>
		{/each}
	</ul>

	{#snippet question()}{asking}{/snippet}
	<!-- The words fit the count: one group has no order to be in and no "any" to choose among. -->
	{#snippet detail()}{guide}{/snippet}
	<!-- The affirmative leads and the rest sit behind the chevron, the shape every question here
	     wears: the No about the whole card, ticked or not (none of these groups is them), and the
	     closest group's review. -->
	{#snippet answers()}
		<Answers
			yes={{ label: 'Yes', icon: 'check', run: yes, disabled: ticked.length === 0 }}
			rest={[
				{
					label: 'No',
					icon: 'close',
					run: () => onno(card.groups.map((group) => group.pile_id))
				},
				{
					label: 'Review each face',
					icon: 'arrow_forward',
					run: () => void goto(reviewHref(card.groups[0]))
				}
			]}
			about={name}
			busy={answering}
			disabled={busy}
		/>
	{/snippet}
</DecisionCard>

<style>
	.groups {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.group {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		min-inline-size: 0;
	}

	.group :global(.tick) {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		inline-size: 100%;
		padding: var(--space-1) var(--space-2);
		text-align: start;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	/* How close the group comes: a shade nearer the ink than the count, the one number worth
	   reading first, as on the question cards beside this one. */
	.sure {
		color: var(--sift-ink);
	}

	.why,
	.fact {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
		overflow-wrap: anywhere;
	}
</style>
