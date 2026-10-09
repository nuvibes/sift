<script lang="ts">
	/* "These groups may be her": the unnamed groups that may be one person, closest first. Yes takes
	 * the ticked groups (the shown faces confirmed, the rest asked), No refuses every group as her;
	 * the ticks are held here only. A group's crops open its face-by-face review. */
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

	/* The closest few, so cards stay near one height; Yes and No cover only those drawn. */
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

	/* The group's likeness as a percentage, only where likeness is the reason. */
	function likeness(group: MayBeGroup): string | null {
		if (group.likeness === null || group.likeness === undefined) return null;
		const measured = group.reasons.some(
			(reason) => reason.kind === 'likeness' || reason.kind === 'stash-box'
		);
		if (!measured) return null;
		return `${Math.round(group.likeness * 100)}%`;
	}

	/*
	 * Why the groups are here, once: each proposing folder, or the boxes whose starter pictures
	 * matched.
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
	<!-- Yes leads; the No for the whole card and the closest group's review behind the chevron. -->
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

	/* The likeness, a shade nearer the ink: the number worth reading first. */
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
