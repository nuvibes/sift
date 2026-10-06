<script lang="ts">
	/*
	 * One unnamed group that may be somebody, face by face: the review a "these groups may be her"
	 * card opens, the same way a person's card opens her suggested faces.
	 *
	 * The card asks about whole groups; this page asks about each face in one of them, which is
	 * what somebody does when the group is close but not certainly her. Every face has its own Yes
	 * and No, and the page has one answer for everything on it.
	 *
	 * The answers are the card's own doors, so nothing here is a second way to say the same thing:
	 *
	 *   Yes on a face, or on the page:  `confirmGroups` with those faces. They are confirmed (they
	 *                                   are the ones somebody looked at) and the rest of the group
	 *                                   is offered as questions about her, which is where the review
	 *                                   carries on: her own Needs your input.
	 *   No on a face:                   `rejectFace`. That face is not her, remembered, and it stays
	 *                                   in its group for somebody to name.
	 *   No, none of this group:         `refuseGroups`, the card's own No for this one group.
	 *
	 * The faces are the group's own page (`faceGroup`), read as the group screen reads them, and the
	 * person is read by id, so the page needs nothing the card has not already got.
	 */
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { untrack } from 'svelte';

	import { isMissing } from '$lib/api/client';
	import { openAsset } from '$lib/player/asset-view';
	import { Button, Empty, Pressable, Problem, Skeleton, Tooltip } from '$lib/components/common';
	import Pager from '$lib/components/common/Pager.svelte';
	import Answers from '$lib/components/organize/Answers.svelte';
	import OrganizeHeader from '$lib/components/organize/OrganizeHeader.svelte';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import { counted } from '$lib/entity/entity-counts';
	import {
		FACES_PER_PAGE,
		confirmGroups,
		cropUrl,
		faceGroup,
		rejectFace,
		rejectGroups,
		type Sighting
	} from '$lib/people/faces.svelte';
	import { organizeCrumbs } from '$lib/organize/bands';
	import { decided, heldBoard } from '$lib/organize/organize.svelte';
	import { people } from '$lib/people/people.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import { thing } from '$lib/components/common/toast-pieces';

	/** The tab the card is on, which this page belongs to for its trail. */
	const FROM = 'faces';

	const personId = $derived(page.params.person ?? '');
	const pileId = $derived(page.params.pile ?? '');

	let name = $state<string | null>(null);
	let faces = $state<Sighting[]>([]);
	let total = $state(0);
	let offset = $state(0);
	let loading = $state(true);
	let missing = $state(false);
	let failed = $state(false);
	/** The face being answered, or `page` for the page's own answer. One at a time. */
	let busy = $state<string | null>(null);

	/* What the page is about, in the card's own words: a count of faces and who they may be. */
	const heading = $derived(
		name === null
			? 'Faces that may be one person'
			: `${total === 1 ? 'One face' : `${counted(total)} faces`} that may be ${name}`
	);

	/* Who each face's answer names: the person, or "them" until the name has been read. One value,
	   so each answer is one label that starts with its Yes or No. */
	const who = $derived(name ?? 'them');

	/* Where the rest of the review goes once a Yes has offered the others to her. */
	const herReview = $derived(
		`/organize/known-people/${encodeURIComponent(personId)}?show=suggested&via=${FROM}`
	);

	async function load() {
		failed = false;
		try {
			const [person, group] = await Promise.all([
				name === null ? people.one(personId).catch(() => null) : Promise.resolve(null),
				faceGroup(pileId, { limit: FACES_PER_PAGE, offset })
			]);
			if (person) name = person.name;
			faces = group.group.faces;
			total = group.total;
			missing = false;
		} catch (error) {
			missing = isMissing(error);
			failed = !missing;
			faces = [];
			total = 0;
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		void personId;
		void pileId;
		void offset;
		untrack(() => void load());
	});

	/* A change elsewhere (a rename, a face decided on another screen) re-reads in place; a pile gone
	   since says so, and a failed re-read leaves the faces drawn. Not while an answer is on its way. */
	reloadOnLibraryChange(() => void reread());
	async function reread() {
		if (loading || busy !== null) return;
		const asked = { personId, pileId, offset };
		try {
			const [person, group] = await Promise.all([
				people.one(personId).catch(() => null),
				faceGroup(pileId, { limit: FACES_PER_PAGE, offset })
			]);
			if (asked.personId !== personId || asked.pileId !== pileId || asked.offset !== offset) return;
			if (person && person.name !== name) name = person.name;
			if (JSON.stringify(group.group.faces) !== JSON.stringify(faces)) faces = group.group.faces;
			total = group.total;
		} catch (error) {
			if (!isMissing(error)) return;
			missing = true;
			faces = [];
			total = 0;
		}
	}

	/* A Yes on some of the faces: those are confirmed, and the rest of the group becomes questions
	   about her, so the review carries on in her own list when anything was offered. */
	async function agree(ids: readonly string[], who: string) {
		if (busy || ids.length === 0) return;
		busy = who;
		try {
			const answer = await confirmGroups(personId, [pileId], ids);
			const named =
				answer.changed === 1 ? 'One face is named' : `${counted(answer.changed)} faces are named`;
			const more =
				answer.offered > 0 ? `. ${counted(answer.offered)} more are waiting for your answer` : '';
			decided(`${named}${more}`, answer.decision_id ?? null);
			await goto(answer.offered > 0 ? herReview : `/organize/${FROM}`);
		} catch {
			toasts.show("Those couldn't be confirmed", { tone: 'error' });
		} finally {
			busy = null;
		}
	}

	/* No on one face: that face is not her. It leaves this page and stays in its group. */
	async function refuse(face: Sighting) {
		if (busy) return;
		busy = face.track_id;
		try {
			await rejectFace(face.track_id, personId);
			toasts.show(
				[
					"That face won't be suggested for ",
					name ? thing('person', personId, name) : 'them',
					' again'
				],
				{ tone: 'success' }
			);
			faces = faces.filter((one) => one.track_id !== face.track_id);
			total = Math.max(0, total - 1);
		} catch {
			toasts.show("That face couldn't be refused", { tone: 'error' });
		} finally {
			busy = null;
		}
	}

	/* No for the whole group: the card's own No, about this group alone. */
	async function refuseAll() {
		if (busy) return;
		busy = 'page';
		try {
			const answer = await rejectGroups(personId, [pileId]);
			decided(
				answer.changed === 1
					? "That face won't be suggested for them again"
					: `${counted(answer.changed)} faces won't be suggested for them again`,
				answer.decision_id ?? null
			);
			await goto(`/organize/${FROM}`);
		} catch {
			toasts.show("Those suggestions couldn't be discarded", { tone: 'error' });
		} finally {
			busy = null;
		}
	}

	const onPage = $derived(faces.map((face) => face.track_id));
</script>

<svelte:head><title>{heading}</title></svelte:head>

<PageFrame
	crumbs={organizeCrumbs(heldBoard.found?.queues ?? [], FROM, heading, undefined, FROM)}
	footer={total > FACES_PER_PAGE ? pagerFooter : undefined}
>
	{#snippet header()}
		<OrganizeHeader
			queue={FROM}
			here={heading}
			controls={faces.length > 0 ? pageAnswers : undefined}
		></OrganizeHeader>
	{/snippet}

	{#if loading && faces.length === 0}
		<Skeleton lines={3} />
	{:else if missing}
		<Empty scope="page" icon="face" title="This group has been answered">
			It was named, discarded or regrouped since the card was drawn.
			<a href={`/organize/${FROM}`}>Back to Faces</a>
		</Empty>
	{:else if failed}
		<Problem message="That couldn't be loaded. Try again in a moment." />
	{:else if faces.length === 0}
		<Empty scope="page" icon="face" title="Nothing left to review">
			Every face in this group has been answered.
			<a href={`/organize/${FROM}`}>Back to Faces</a>
		</Empty>
	{:else}
		<ul class="faces" aria-label="Faces in this group">
			{#each faces as face (face.track_id)}
				<li>
					<!-- The crop opens the file at the moment the face was found, which is usually the
					     only way to tell. -->
					<Pressable
						class="face"
						feedback="none"
						radius="md"
						aria-label="Open the file where this face was found"
						onclick={() => openAsset(face.asset_id, [], face.picture_ms)}
					>
						<img src={cropUrl(face)} alt="" loading="lazy" />
					</Pressable>
					<span class="pair">
						<Tooltip label={`Not ${who}`}>
							<Button
								tone="ghost"
								size="small"
								icon="close"
								aria-label={`No, this face isn't ${who}`}
								busy={busy === face.track_id}
								disabled={busy !== null}
								onclick={() => void refuse(face)}
							/>
						</Tooltip>
						<Tooltip label={`This is ${who}`}>
							<Button
								tone="secondary"
								size="small"
								icon="check"
								aria-label={`Yes, this face is ${who}`}
								busy={busy === face.track_id}
								disabled={busy !== null}
								onclick={() => void agree([face.track_id], face.track_id)}
							/>
						</Tooltip>
					</span>
				</li>
			{/each}
		</ul>
	{/if}
</PageFrame>

<!-- The page's own answer, at the end of the title's line: Yes for every face on this page, and
     behind the chevron the No for the whole group. -->
{#snippet pageAnswers()}
	<Answers
		yes={{
			// "all" only when the page holds the whole group; a page of a larger group says how many it holds.
			label:
				onPage.length === 1
					? 'Yes, this face'
					: onPage.length < total
						? `Yes, these ${counted(onPage.length)}`
						: `Yes, all ${counted(onPage.length)}`,
			icon: 'check',
			run: () => void agree(onPage, 'page')
		}}
		rest={[{ label: 'No, none of this group', icon: 'close', run: () => void refuseAll() }]}
		about={name ?? 'this group'}
		busy={busy === 'page'}
		disabled={busy !== null}
	/>
{/snippet}

{#snippet pagerFooter()}
	<Pager
		{offset}
		shown={faces.length}
		{total}
		noun="faces"
		onfirst={() => (offset = 0)}
		onprevious={() => (offset = Math.max(0, offset - FACES_PER_PAGE))}
		onnext={() => (offset = Math.min(offset + FACES_PER_PAGE, Math.max(0, total - 1)))}
		onlast={() => (offset = Math.max(0, Math.floor((total - 1) / FACES_PER_PAGE) * FACES_PER_PAGE))}
		onjump={(position) =>
			(offset = Math.floor(Math.max(0, position - 1) / FACES_PER_PAGE) * FACES_PER_PAGE)}
	/>
{/snippet}

<style>
	.faces {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(7rem, 1fr));
		gap: var(--space-2);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.faces li {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		min-inline-size: 0;
	}

	/* The crop, square, filling its column. `:global` because the class is handed to `Pressable`. */
	.faces :global(.face) {
		display: block;
		inline-size: 100%;
	}

	.faces :global(.face img) {
		display: block;
		inline-size: 100%;
		aspect-ratio: 1;
		border-radius: var(--radius-sm);
		object-fit: cover;
	}

	/* The face's own No and Yes, under it, the Yes on the right. */
	.pair {
		display: flex;
		justify-content: flex-end;
		gap: var(--space-1);
	}
</style>
