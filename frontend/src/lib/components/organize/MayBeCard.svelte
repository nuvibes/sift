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
	 * pictures), so each is said once, under the question; a group carries only its own numbers.
	 *
	 * ## One height with the question cards
	 *
	 * The groups share a question card's strip of two rows of six, a block per group, and each
	 * tick stands under its block.
	 */
	import { goto } from '$app/navigation';

	import FaceCovers from '$lib/components/faces/FaceCovers.svelte';
	import Answers from '$lib/components/organize/Answers.svelte';
	import DecisionCard from '$lib/components/organize/DecisionCard.svelte';
	import { Checkbox, Pressable, Tooltip } from '$lib/components/common';
	import { counted } from '$lib/entity/entity-counts';
	import { CROPS_ON_A_CARD, type MayBeGroup, type ToCheckItem } from '$lib/people/faces.svelte';

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

	/** Groups a card draws. */
	const GROUPS_ON_A_CARD = 3;

	/* The closest few, so every card stays near one height; the rest are counted under them and
	   come up as these are answered. Yes and No are about the groups drawn, never the unseen. */
	const shown = $derived(card.groups.slice(0, GROUPS_ON_A_CARD));
	const unseen = $derived(card.groups.length - shown.length);

	/* The strip's six columns shared between the groups drawn, and the cells each block holds. */
	const across = $derived(shown.length === 3 ? 2 : shown.length === 2 ? 3 : 6);
	const cells = $derived(CROPS_ON_A_CARD / Math.max(1, shown.length));

	/** What a group's block draws, so what a Yes confirms: one cell is the counter's. */
	function drawn(group: MayBeGroup) {
		return group.faces.slice(
			0,
			Math.max(group.size, group.faces.length) > cells ? cells - 1 : cells
		);
	}

	const ticked = $derived(shown.filter((group) => chosen(group)));

	/** The card's question, whole: the verb and the noun agree with the count. */
	const asking = $derived(
		shown.length === 1
			? `This group may be ${name}`
			: `These ${shown.length.toLocaleString()} groups may be ${name}`
	);

	/** Her own Needs your input, where the groups not drawn are reached. */
	const herInput = $derived(
		`/organize/known-people/${encodeURIComponent(card.id)}?show=suggested&via=faces`
	);

	/** What to do with the ticks, in words that fit how many groups there are. */
	const guide = $derived(
		shown.length === 1
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
		for (const group of shown) {
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

	/* The words under the question: what to do with the ticks, then why the groups are here. */
	const told = $derived([guide, ...reasons].join(' '));

	/* A group's own share of a folder that proposed it: "41 of its 47 files are in that folder". */
	function inFolder(group: MayBeGroup): string | null {
		const reason = group.reasons.find((one) => one.kind === 'folder' && one.folder_name);
		if (!reason) return null;
		return `${counted(reason.in_folder ?? 0)} of its ${counted(reason.group_files ?? 0)} files are in ${reason.folder_name}`;
	}

	/* A tick's whole line, where its cell cuts it short, with the group's share of her folder. */
	function tickSays(group: MayBeGroup): string {
		const share = likeness(group);
		return [`${faces(group.size)}${share ? ` \u00b7 ${share}` : ''}`, inFolder(group)]
			.filter(Boolean)
			.join('. ');
	}

	/* The group's own review, face by face: who it may be, and which group. */
	function reviewHref(group: MayBeGroup): string {
		return `/organize/may-be/${encodeURIComponent(card.id)}/${encodeURIComponent(group.pile_id)}`;
	}

	function yes(): void {
		onyes(
			ticked.map((group) => group.pile_id),
			ticked.flatMap((group) => drawn(group).map((face) => face.track_id))
		);
	}
</script>

<DecisionCard opens={herInput} detailLines={2}>
	<!-- Each block opens its group's review, face by face. -->
	<div class="strips" class:pair={shown.length === 2} class:three={shown.length === 3}>
		{#each shown as group (group.pile_id)}
			<FaceCovers
				faces={drawn(group)}
				total={group.size}
				most={cells}
				{across}
				href={reviewHref(group)}
				label={`Review the ${faces(group.size)} that may be ${name}, face by face`}
			/>
		{/each}
	</div>
	<!-- The whole cell is the control: a 16-pixel box is a thing to aim at. -->
	<div class="ticks" class:pair={shown.length === 2} class:three={shown.length === 3}>
		{#each shown as group (group.pile_id)}
			<Tooltip label={tickSays(group)} stretch shrinks>
				<Pressable
					class="tick"
					feedback="wash"
					radius="md"
					aria-pressed={chosen(group)}
					disabled={busy}
					onclick={() => toggle(group)}
				>
					<Checkbox state={chosen(group) ? 'on' : 'off'} mark />
					<span class="count"
						>{faces(group.size)}{#if likeness(group)}<span class="sure"
								>&nbsp;&middot; {likeness(group)}</span
							>{/if}</span
					>
				</Pressable>
			</Tooltip>
		{/each}
	</div>

	{#snippet question()}{asking}{/snippet}
	<!-- The words fit the count: one group has no order to be in and no "any" to choose among. -->
	{#snippet detail()}<Tooltip label={told}><span class="told">{told}</span></Tooltip>{/snippet}
	<!-- The affirmative leads and the rest sit behind the chevron, the shape every question here
	     wears: the No about the whole card, ticked or not (none of these groups is them), and the
	     closest group's review. -->
	{#snippet answers()}
		<div class="answer-line">
			<Answers
				yes={{ label: 'Yes', icon: 'check', run: yes, disabled: ticked.length === 0 }}
				rest={[
					{
						label: 'No',
						icon: 'close',
						run: () => onno(shown.map((group) => group.pile_id))
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
			{#if unseen > 0}
				<a class="unseen" href={herInput}>and {unseen.toLocaleString()} more</a>
			{/if}
		</div>
	{/snippet}
</DecisionCard>

<style>
	/* The strip's six columns, shared: the gap between two blocks is the gap between two crops. */
	.strips,
	.ticks {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: var(--space-1);
	}

	.strips.pair,
	.ticks.pair {
		grid-template-columns: repeat(2, minmax(0, 1fr));
	}

	.strips.three,
	.ticks.three {
		grid-template-columns: repeat(3, minmax(0, 1fr));
	}

	.ticks {
		min-block-size: var(--control-height-sm);
	}

	.ticks :global(.tick) {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		min-inline-size: 0;
		padding: var(--space-1);
		text-align: start;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	.count {
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	/* How close the group comes: a shade nearer the ink than the count, the one number worth
	   reading first, as on the question cards beside this one. */
	.sure {
		color: var(--sift-ink);
	}

	/* Two lines, the room a question card's detail keeps; the whole of it is the tooltip. */
	.told {
		display: -webkit-box;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		overflow: hidden;
	}

	.answer-line {
		display: flex;
		align-items: center;
		gap: var(--space-3);
	}

	.unseen {
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}
</style>
