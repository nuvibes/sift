<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/*
	 * One person's decided faces, in full, so one face can be answered for: a wrong name is always
	 * wrong on a particular appearance. Agree says yes to a face Sift proposed; Take off says it is
	 * not them, remembered so the suggestion does not come round again. A face opens its file at
	 * the moment it was found.
	 */
	import { isMissing } from '$lib/api/client';
	import type { components } from '$lib/api/schema';
	import { openAsset } from '$lib/player/asset-view';
	import { page } from '$app/state';
	import Icon from '$lib/components/Icon.svelte';
	import Pager from '$lib/components/common/Pager.svelte';
	import { untrack } from 'svelte';

	import { page as address } from '$app/state';
	import { anchorIn, rememberAnchor } from '$lib/grid/anchor';
	import { CardPaging } from '$lib/grid/cards.svelte';
	import OrganizeHeader from '$lib/components/organize/OrganizeHeader.svelte';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import { organizeCrumbs, tabOpenedFrom } from '$lib/organize/bands';
	import { decided, heldBoard } from '$lib/organize/organize.svelte';
	import {
		ActionBar,
		ConfirmDialog,
		ContextMenu,
		Empty,
		FaceMark,
		Pressable,
		Problem,
		Selection,
		Skeleton,
		SplitButton,
		TileGesture,
		TILE_ID,
		VerbButtons,
		VerbMenuItems,
		VerbMore
	} from '$lib/components/common';
	import type { TabLink } from '$lib/components/common/Tabs.svelte';
	import { faceVerbs } from '$lib/components/faces/verbs';
	import { barShape, type Verb } from '$lib/components/common/verbs';
	import MoveFacesDialog from '$lib/components/faces/MoveFacesDialog.svelte';
	import { announceSkipped, type BulkWriteDone } from '$lib/library/bulk';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { agreeingWith, rematching } from '$lib/components/faces/WaitingForYou.svelte';
	import {
		IDENTIFIED_PER_PAGE,
		acceptFaces,
		confirmLookAlikes,
		confirmMatches,
		cropUrl,
		formatRange,
		identifiedForPerson,
		moveFaces,
		rejectFace,
		rejectLookAlikes,
		rejectMatches,
		removeFaces,
		setAsideFaces,
		type RunWrite,
		type Sighting
	} from '$lib/people/faces.svelte';

	const personId = $derived(page.params.id ?? '');

	let faces = $state<Sighting[]>([]);

	/*
	 * The three states a named face can be in, as three tabs, the work first: "you said so" and
	 * "Sift worked it out" are different kinds of certainty. The same words on every faces screen.
	 */
	const SHOWN = [
		{
			key: 'suggested',
			title: 'Needs your input',
			blurb: "Sift isn't sure about these. Confirm the ones that are right."
		},
		{ key: 'confirmed', title: 'Confirmed', blurb: 'You confirmed these names.' },
		{
			key: 'matched',
			title: 'Recognized by Sift',
			blurb: 'Sift named these without asking. Nothing to do unless you disagree.'
		}
	] as const;

	type Shown = (typeof SHOWN)[number]['key'];

	/* Counted by the server on the same read, so the row arrives whole; null draws no number. */
	let counts = $state<Record<Shown, number> | null>(null);
	let total = $state(0);

	/* From the address, so it survives a refresh and a shared link. */
	const asked = $derived(SHOWN.find((one) => one.key === page.url.searchParams.get('show'))?.key);
	/*
	 * With no tab in the address, the first with something on it, the guesses winning whenever
	 * there are any. `counts` arrives with the first answer, so this settles by the second pass.
	 */
	const show = $derived<Shown>(
		asked ??
			(counts === null
				? 'suggested'
				: ((SHOWN.find((one) => (counts as Record<Shown, number>)[one.key] > 0)?.key ??
						'suggested') as Shown))
	);
	/** The tab showing, as its own row of SHOWN: its title and its sentence. */
	const showing = $derived(SHOWN.find((one) => one.key === show) ?? SHOWN[0]);
	let loading = $state(true);
	let missing = $state(false);
	/* A failure that is NOT "there is nothing here". See `isMissing`. */
	let failed = $state(false);
	const paging = new CardPaging(IDENTIFIED_PER_PAGE, 'organize.person-faces');
	let busy = $state(false);
	let confirmRemove = $state(false);
	let confirmAside = $state(false);
	let moving = $state(false);
	let confirmUndo = $state(false);

	const selection = new Selection();
	const gesture = new TileGesture(selection, () => faces.map((face) => face.track_id));
	const picked = $derived(selection.ordered(faces.map((face) => face.track_id)));

	/** The face a right-click was aimed at, while its menu is open. Not a selection. See `aimAt`. */
	let aimed = $state<string | null>(null);
	/*
	 * What the verbs are ABOUT: the aimed face, or the selection, so a dim row is judged on the
	 * same faces.
	 */
	const aiming = $derived(aimed ? [aimed] : picked);
	/** What the verb somebody chose is about, recorded when they choose it. See the pile's note. */
	let acting = $state<string[]>([]);

	/*
	 * Which tab this person was opened from; `tabOpenedFrom` refuses a name that is not a tab here.
	 */
	const opened = $derived(
		tabOpenedFrom(
			heldBoard.found?.queues ?? [],
			'known-people',
			address.url.searchParams.get('via')
		) ?? 'known-people'
	);

	/* Off the answer, not its first face: an empty tab has none. */
	let personName = $state<string | null>(null);

	/*
	 * Which of some faces carry a name nobody confirmed, matched as well as suggested. A function
	 * of the ids: by the time the action runs the menu has closed and the aim is gone.
	 */
	const proposalsIn = (ids: readonly string[]) =>
		faces.filter(
			(face) => ids.includes(face.track_id) && face.person_id && face.attribution !== 'confirmed'
		);

	/* A face whose person this account may not be told about is left out. */
	const namedIn = (ids: readonly string[]) =>
		faces.filter((face) => ids.includes(face.track_id) && face.person_id);

	const agreeable = $derived(proposalsIn(aiming));
	const undoable = $derived(namedIn(aiming));

	/*
	 * No naming here: everything already has a name. The two rows that can apply to nothing are dim
	 * rather than dropped, so the rows do not move under the pointer.
	 */
	const verbs = $derived(
		faceVerbs({
			agree: (ids) => ((acting = ids), void agree()),
			undo: (ids) => ((acting = ids), (confirmUndo = true)),
			move: (ids) => ((acting = ids), (moving = true)),
			setAside: (ids) => ((acting = ids), (confirmAside = true)),
			remove: (ids) => ((acting = ids), (confirmRemove = true))
		}).map((verb) => ({
			...verb,
			disabled:
				busy ||
				(verb.id === 'agree' && agreeable.length === 0) ||
				(verb.id === 'undo' && undoable.length === 0)
		}))
	);
	/* Agree and Remove keep their words; the rest go behind the door. See `barShape`. */
	const shape = $derived(barShape(verbs));

	/* Right-clicking AIMS at a face without picking it, so the bar does not rise over the menu. */
	function aimAt(trackId: string) {
		aimed = selection.has(trackId) ? null : trackId;
	}

	/* Where this wall was left, in the address (`$lib/grid/anchor`); `path` is captured once. */
	const path = address.url.pathname;
	let arriving = true;

	/**
	 * Keep the address in step with where the page landed, through `land` in the same step as the
	 * rows, or the effect watching the offset would ask again for the page just handed.
	 */
	function settle(at: number, first: string | null | undefined) {
		paging.land(at);
		rememberAnchor(address.url, path, first, at);
	}

	async function load() {
		if (!personId) return;
		failed = false;
		let overtaken = false;
		try {
			/* Filtered by the server, per tab, so each tab and its pager count the same faces. */
			const id = personId;
			const tab = show;
			const page = await paging.fill(
				`${id}\n${tab}`,
				() => faces,
				(query) => {
					// "Looking..." only when a request goes out: a landing or a trim asks nothing.
					loading = true;
					return identifiedForPerson(id, query, tab);
				},
				(answer) => ({ rows: answer.items, total: answer.total, offset: answer.offset })
			);
			// Overtaken by a newer read, which finishes this one's work: the loading line too.
			if (page === null) {
				overtaken = true;
				return;
			}
			faces = page.rows;
			total = page.total;
			const answer = page.answer;
			if (answer) {
				personName = answer.person_name ?? null;
				counts = {
					suggested: answer.waiting,
					confirmed: answer.confirmed,
					matched: answer.matched
				};
			}
			/* Only a 404 says the person has gone: two of the three tabs are routinely empty. */
			missing = false;
			settle(page.offset, faces[0]?.track_id);
		} catch (error) {
			// Only a 404 says these have gone. Anything else is a fault.
			missing = isMissing(error);
			failed = !missing;
			faces = [];
			total = 0;
		} finally {
			if (!overtaken) loading = false;
		}
	}

	/* Not state: writing it would make the effect below depend on what it writes. */
	let shownLast = show;

	$effect(() => {
		void personId;
		void show;
		void paging.offset;
		void paging.size;
		/* A different tab starts again at the top, or it would land past the end of a short one. */
		if (show !== shownLast) {
			shownLast = show;
			paging.forget();
			if (paging.offset !== 0) {
				paging.offset = 0;
				return;
			}
		}
		if (arriving) {
			arriving = false;
			// UNTRACKED: this effect's own answer writes the address.
			paging.arrive(untrack(() => anchorIn(address.url)));
		}
		/*
		 * UNTRACKED: tracked, `land` clearing the anchor would ask again for the page just landed.
		 */
		untrack(() => void load());
	});

	async function agree() {
		const agreeing = proposalsIn(acting);
		if (agreeing.length === 0) return;
		busy = true;
		try {
			// What the verb was HANDED: the closing menu has already emptied `aiming`.
			const answer = await acceptFaces(agreeing.map((face) => face.track_id));
			selection.clear();
			toasts.show(
				answer.changed === 1
					? 'That face is confirmed'
					: `${counted(answer.changed)} faces are confirmed`,
				{ tone: 'success' }
			);
			announceSkipped(answer, 'face');
			await load();
		} catch {
			toasts.show("Those couldn't be confirmed", { tone: 'error' });
		} finally {
			busy = false;
		}
	}

	/*
	 * Set aside, move and remove, offered here too: being named does not change a useless crop.
	 * Setting aside and moving take the name off on the way through.
	 */
	/**
	 * The answer is passed in so what was LEFT OUT (a vaulted face skipped) is said in one place.
	 */
	async function afterDeciding(message: string, done: BulkWriteDone) {
		selection.clear();
		toasts.show(message, { tone: 'success' });
		announceSkipped(done, 'face');
		await load();
	}

	async function remove() {
		if (acting.length === 0) return;
		busy = true;
		try {
			const done = await removeFaces(acting);
			await afterDeciding(
				done.changed === 1
					? 'That face has been deleted'
					: `${counted(done.changed)} faces have been deleted`,
				done
			);
		} catch {
			toasts.show("Those faces couldn't be deleted", { tone: 'error' });
		} finally {
			busy = false;
		}
	}

	async function setAside() {
		if (acting.length === 0) return;
		busy = true;
		try {
			const done = await setAsideFaces(acting);
			await afterDeciding(
				acting.length === 1
					? 'That face has been discarded'
					: `${counted(acting.length)} faces have been discarded`,
				done
			);
		} catch {
			toasts.show("Those faces couldn't be discarded", { tone: 'error' });
		} finally {
			busy = false;
		}
	}

	async function move(target: string | null) {
		if (acting.length === 0) return;
		busy = true;
		try {
			const done = await moveFaces(acting, target);
			await afterDeciding(
				target === null
					? `${done.changed === 1 ? 'That face is' : `${counted(done.changed)} faces are`} a group of their own now`
					: `${done.changed === 1 ? 'That face has' : `${counted(done.changed)} faces have`} joined the other group`,
				done
			);
		} catch {
			toasts.show("Those faces couldn't be moved", { tone: 'error' });
		} finally {
			busy = false;
		}
	}

	async function undo() {
		const undoing = namedIn(acting);
		if (undoing.length === 0) return;
		busy = true;
		try {
			for (const face of undoing) {
				await rejectFace(face.track_id, face.person_id as string);
			}
			selection.clear();
			toasts.show(undoing.length === 1 ? 'Name cleared' : `${undoing.length} names cleared`, {
				tone: 'success'
			});
			await load();
		} catch {
			toasts.show("Those names couldn't be cleared", { tone: 'error' });
		} finally {
			busy = false;
		}
	}

	/*
	 * Yes and no for the tab, on the tab line, shaped like the group screen's "Add as person": what
	 * is picked, this page, or the whole tab, which the server filters (`RunWrite`). One receipt a
	 * press.
	 */
	let bulking = $state(false);

	/** How many are on this tab in all: its own count, which the server sent with the page. */
	const onThisTab = $derived(counts?.[show] ?? 0);
	/** Every face on this page, for the answers about the page. */
	const onPage = $derived(faces.map((face) => face.track_id));

	type RunScope = components['schemas']['RunScope'];

	/** A No waiting on its confirmation: which faces, and how many that is. */
	let pendingNo = $state<{ scope: RunScope; ids: string[]; count: number } | null>(null);
	let confirmBulkNo = $state(false);

	/** The faces one scope is about, and how many; `all` names none and counts the tab. */
	function scoped(scope: RunScope): { ids: string[]; count: number } {
		if (scope === 'all') return { ids: [], count: onThisTab };
		const ids = scope === 'picked' ? picked : onPage;
		return { ids: [...ids], count: ids.length };
	}

	/* The two doors per tab, the scope in the body. A No answers with its receipt, for Undo. */
	async function run(
		yes: boolean,
		body: RunWrite
	): Promise<{ changed: number; decisionId: string | null }> {
		if (show === 'matched' && yes) {
			return { changed: (await confirmMatches(personId, body)).confirmed, decisionId: null };
		}
		const answer =
			show === 'matched'
				? await rejectMatches(personId, body)
				: yes
					? await confirmLookAlikes(personId, body)
					: await rejectLookAlikes(personId, body);
		return { changed: answer.changed, decisionId: answer.decision_id ?? null };
	}

	async function answer(yes: boolean, scope: RunScope, ids: readonly string[]) {
		if (bulking || show === 'confirmed') return;
		if (scope !== 'all' && ids.length === 0) return;
		bulking = true;
		try {
			const { changed, decisionId } = await run(
				yes,
				scope === 'all' ? { scope } : { scope, track_ids: [...ids] }
			);
			selection.clear();
			if (yes && changed > 0) rematching.after(personId);
			if (yes && show !== 'matched') toasts.show(agreeingWith(changed), { tone: 'success' });
			else if (yes) {
				decided(
					changed === 1
						? 'One face is confirmed'
						: `${changed.toLocaleString()} faces are confirmed`,
					decisionId,
					{ after: load }
				);
			} else {
				decided(
					changed === 1
						? 'One face is no longer theirs'
						: `${changed.toLocaleString()} faces are no longer theirs`,
					decisionId,
					{ after: load }
				);
			}
			await load();
		} catch {
			toasts.show(yes ? "Those couldn't be confirmed" : "Those names couldn't be cleared", {
				tone: 'error'
			});
		} finally {
			bulking = false;
		}
	}

	/** A Yes goes immediately; a No asks first, because it takes a name off every face it is about. */
	function press(yes: boolean, scope: RunScope) {
		const { ids, count } = scoped(scope);
		if (yes) void answer(true, scope, ids);
		else {
			pendingNo = { scope, ids, count };
			confirmBulkNo = true;
		}
	}

	/** The main half: what is picked, or the whole page when nothing is. */
	const leadScope = $derived<RunScope>(picked.length > 0 ? 'picked' : 'page');
	const leadCount = $derived(picked.length > 0 ? picked.length : onPage.length);

	/*
	 * The group screen's words (`PileDetail`'s `pageRows`), with "in this tab" for "in this group".
	 */
	const tabRows = $derived<Verb[]>(
		[
			{ yes: true, id: 'yes', label: 'Yes', icon: 'check' as const },
			{ yes: false, id: 'no', label: 'No', icon: 'close' as const }
		].map((answerRow) => ({
			id: answerRow.id,
			label: answerRow.label,
			icon: answerRow.icon,
			children: [
				{
					id: `${answerRow.id}-page`,
					label:
						onPage.length === 1
							? 'The one face on this page'
							: `All ${onPage.length.toLocaleString()} on this page`,
					icon: 'checklist' as const,
					disabled: bulking || onPage.length === 0,
					run: () => press(answerRow.yes, 'page')
				},
				{
					id: `${answerRow.id}-picked`,
					label:
						picked.length === 0
							? 'Nothing is selected'
							: picked.length === 1
								? 'The one selected'
								: `The ${picked.length.toLocaleString()} picked`,
					icon: 'check_box' as const,
					disabled: bulking || picked.length === 0,
					run: () => press(answerRow.yes, 'picked')
				},
				{
					id: `${answerRow.id}-all`,
					label:
						onThisTab === 1
							? 'The one face in this tab'
							: `All ${onThisTab.toLocaleString()} in this tab`,
					icon: 'groups' as const,
					disabled: bulking || onThisTab === 0,
					run: () => press(answerRow.yes, 'all')
				}
			]
		}))
	);

	function pressed(face: Sighting, event: MouseEvent) {
		/*
		 * Read BEFORE the click is applied, or letting go of the last selected face opens the file.
		 */
		if (gesture.handled(face.track_id, event)) return;
		// At the moment of the picture pressed, not the first frame the face was seen in.
		openAsset(face.asset_id, [], face.picture_ms);
	}

	/* Whether anything is asked of somebody, in the words the rest of Organize uses. */
	function describe(face: Sighting): string {
		if (face.attribution === 'confirmed') return 'Confirmed';
		if (face.attribution === 'suggested') return 'Needs your input';
		return 'Recognized by Sift';
	}

	/* One mark for a face Sift named and took as a reference, in the glyph History gives faces. */
	const learned = (face: Sighting) => (face.attribution === 'matched' ? 'learned' : 'reference');

	function learnsFrom(face: Sighting): string {
		const first = (personName ?? '').trim().split(' ')[0];
		if (face.attribution !== 'matched') return `Sift uses this one to identify ${first || 'them'}`;
		const whom = first ? `${first} in this one` : 'this face';
		return `Sift recognized ${whom} and uses it to identify them`;
	}

	/* The sweep reads the id off whatever is under the pointer, so it has to be in the DOM. */
	const sweepable = (trackId: string) => ({ [TILE_ID]: trackId });

	/* Every tab is a real address, so Back steps between them. */
	const tabs = $derived<TabLink[]>(
		SHOWN.map((one) => ({
			id: one.key,
			label: one.title,
			href: `${path}?show=${one.key}`,
			count: counts?.[one.key]
		}))
	);
</script>

<svelte:head><title>{personName ?? 'Identified'}</title></svelte:head>

<!-- `known-people`, the queue's name, so the trail can step back to the tab pressed. -->
<PageFrame
	crumbs={organizeCrumbs(
		heldBoard.found?.queues ?? [],
		'known-people',
		personName ?? 'Identified faces',
		undefined,
		opened
	)}
>
	{#snippet header()}
		<!--
		Titled with the person's name. The tabs are this person's three states, as one page with one
		scrolling region, which is what the page size is measured against.
		-->
		<OrganizeHeader
			queue={opened}
			here={personName ?? 'Identified faces'}
			title={personName ?? 'Identified faces'}
			icon="person"
			showTitle
			{tabs}
			current={show}
			controls={show !== 'confirmed' && !missing && !failed && onThisTab > 0
				? tabAnswers
				: undefined}
		></OrganizeHeader>
	{/snippet}
	{#snippet footer()}
		<!-- In the frame's foot, where every screen in Sift keeps it. -->
		<Pager {...paging.asPager(faces.length, total, 'appearances')} />
	{/snippet}

	<!-- Only while nothing is on screen: a reload after a decision keeps what is drawn. -->
	{#if loading && faces.length === 0 && total === 0}
		<Skeleton lines={3} />
	{:else if missing}
		<Empty scope="page" icon="person" title="These faces have moved">
			They may have been renamed or undone since this page was opened.
		</Empty>
	{:else if failed}
		<Problem
			message="Those faces couldn't be loaded. They are still there. Try again in a moment."
		/>
	{:else if total === 0}
		<!-- An empty TAB, which is not an empty person. -->
		<Empty scope="page" icon="person" title={show === 'suggested' ? 'Nothing waiting' : 'None yet'}>
			{show === 'suggested'
				? 'Sift has nothing to suggest for them. Faces Sift is unsure about appear here.'
				: showing.blurb}
		</Empty>
	{:else}
		<!-- No count line: the lit tab carries the number. One scrolling region, the page's own, or
		     `CardPaging` would measure against a nested box. -->
		<ul class="faces" {@attach paging.cards}>
			{#each faces as face (face.track_id)}
				<li>
					<ContextMenu
						label={`Actions for ${describe(face)}`}
						onOpenChange={(isOpen) => {
							if (!isOpen && aimed === face.track_id) aimed = null;
						}}
					>
						<Pressable
							class="face {face.attribution === 'suggested' ? 'waiting' : ''}"
							feedback="none"
							radius="md"
							picked={selection.has(face.track_id) || aimed === face.track_id}
							oncontextmenu={() => aimAt(face.track_id)}
							onpointerdown={(event) => gesture.pressStart(face.track_id, event)}
							onpointerup={() => gesture.pressEnd()}
							onpointercancel={() => gesture.pressEnd()}
							onclick={(event) => pressed(face, event)}
							aria-label={`${describe(face)}, found at ${formatRange(
								face.started_ms,
								face.ended_ms
							)}${face.is_reference ? `, ${learnsFrom(face)}` : ''}`}
							{...sweepable(face.track_id)}
						>
							<img src={cropUrl(face)} alt="" loading="lazy" />
							<!--
							The marks sit beside the timestamp rather than over the crop, which is
							what is being judged.
							The status is the tab, so it is not repeated under every tile.
							-->
							<span class="foot">
								<span class="marks">
									<!--
									Marked only where it is a reference: agreeing to a group offers
									every face and rules can decline
									one, so a reference count alone would read as broken.
									-->
									{#if face.is_reference}
										<FaceMark kind={learned(face)} label={learnsFrom(face)} decorative />
									{/if}
									<!--
									Nothing on a face you confirmed: the absence of a mark says
									there is nothing to do.
									-->
									{#if face.attribution === 'matched' && !face.is_reference}
										<FaceMark kind="recognized" decorative />
									{:else if face.attribution === 'suggested'}
										<FaceMark kind="asking" decorative />
									{/if}
								</span>
								<span class="when">{formatRange(face.started_ms, face.ended_ms)}</span>
							</span>
							{#if selection.has(face.track_id)}
								<span class="tick" aria-hidden="true"><Icon name="check" size={16} /></span>
							{/if}
						</Pressable>

						{#snippet items()}
							<VerbMenuItems ids={aiming} {verbs} />
						{/snippet}
					</ContextMenu>
				</li>
			{/each}
		</ul>
	{/if}
</PageFrame>

<ActionBar count={picked.length} noun="face" onclear={() => selection.clear()}>
	{#snippet actions()}
		<!-- The same declared list every face on this wall offers on a right-click. -->
		<div class="row"><VerbButtons verbs={shape.named} ids={picked} /></div>
	{/snippet}
	{#snippet overflow()}
		<VerbMore verbs={shape.rest} ids={picked} noun="face" />
	{/snippet}
</ActionBar>

<ConfirmDialog
	bind:open={confirmUndo}
	title={undoable.length === 1 ? 'Clear this name?' : `Clear ${undoable.length} names?`}
	consequence={"Sift won't suggest that person for these faces again. Nothing is deleted and " +
		'no file is touched.'}
	confirmLabel={personName ? `Remove ${personName} from this` : 'Remove the name from this'}
	destructive={false}
	onconfirm={() => void undo()}
/>

<ConfirmDialog
	bind:open={confirmAside}
	title="Discard these?"
	consequence={"They lose their name first, because a face can't be both named and discarded. " +
		'They move to Discarded, where you can restore them. Nothing is deleted and no file is touched.'}
	confirmLabel="Discard"
	destructive={false}
	onconfirm={() => void setAside()}
/>

<ConfirmDialog
	bind:open={confirmRemove}
	title={acting.length === 1 ? 'Delete this face?' : `Delete ${counted(acting.length)} faces?`}
	consequence={"These faces are deleted permanently, and Sift won't find them again in the same " +
		"file. Use this for a crop too poor to be useful. This can't be undone. No file is deleted."}
	confirmLabel="Delete permanently"
	destructive
	onconfirm={() => void remove()}
/>

<!-- The tab's own answers, at the far end of the tab line; none on the settled tab. -->
{#snippet tabAnswers()}
	<SplitButton
		tone="primary"
		icon="check"
		disabled={bulking || leadCount === 0}
		trailingLabel="More for the faces here"
		onclick={() => press(true, leadScope)}
	>
		{bulking ? 'Answering\u2026' : `Yes (${leadCount.toLocaleString()})`}
		{#snippet menu()}
			<VerbMenuItems verbs={tabRows} ids={picked} />
		{/snippet}
	</SplitButton>
{/snippet}

<ConfirmDialog
	bind:open={confirmBulkNo}
	title={pendingNo?.count === 1
		? 'Clear the name from this face?'
		: `Clear the name from ${(pendingNo?.count ?? 0).toLocaleString()} faces?`}
	consequence={(pendingNo?.count === 1
		? "The face loses the name, and Sift won't suggest it for this face again. "
		: "Every one of them loses the name, and Sift won't suggest it for them again. ") +
		'You can undo this from History. Nothing is deleted and no file is touched.'}
	confirmLabel={personName ? `Remove ${personName} from this` : 'Remove the name from this'}
	destructive={false}
	onconfirm={() => {
		/* Left in place rather than cleared: the dialog is still closing, and a title reading
		   "0 faces" for the length of its fade is a sentence nobody asked for. The next No
		   replaces it. */
		if (pendingNo) void answer(false, pendingNo.scope, pendingNo.ids);
	}}
/>

<MoveFacesDialog
	bind:open={moving}
	count={acting.length}
	fromPileId=""
	onmove={(target) => void move(target)}
/>

<style>
	.faces {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(7rem, 1fr));
		gap: var(--space-2);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	/* `:global`, because the class is handed to a component and lands in its scope. Feedback
	   `none`:
	   a lift in a dense wall shifts the neighbours. */
	.faces :global(.face) {
		position: relative;
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		inline-size: 100%;
		padding: var(--space-1);
		border: 1px solid transparent;
		background: var(--sift-surface-2);
		color: var(--sift-ink-3);
		font: var(--text-label);
		transition: background var(--dur-instant) var(--ease);
	}

	.faces :global(.face:hover) {
		background: var(--sift-surface-3);
	}

	/* A proposal reads differently from a decision, so the few still asking stand out. */
	.faces :global(.face.waiting) {
		border-color: var(--sift-line-strong);
	}

	.faces :global(.face img) {
		inline-size: 100%;
		aspect-ratio: 1;
		border-radius: var(--radius-sm);
		object-fit: cover;
		background: var(--sift-surface-3);
	}

	/* Three columns, so the timestamp stays centred whether or not the face wears marks. */
	.foot {
		display: grid;
		grid-template-columns: 1fr auto 1fr;
		align-items: center;
		gap: var(--space-1);
	}

	.marks {
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}

	/* Its own rule, or it would draw as the tick's blue circle over the picture. */
	.when {
		text-align: center;
		font: var(--text-micro);
	}

	.tick {
		position: absolute;
		inset-block-start: var(--space-2);
		inset-inline-end: var(--space-2);
		display: grid;
		place-items: center;
		inline-size: 22px;
		block-size: 22px;
		border-radius: 50%;
		background: var(--sift-accent);
		color: var(--primary-foreground);
	}

	.row {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2);
	}
</style>
