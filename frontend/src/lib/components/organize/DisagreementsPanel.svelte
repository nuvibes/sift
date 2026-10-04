<script lang="ts">
	/*
	 * The names a pass filed that the face in the file does not agree with, gathered by person.
	 *
	 * The one question in this group that runs backwards: everything else under Faces asks who
	 * somebody is; this asks whether an answer already given was right. A folder, a filename or a
	 * stash-box put a name on a file, the file holds exactly one face, and that face is too far
	 * from what Sift knows the person looks like to offer the match.
	 *
	 * BY PERSON, because that is the shape the rows come in. A pass files a whole folder at once, so
	 * its mistakes arrive by the hundred under one name: a thousand files about a handful of people,
	 * nearly all from a folder name. One card per file would be a thousand presses of the same question;
	 * this is the people down one side (`DisagreementsByPerson`) and the chosen person's faces as a
	 * wall beside them, closest to her first, with a Yes and a No on every face and over the page or
	 * all of hers.
	 *
	 * Closest first so the faces that may be her after all (turned away, in poor light) are the first
	 * page somebody sees and answers Yes to, and what is left behind them is what a No over all of
	 * hers is for. Nothing is taken off without a press: a face too covered to read looks like a
	 * stranger to the arithmetic too.
	 *
	 * Both answers are receipts with an Undo: a Yes is the naming every faces screen writes, a No
	 * takes her off the files and remembers it, so the folder does not put her back.
	 *
	 * The question is asked the way every other faces question is ("Do these faces look like
	 * her?"), so a Yes means one thing. Under a heading saying she did NOT look like them, a Yes would
	 * read both ways. Each answer says whose the faces are and what happens to them: a Yes says
	 * they are hers, a No says she is taken off the files, which also ends the question.
	 *
	 * Faces are picked by the gesture every wall of tiles uses (`TileGesture`: a press held, then
	 * drawn across the wall), and a pick is what the answers over the page answer, as "these 12".
	 *
	 * Where the name came from is the server's sentence, folders named and linked (`filed`), drawn
	 * as it comes; the line builds none of its own.
	 *
	 * Which person is showing is in the address (`?person=`), so Back steps between people and a
	 * link opens on the one it was sent from. With none named, the one with the most files.
	 */
	import { goto } from '$app/navigation';
	import { page as address } from '$app/state';
	import { onDestroy, untrack } from 'svelte';

	import { openAsset } from '$lib/player/asset-view';
	import {
		Button,
		ConfirmDialog,
		Empty,
		Pressable,
		Problem,
		SectionHeading,
		Selection,
		Skeleton,
		TILE_ID,
		TileGesture,
		Tooltip
	} from '$lib/components/common';
	import type { OnPaging } from '$lib/components/common/Pager.svelte';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import Answers from '$lib/components/organize/Answers.svelte';
	import DisagreementsByPerson from '$lib/components/organize/DisagreementsByPerson.svelte';
	import Said from '$lib/components/organize/Said.svelte';
	import { counted } from '$lib/entity/entity-counts';
	import { cropUrl } from '$lib/people/faces.svelte';
	import { CardPaging } from '$lib/grid/cards.svelte';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import {
		DISAGREEMENTS_PER_PAGE as PAGE,
		answerDisagreements,
		disagreeingPeople,
		disagreementsOf,
		filedFrom,
		type DisagreeingPerson,
		type Disagreement,
		type DisagreementScope
	} from '$lib/organize/disagreements';
	import { answered, decided } from '$lib/organize/organize.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';

	/** Where the pager goes: the frame's foot, drawn by the route. See `PagerProps`. */
	let { onpaging }: { onpaging?: OnPaging } = $props();

	let people = $state<DisagreeingPerson[]>([]);
	let loading = $state(true);
	let failed = $state(false);

	/* The person the address names, while she is still on the list; else the one with the most. */
	const asked = $derived(address.url.searchParams.get('person'));
	const current = $derived(people.find((one) => one.person_id === asked) ?? people[0] ?? null);
	const name = $derived(current?.person_name ?? '');

	let faces = $state<Disagreement[]>([]);
	let total = $state(0);
	/* Whose faces `faces` are, so a reload for a newly chosen person draws nothing of the last. */
	let facesOf = $state<string | null>(null);
	/* A fixed page of crops, as her review screen draws them: nothing here is measured. */
	const paging = new CardPaging(PAGE);

	/* The row whose answer is in flight: a file's id, 'page' or 'all'. One at a time. */
	let busy = $state<string | null>(null);
	/* An answer over all of hers waiting on its confirmation. */
	let pendingAll = $state<boolean | null>(null);
	let confirmAll = $state(false);

	async function loadPeople() {
		try {
			const answer = await disagreeingPeople();
			people = answer.people;
			failed = false;
		} catch {
			failed = true;
		} finally {
			loading = false;
		}
	}

	async function loadFaces(personId: string, offset: number) {
		try {
			const answer = await disagreementsOf(personId, { limit: PAGE, offset });
			/* Overtaken by another person or page, which finishes this one's work. */
			if (personId !== current?.person_id || offset !== paging.offset) return;
			faces = answer.items;
			total = answer.total;
			facesOf = personId;
			/* The last page emptied by its answers: step back to the one before it. */
			if (answer.items.length === 0 && answer.total > 0 && offset > 0) {
				paging.offset = Math.max(0, Math.floor((answer.total - 1) / PAGE) * PAGE);
			}
		} catch {
			toasts.show("Those faces couldn't be loaded", { tone: 'error' });
		}
	}

	/* The people, again whenever anything is answered anywhere. */
	$effect(() => {
		void answered.stamp;
		untrack(() => void loadPeople());
	});

	/* And her faces, whenever she, the page or anything answered moves. */
	$effect(() => {
		const personId = current?.person_id;
		const offset = paging.offset;
		void answered.stamp;
		if (personId) untrack(() => void loadFaces(personId, offset));
	});

	/* And when a share or a restrict moves: a row about a file this account may no longer see has
	   to leave, and nothing else announces that. */
	reloadOnLibraryChange(() => void loadPeople());

	$effect(() => {
		/* A noun, as `PagerProps.noun` asks, so an empty tab reads properly. */
		onpaging?.(paging.asPager(faces.length, total, 'files'));
	});
	onDestroy(() => onpaging?.(null));

	/** Show another person's faces, from her first page. */
	function pick(personId: string) {
		if (personId === current?.person_id) return;
		paging.offset = 0;
		const url = new URL(address.url);
		url.searchParams.set('person', personId);
		void goto(url, { keepFocus: true, noScroll: true });
	}

	const shown = $derived(facesOf === current?.person_id ? faces : []);
	const onPage = $derived(shown.map((item) => item.id));

	/*
	 * Faces picked by hand, by the gesture every other wall of tiles uses: a press held, then drawn
	 * across the wall, picks every face it crosses; Ctrl, Cmd or Shift picks one or a run; Escape
	 * lets go. See `TileGesture`, which is the whole of the rules; this wall only says which face
	 * each tile is (`TILE_ID`) and in what order they are drawn.
	 *
	 * Only faces on the page can be picked, and only the ones still on it count, so an answer
	 * that takes some off never leaves the count saying more than the wall shows.
	 */
	const selection = new Selection();
	const gesture = new TileGesture(selection, () => onPage);
	const picked = $derived(selection.ordered(onPage));

	/* Let go when the wall changes underneath: another person or another page. A pick carried
	   across either is faces nobody can see, and the answers would then be about them. */
	const currentId = $derived(current?.person_id ?? null);
	$effect(() => {
		void currentId;
		void paging.offset;
		selection.clear();
	});

	/* What the answers over the page are about: the faces picked, or with none picked the page. */
	const answering = $derived(picked.length > 0 ? picked : onPage);
	const askedScope = $derived<DisagreementScope>(picked.length > 0 ? 'picked' : 'page');

	/** A plain press opens the file at that face; anything about picking is the gesture's. */
	function pressed(item: Disagreement, event: MouseEvent) {
		if (gesture.handled(item.id, event)) return;
		const face = item.faces[0];
		if (face) openAsset(item.id, [], face.picture_ms);
	}

	/** "1 file", "734 files". */
	function files(count: number): string {
		return count === 1 ? '1 file' : `${counted(count)} files`;
	}

	/** Answer some of hers, then say what moved, with the receipt's Undo. */
	async function answer(
		yes: boolean,
		scope: DisagreementScope,
		ids: readonly string[],
		pressed: string
	) {
		const personId = current?.person_id;
		if (busy || !personId) return;
		busy = pressed;
		try {
			const done = await answerDisagreements(personId, yes, scope, ids);
			const moved = done.changed;
			const said = yes
				? moved === 1
					? 'One face is confirmed'
					: `${counted(moved)} faces are confirmed`
				: moved === 1
					? `${name} is no longer on this file`
					: `${name} is no longer on ${counted(moved)} files`;
			selection.clear();
			decided(said, done.decision_id ?? null, { after: loadPeople });
		} catch {
			toasts.show(yes ? "Those couldn't be confirmed" : "Those couldn't be changed", {
				tone: 'error'
			});
		} finally {
			busy = null;
		}
	}

	/** All of hers asks first: it answers for files nobody has scrolled to. */
	function askAll(yes: boolean) {
		pendingAll = yes;
		confirmAll = true;
	}

	/* The answers' words fit the count: "these 60" while more wait on later pages, "these 12"
	   once twelve are picked. Each says whose the faces are and what a No does to the files. */
	const yesWords = $derived(
		answering.length === 1
			? `Yes, this face is ${name}`
			: `Yes, these ${counted(answering.length)} are ${name}`
	);
	const noWords = $derived(
		answering.length === 1
			? `No, take ${name} off this file`
			: `No, take ${name} off these ${counted(answering.length)} files`
	);
</script>

<svelte:window
	onkeydown={(event) => {
		// Ctrl+Z takes back the last thing PICKED, Ctrl+Shift+Z picks it again. It touches no
		// data and never reaches the server. See `TileGesture.undoKeys`.
		if (gesture.undoKeys(event)) {
			event.preventDefault();
			event.stopPropagation();
			return;
		}
		if (event.key === 'Escape' && gesture.escaped(event)) event.stopPropagation();
	}}
/>

<section class="panel" class:stacked={phoneWidth.yes}>
	{#if loading && people.length === 0}
		<Skeleton lines={3} />
	{:else if failed}
		<Problem message="That couldn't be loaded. Try again in a moment." />
	{:else if people.length === 0 || !current}
		<Empty scope="page" icon="person_alert" title="Nothing disagrees">
			A file appears here when its only face doesn't match the person named on it. You can then
			correct a name that came from a folder or a filename.
		</Empty>
	{:else}
		<DisagreementsByPerson {people} current={current.person_id} onpick={pick} />

		<div class="hers">
			<!-- Her heading, with the answers over the page and over all of hers at its end: Yes
			     leads, and the rest wait behind the chevron. -->
			<SectionHeading actions={hersAnswers}>Do these faces look like {name}?</SectionHeading>
			<!-- Where the name came from is the server's sentence, the folders in it linked. A server
			     that sends none is read by the word it does send. -->
			<p class="detail">
				{files(current.count)} &middot; {#if current.filed}<Said
						what={current.filed}
						links={current.filed_links ?? []}
					/>{:else}{filedFrom(current.source)}{/if}. Sift recognized someone else in these. The ones
				that look most like {name} are first.
			</p>

			<ul class="faces" aria-label={`Faces added to ${name}`}>
				{#each shown as item (item.id)}
					{@const face = item.faces[0]}
					<li>
						<!-- The crop opens the file at the moment the face was found, which is usually
						     the only way to tell. -->
						<Pressable
							class="face"
							feedback="none"
							radius="md"
							picked={selection.has(item.id)}
							aria-label="Open the file where this face was found"
							onpointerdown={(event: PointerEvent) => gesture.pressStart(item.id, event)}
							onpointerup={() => gesture.pressEnd()}
							onpointercancel={() => gesture.pressEnd()}
							onclick={(event: MouseEvent) => pressed(item, event)}
							{...{ [TILE_ID]: item.id }}
						>
							{#if face}<img src={cropUrl(face)} alt="" loading="lazy" />{/if}
						</Pressable>
						<span class="pair">
							<Tooltip label={`Take ${name} off this file`}>
								<Button
									tone="ghost"
									size="small"
									icon="close"
									aria-label={`No, take ${name} off this file`}
									busy={busy === item.id}
									disabled={busy !== null}
									onclick={() => void answer(false, 'picked', [item.id], item.id)}
								/>
							</Tooltip>
							<Tooltip label={`This is ${name}`}>
								<Button
									tone="secondary"
									size="small"
									icon="check"
									aria-label={`Yes, this face is ${name}`}
									busy={busy === item.id}
									disabled={busy !== null}
									onclick={() => void answer(true, 'picked', [item.id], item.id)}
								/>
							</Tooltip>
						</span>
					</li>
				{/each}
			</ul>
		</div>
	{/if}
</section>

{#snippet hersAnswers()}
	<Answers
		yes={{
			label: yesWords,
			icon: 'check',
			run: () => void answer(true, askedScope, answering, 'page'),
			disabled: answering.length === 0
		}}
		rest={[
			{
				label: noWords,
				icon: 'close',
				run: () => void answer(false, askedScope, answering, 'page'),
				disabled: answering.length === 0
			},
			{
				label: `Yes, all ${counted(total)} are ${name}`,
				icon: 'check',
				run: () => askAll(true),
				disabled: total === 0
			},
			{
				label: `No, take ${name} off all ${counted(total)} files`,
				icon: 'close',
				run: () => askAll(false),
				disabled: total === 0
			}
		]}
		about={name}
		busy={busy === 'page' || busy === 'all'}
		disabled={busy !== null}
	/>
{/snippet}

<ConfirmDialog
	bind:open={confirmAll}
	title={pendingAll
		? `Confirm all ${counted(total)} faces as ${name}?`
		: `Take ${name} off all ${counted(total)} files?`}
	consequence={pendingAll
		? `Each face is confirmed as ${name}, and Sift learns from each one. You can undo this from History.`
		: `${name} is no longer on these files, and Sift won't add ${name} to them again from the same place. You can undo this from History. ` +
			'Nothing is deleted and no file is touched.'}
	confirmLabel={pendingAll ? 'Confirm all' : `No, take ${name} off`}
	destructive={false}
	onconfirm={() => {
		if (pendingAll !== null) void answer(pendingAll, 'all', [], 'all');
	}}
/>

<style>
	/* The people down one side and her faces beside them, both from the top of the tab. */
	.panel {
		display: grid;
		grid-template-columns: minmax(16rem, 22rem) minmax(0, 1fr);
		gap: var(--space-6);
		align-items: start;
	}

	/* On a phone, one column: the people, then her faces under them. */
	.panel.stacked {
		grid-template-columns: minmax(0, 1fr);
		gap: var(--space-4);
	}

	.hers {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		min-inline-size: 0;
	}

	.detail {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

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

	/* A picked face wears the picked ring every tile wears. The crop fills the whole press, which
	   would cover the press's own inset ring, so the ring is drawn on the crop, over the picture. */
	.faces :global(.face.picked img) {
		outline: var(--selected-ring-width) solid var(--sift-accent-ring-line);
		outline-offset: calc(-1 * var(--selected-ring-width));
	}

	/* The face's own No and Yes, under it from its left edge, the same on every face of the wall:
	   they belong to the picture above them, and on the left they start where it starts. */
	.pair {
		display: flex;
		justify-content: flex-start;
		gap: var(--space-1);
	}
</style>
