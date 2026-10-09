<script lang="ts" module>
	import { counted as countedWords } from '$lib/entity/entity-counts';
	import { thing, type ToastWords } from '$lib/components/common/toast-pieces';

	/* "4 faces have been added to Wren Halloway", in the answer's own spelling; else the count. */
	export function namedSaid(
		changed: number,
		person: { id: string | null; name: string | null } | null | undefined
	): ToastWords {
		if (!person?.id || !person.name) {
			return changed === 1
				? 'That face has been named'
				: `${countedWords(changed)} faces have been named`;
		}
		const lead = changed === 1 ? 'That face has' : `${countedWords(changed)} faces have`;
		return [`${lead} been added to `, thing('person', person.id, person.name)];
	}
</script>

<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/*
	 * One pile of faces, in full, since the grouping splits rather than merges and a pile is
	 * sometimes one person plus a stranger: every face, the usual picking, decisions over what is
	 * picked. A face opens its file at that moment. The open and the discarded pile share this
	 * screen.
	 */
	import { openAsset } from '$lib/player/asset-view';
	import { leaveFor } from '$lib/shell/navigation.svelte';
	import { page } from '$app/state';
	import Icon from '$lib/components/Icon.svelte';
	import Pager from '$lib/components/common/Pager.svelte';
	import { untrack } from 'svelte';

	import { page as address } from '$app/state';
	import { anchorIn, rememberAnchor } from '$lib/grid/anchor';
	import { CardPaging } from '$lib/grid/cards.svelte';
	import MoveFacesDialog from '$lib/components/faces/MoveFacesDialog.svelte';
	import ReferenceCount from '$lib/components/faces/ReferenceCount.svelte';
	import OrganizeHeader from '$lib/components/organize/OrganizeHeader.svelte';
	import Pressable from '$lib/components/common/Pressable.svelte';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import { organizeCrumbs, tabOpenedFrom } from '$lib/organize/bands';
	import { heldBoard } from '$lib/organize/organize.svelte';
	import {
		ActionBar,
		Button,
		SplitButton,
		ConfirmDialog,
		ContextMenu,
		Empty,
		MenuButton,
		PickMenu,
		Problem,
		Selection,
		Skeleton,
		TILE_ID,
		TileGesture,
		VerbButtons,
		VerbMenuItems,
		VerbMore
	} from '$lib/components/common';
	import { faceVerbs } from '$lib/components/faces/verbs';
	import { barShape, type Verb } from '$lib/components/common/verbs';
	import { isMissing } from '$lib/api/client';
	import {
		fingerprintOffers,
		fingerprintQuestion,
		makePersonFromFingerprints,
		type FingerprintOffer
	} from '$lib/people/fingerprint-offers';
	import { pageOf } from '$lib/entity/related.svelte';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import type { PickChoice, PickPage } from '$lib/components/common/verbs';
	import { people } from '$lib/people/people.svelte';
	import { personRow } from '$lib/people/person-row';
	import { announceSkipped, type BulkWriteDone } from '$lib/library/bulk';
	import { toasts } from '$lib/shell/toasts.svelte';
	import {
		FACES_PER_PAGE,
		cropUrl,
		faceGroup,
		restoreGroup,
		formatRange,
		moveFaces,
		nameFaces,
		pileTrackIds,
		referenceStrengths,
		removeFaces,
		setAsideFaces,
		type PileStatus,
		type ReferenceStrengths,
		type Sighting
	} from '$lib/people/faces.svelte';

	const pileId = $derived(page.params.id ?? '');

	/* Read from the pile, which knows, not from the route. */
	let status = $state<PileStatus>('open');

	let faces = $state<Sighting[]>([]);
	let total = $state(0);
	let loading = $state(true);
	let missing = $state(false);
	/* A failure that is NOT the group being gone (`isMissing`). */
	let failed = $state(false);
	const paging = new CardPaging(FACES_PER_PAGE, 'faces.group');

	const selection = new Selection();
	const gesture = new TileGesture(selection, () => faces.map((face) => face.track_id));

	let naming = $state(false);
	let aimed = $state<string | null>(null);
	/* The ids the chosen verb was handed: a right-click aims without picking. */
	let acting = $state<string[]>([]);
	/* Read once for the screen. */
	let strengths = $state<ReferenceStrengths | null>(null);
	let busy = $state(false);
	let confirmAside = $state(false);
	let confirmRemove = $state(false);
	let moving = $state(false);

	const picked = $derived(selection.ordered(faces.map((face) => face.track_id)));

	/* Naming and moving open something; Restore brings back the whole group, whatever is picked. */
	const verbs = $derived(
		faceVerbs({
			name: (ids) => {
				acting = ids;
				pickTheAimed();
				// The field lives on the bar, so this page's faces are picked first.
				if (picked.length === 0) selection.toggleAll(onPage.filter((id) => ids.includes(id)));
				naming = true;
			},
			move: (ids) => ((acting = ids), (moving = true)),
			setAside: status === 'ignored' ? undefined : (ids) => ((acting = ids), (confirmAside = true)),
			restore: status === 'ignored' ? () => void bringBack() : undefined,
			remove: (ids) => ((acting = ids), (confirmRemove = true))
		}).map((verb) => ({ ...verb, disabled: busy }))
	);
	/* "Add as person" and Delete keep their words; the rest behind the door (`barShape`). */
	const shape = $derived(barShape(verbs));

	const onPage = $derived(faces.map((face) => face.track_id));

	/* The header's control: each verb for the whole page or what is picked, in words. */
	const pageRows = $derived<Verb[]>(
		verbs.map((verb) => {
			if (verb.id === 'restore') return verb;
			const count = picked.length;
			return {
				id: verb.id,
				label: verb.label,
				icon: verb.icon,
				filled: verb.filled,
				destructive: verb.destructive,
				children: [
					{
						id: `${verb.id}-page`,
						label:
							onPage.length === 1
								? 'The one face on this page'
								: `All ${onPage.length} on this page`,
						icon: 'checklist' as const,
						disabled: verb.disabled || onPage.length === 0,
						run: () => verb.run?.(onPage)
					},
					{
						id: `${verb.id}-picked`,
						label:
							count === 0
								? 'Nothing is selected'
								: count === 1
									? 'The one selected'
									: `The ${count} picked`,
						icon: 'check_box' as const,
						disabled: verb.disabled || count === 0,
						run: () => verb.run?.(picked)
					},
					{
						id: `${verb.id}-all`,
						label: total === 1 ? 'The one face in this group' : `All ${total} in this group`,
						icon: 'groups' as const,
						disabled: verb.disabled || total === 0,
						run: () => void overTheWholePile(verb)
					}
				]
			};
		})
	);

	/* The whole pile past the page: the ids read once, the verb run over them. */
	async function overTheWholePile(verb: Verb) {
		try {
			const ids = await pileTrackIds(pileId);
			if (ids.length === 0) return;
			verb.run?.(ids);
		} catch {
			toasts.show("The whole group couldn't be read. Try again in a moment.", { tone: 'error' });
		}
	}

	function nameThese() {
		const chosen = picked.length > 0 ? picked : onPage;
		if (chosen.length === 0) return;
		// The bar rises over a selection, so the faces are picked first.
		if (picked.length === 0) selection.toggleAll(onPage);
		acting = chosen;
		naming = true;
	}

	/*
	 * Right-clicking AIMS without picking; naming picks what was aimed at, since its field is in
	 * the bar.
	 */
	function aimAt(trackId: string) {
		aimed = selection.has(trackId) ? null : trackId;
	}

	const menuIds = $derived(aimed ? [aimed] : picked);

	function pickTheAimed() {
		if (aimed) selection.only(aimed);
		aimed = null;
	}

	/* A page of the library's people from the server, with `total`. */
	async function askPeople(typed: string): Promise<PickPage> {
		const asked = await people.choices(typed);
		return {
			choices: asked.items.map(personRow),
			more: Math.max(0, asked.total - asked.items.length)
		};
	}

	/**
	 * Create, then name through `onpick`; the picker offers create only when no row has the name.
	 */
	async function makePerson(named: string): Promise<PickChoice> {
		const made = await people.create(named);
		return { id: made.id, name: made.name };
	}

	/* The tab this group was opened from (`pileHref`, `tabOpenedFrom`). */
	const opened = $derived(
		tabOpenedFrom(
			heldBoard.found?.queues ?? [],
			'faces-to-name',
			address.url.searchParams.get('via')
		) ?? 'faces-to-name'
	);

	/* Leaves for that tab through `leaveFor`, the browser's own step where it can. */
	const home = $derived(`/organize/${opened}`);

	/* Where this wall was left (`$lib/grid/anchor`); `path` is captured once. */
	const path = address.url.pathname;
	let arriving = true;

	/** Through `land`, in the same step as the rows (`CardPaging.land`). */
	function settle(at: number, first: string | null | undefined) {
		paging.land(at);
		rememberAnchor(address.url, path, first, at);
	}

	/* A group like somebody in a fingerprints file asks first whether to make them a person. */
	let offer = $state<FingerprintOffer | null>(null);
	let made = $state<{ id: string; name: string } | null>(null);

	async function readOffer() {
		offer = (await fingerprintOffers()).get(pileId) ?? null;
	}

	$effect(() => {
		void pileId;
		made = null;
		untrack(() => void readOffer());
	});

	reloadOnLibraryChange(() => void readOffer());

	async function makeFromFingerprints(asked: FingerprintOffer) {
		busy = true;
		try {
			const id = await makePersonFromFingerprints(asked.entry_id);
			made = { id, name: asked.name };
			toasts.show([thing('person', id, asked.name), ' is a person now'], { tone: 'success' });
		} catch {
			toasts.show("That person couldn't be made", { tone: 'error' });
		} finally {
			busy = false;
		}
	}

	async function load() {
		missing = false;
		failed = false;
		let overtaken = false;
		try {
			const page = await paging.fill(
				pileId,
				() => faces,
				(query) => {
					loading = true;
					return faceGroup(pileId, query);
				},
				(answer) => ({ rows: answer.group.faces, total: answer.total, offset: answer.offset })
			);
			if (page === null) {
				overtaken = true;
				return;
			}
			faces = page.rows;
			total = page.total;
			if (page.answer) status = page.answer.group.status;
			settle(page.offset, faces[0]?.track_id);
		} catch (error) {
			// Only a 404 says the group has gone.
			missing = isMissing(error);
			failed = !missing;
			faces = [];
			total = 0;
		} finally {
			if (!overtaken) loading = false;
		}
	}

	$effect(() => {
		void pileId;
		void paging.offset;
		void paging.size;
		if (arriving) {
			arriving = false;
			// UNTRACKED: this effect's own answer writes the address.
			paging.arrive(untrack(() => anchorIn(address.url)));
		}
		selection.clear();
		/* UNTRACKED: tracked, `land` clearing the anchor would ask again. */
		untrack(() => void load());
	});

	/*
	 * Its own effect, or the answer landing would re-run the pile's read; a hint's failure is
	 * swallowed.
	 */
	$effect(() => {
		if (!strengths) {
			void referenceStrengths()
				.then((found) => (strengths = found))
				.catch(() => (strengths = null));
		}
	});

	/* ARRIVED HERE TO NAME THEM: `nameThese`, once, once the faces are on screen. */
	let asked = false;

	$effect(() => {
		if (asked || !address.url.searchParams.has('name') || faces.length === 0) return;
		asked = true;
		nameThese();
	});

	/** Re-read, never patched; what was LEFT OUT (vaulted faces) is said here once. */
	async function afterDeciding(message: ToastWords, done: BulkWriteDone) {
		selection.clear();
		naming = false;
		toasts.show(message, { tone: 'success' });
		announceSkipped(done, 'face');
		await load();
		if (total === 0) await leaveFor(home);
	}

	async function name(who: { personId: string } | { name: string }) {
		if (acting.length === 0) return;
		busy = true;
		try {
			const done = await nameFaces(acting, who);
			await afterDeciding(
				namedSaid(done.changed, { id: done.person_id, name: done.person_name }),
				done
			);
		} catch {
			toasts.show("Those faces couldn't be named", { tone: 'error' });
		} finally {
			busy = false;
		}
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

	/*
	 * Merge into another group or split into a new one: the two mistakes a splitting grouping
	 * makes.
	 */
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

	/* No confirmation: this is the undo. */
	async function bringBack() {
		busy = true;
		try {
			await restoreGroup(pileId);
			toasts.show('That group is back', { tone: 'success' });
			await leaveFor(home);
		} catch {
			toasts.show("That group couldn't be restored", { tone: 'error' });
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

	/*
	 * A plain click opens the file over this page, with no neighbours: faces are not a list of
	 * files.
	 */
	function pressed(face: Sighting, event: MouseEvent) {
		/* Read BEFORE the click is applied, or letting go of the last face opens the file. */
		if (gesture.handled(face.track_id, event)) return;
		openAsset(face.asset_id, [], face.picture_ms);
	}
</script>

<svelte:head><title>A group of faces</title></svelte:head>

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

<PageFrame
	crumbs={organizeCrumbs(
		heldBoard.found?.queues ?? [],
		'faces-to-name',
		'A group of faces',
		undefined,
		opened
	)}
>
	{#snippet header()}
		<!-- No `controls`: the group's control is on its own row, above its faces. -->
		<OrganizeHeader queue={opened} here="A group of faces"></OrganizeHeader>
	{/snippet}
	{#snippet footer()}
		{#if !loading || faces.length > 0}
			<Pager
				offset={paging.offset}
				shown={faces.length}
				{total}
				noun="faces"
				onfirst={() => paging.goTo(0, total)}
				onprevious={() => paging.step(-1, total)}
				onnext={() => paging.step(1, total)}
				onlast={() => paging.last(total)}
				onjump={(position) => paging.goTo(position - 1, total)}
			/>
		{/if}
	{/snippet}

	<!-- Only while nothing is on screen: a reload keeps what is drawn. -->
	{#if loading && faces.length === 0}
		<Skeleton lines={3} />
	{:else if missing}
		<Empty scope="block">
			That group isn't there any more. It may have been named or discarded since this page was
			opened.
		</Empty>
	{:else if failed}
		<Problem message="That group couldn't be loaded. It's still there — try again in a moment." />
	{:else}
		{#if status === 'open' && (made || offer)}
			<div class="bar offer">
				{#if made}
					<p class="asks"><a href={pageOf('person', made.id)}>{made.name}</a> is a person now</p>
				{:else if offer}
					{@const asked = offer}
					<p class="asks">{fingerprintQuestion(asked)}</p>
					<Button
						tone="primary"
						icon="add"
						disabled={busy}
						onclick={() => void makeFromFingerprints(asked)}>Create a person</Button
					>
				{/if}
			</div>
		{/if}
		<div class="bar">
			<p class="count">{total === 1 ? '1 face' : `${counted(total)} faces`}</p>
			{#if status === 'open'}
				<SplitButton
					tone={offer && !made ? 'secondary' : 'primary'}
					icon="person_add"
					trailingLabel="More for the faces here"
					disabled={busy || faces.length === 0}
					onclick={nameThese}
				>
					Add as person
					{#snippet menu()}
						<VerbMenuItems verbs={pageRows} ids={picked} />
					{/snippet}
				</SplitButton>
			{:else if status === 'ignored'}
				<!-- A discarded group's way back, as its card on the wall. -->
				<Button icon="history" disabled={busy} onclick={() => void bringBack()}>Restore</Button>
			{/if}
		</div>

		<ul class="faces" {@attach paging.cards}>
			{#each faces as face (face.track_id)}
				<li>
					<ContextMenu
						label={`Actions for the face found at ${formatRange(face.started_ms, face.ended_ms)}`}
						onOpenChange={(isOpen) => {
							if (!isOpen && aimed === face.track_id) aimed = null;
						}}
					>
						<Pressable
							class="face"
							radius="md"
							feedback="wash"
							picked={selection.has(face.track_id) || aimed === face.track_id}
							oncontextmenu={() => aimAt(face.track_id)}
							onpointerdown={(event: PointerEvent) => gesture.pressStart(face.track_id, event)}
							onpointerup={() => gesture.pressEnd()}
							onpointercancel={() => gesture.pressEnd()}
							onclick={(event: MouseEvent) => pressed(face, event)}
							aria-label={`A face found at ${formatRange(face.started_ms, face.ended_ms)}`}
							{...{ [TILE_ID]: face.track_id }}
						>
							<img src={cropUrl(face)} alt="" loading="lazy" />
							<span class="when">{formatRange(face.started_ms, face.ended_ms)}</span>
							{#if selection.has(face.track_id)}
								<span class="tick" aria-hidden="true"><Icon name="check" size={16} /></span>
							{/if}
						</Pressable>

						{#snippet items()}
							<VerbMenuItems ids={menuIds} {verbs} />
						{/snippet}
					</ContextMenu>
				</li>
			{/each}
		</ul>
	{/if}
</PageFrame>

<ActionBar
	count={naming ? acting.length : picked.length}
	noun="face"
	onclear={() => {
		selection.clear();
		naming = false;
	}}
>
	{#snippet actions()}
		{#if naming}
			<!-- The "Add to" selector; `bind:open` closes the naming; opens upwards. -->
			<MenuButton
				label="Name these faces"
				words="Name"
				side="top"
				bind:open={naming}
				disabled={busy}
				scrolls={false}
			>
				<PickMenu
					label="Add as person"
					icon="person_add"
					kind="person"
					plural="people"
					inline
					ask={askPeople}
					onpick={(choice) => void name({ personId: choice.id })}
					oncreate={makePerson}
				>
					{#snippet hint(choice)}
						<ReferenceCount personId={choice.id} {strengths} />
					{/snippet}
				</PickMenu>
			</MenuButton>
		{:else}
			<!-- The declared list the menu draws too. -->
			<VerbButtons verbs={shape.named} ids={picked} />
		{/if}
	{/snippet}
	{#snippet overflow()}
		{#if !naming}
			<VerbMore verbs={shape.rest} ids={picked} noun="face" />
		{/if}
	{/snippet}
</ActionBar>

<ConfirmDialog
	bind:open={confirmRemove}
	title="Delete these faces?"
	consequence={"These faces are deleted for good, and Sift won't find them again in the same " +
		'file. Use it for something that was never a face, or a crop too poor to be any use to ' +
		"anybody. This can't be undone. No file is deleted."}
	confirmLabel="Delete permanently"
	destructive
	onconfirm={() => void remove()}
/>

<ConfirmDialog
	bind:open={confirmAside}
	title="Discard these?"
	consequence={'They move to Discarded as a group of their own, where they stay listed and can be ' +
		'restored. The rest of this group is left where it is. Nothing is deleted and no file ' +
		'is touched.'}
	confirmLabel="Discard"
	onconfirm={() => void setAside()}
/>

<MoveFacesDialog
	bind:open={moving}
	count={acting.length}
	fromPileId={pileId}
	onmove={(target) => void move(target)}
/>

<style>
	.bar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-3);
		margin-block-end: var(--space-3);
	}

	.offer {
		margin-block-end: var(--space-2);
	}

	.asks {
		margin: 0;
		font: var(--text-body);
		color: var(--sift-ink);
	}

	.count {
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

	/* `:global`: the class is handed to `Pressable`, which brings the reset and rings. */
	.faces :global(.face) {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		inline-size: 100%;
		padding: var(--space-1);
		background: var(--sift-surface-2);
		color: var(--sift-ink-3);
		font: var(--text-label);
	}

	.faces :global(.face img) {
		inline-size: 100%;
		aspect-ratio: 1;
		border-radius: var(--radius-sm);
		object-fit: cover;
		background: var(--sift-surface-3);
	}

	.when {
		text-align: center;
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
</style>
