<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	import {
		Button,
		Checkbox,
		ConfirmDialog,
		FaceMark,
		Fold,
		PickDialog,
		Pressable,
		type Choice,
		Scroller,
		Tooltip
	} from '$lib/components/common';
	import Icon from '$lib/components/Icon.svelte';
	import type { Verb } from '$lib/components/common/verbs';
	import { SideScroll } from '$lib/components/common/side-scroll.svelte';
	/*
	 * Who is in this file, as faces. A face with no name is drawn as one and nothing asks why: the
	 * server has settled who this account may be told about. Absent when there is nothing.
	 */
	import {
		facesOf,
		formatRange,
		cropUrl,
		nameFaces,
		rejectFace,
		removeFaces,
		setAsideFaces,
		type Sighting
	} from '$lib/people/faces.svelte';
	import { goto } from '$app/navigation';
	import { peopleNamed, people as peopleStore } from '$lib/people/people.svelte';
	import { personRow } from '$lib/people/person-row';
	import { faceVerbs, ONE_FACE_SAYS, oneFaceSays } from '$lib/components/faces/verbs';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import { pileHref } from '$lib/organize/addresses';
	import { pageOf } from '$lib/entity/related.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { announceSkipped } from '$lib/library/bulk';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { thing } from '$lib/components/common/toast-pieces';
	import {
		faceRemovalConfirmSkipped,
		recallInterfaceState,
		skipFaceRemovalConfirm
	} from '$lib/shell/interface-state.svelte';

	interface Props {
		id: string;
		seekable?: boolean;
		/** A new object each time, so the same face twice works. */
		onseek?: (moment: { ms: number }) => void;
	}

	let { id, seekable = false, onseek }: Props = $props();

	let faces = $state<Sighting[]>([]);
	let busy = $state(false);
	let confirmRemove = $state(false);
	let confirmAside = $state(false);
	let naming = $state(false);

	/**
	 * ONE box for both questions, one key (`confirm.face_removal`); reset on open, written on
	 * press.
	 */
	let stopAsking = $state(false);

	/* Read early; until it lands the guard stays. */
	$effect(() => {
		if (session.isAdmin) void recallInterfaceState();
	});

	/*
	 * The face a destructive verb was pressed on, outliving the press until the dialog is answered.
	 */
	let aimed = $state<Sighting | null>(null);

	/**
	 * Every verb for one face, as buttons on its card: name (an admin's), take a name off only
	 * where the name is visible, open its group only while the pile still exists.
	 */
	function verbsFor(face: Sighting): Verb[] {
		const grouped = groupOf(face);
		const aim = (act: () => void) => () => {
			aimed = face;
			act();
		};
		/* The ones a face cannot take are disabled with their reason, so presses stay in place. */
		const why: Partial<Record<string, string>> = {
			'show-group': grouped ? undefined : NOT_IN_A_GROUP,
			undo: face.person_id ? undefined : NOT_NAMED
		};
		return faceVerbs({
			showGroup: aim(() => {
				if (grouped) void openTheGroup(grouped);
			}),
			name: session.isAdmin ? aim(askWhoThisIs) : undefined,
			undo: aim(() => {
				if (face.person_id) void takeTheNameOff();
			}),
			who: face.person_name ?? undefined,
			setAside: aim(askOrSetAside),
			remove: aim(askOrRemove)
		}).map((verb) => {
			const cannot = why[verb.id];
			return cannot === undefined
				? { ...verb, disabled: busy }
				: { ...verb, disabled: true, why: cannot };
		});
	}

	/* Two functions sharing the QUESTION, not the act. */
	function askOrSetAside(): void {
		if (faceRemovalConfirmSkipped()) {
			void setAside();
			return;
		}
		stopAsking = false;
		confirmAside = true;
	}

	function askOrRemove(): void {
		if (faceRemovalConfirmSkipped()) {
			void remove();
			return;
		}
		stopAsking = false;
		confirmRemove = true;
	}

	/* Fills `peopleStore` on opening, or the sheet opens on "Nothing matched." */
	function askWhoThisIs(): void {
		void peopleStore.load();
		naming = true;
	}

	/* Turned past the quality bar's angle: kept to be named, never named by Sift alone. */
	function turnedAway(face: Sighting): boolean {
		return face.turned === true;
	}

	const TURNED = 'Turned away from the camera';
	const NOT_IN_A_GROUP = 'Not in a group';
	const NOT_NAMED = 'Not named yet';

	/* The group a face waits in while it still exists, for every door onto it. */
	function groupOf(face: Sighting): Sighting | null {
		return face.pile_id && face.pile_status ? face : null;
	}

	/* From the one function that knows where a pile lives. */
	function groupHref(face: Sighting): string | null {
		const grouped = groupOf(face);
		return grouped?.pile_id ? pileHref({ id: grouped.pile_id, status: grouped.pile_status }) : null;
	}

	/* A verb runs a handler, so it navigates; the name is the anchor. */
	async function openTheGroup(face: Sighting): Promise<void> {
		const href = groupHref(face);
		if (href) await goto(href);
	}

	const searchPeople = async (typed: string) => (await peopleNamed(typed)).map(personRow);
	/* Through `personRow`, so a row has its face. */
	const personChoices = $derived(peopleStore.items.map(personRow));

	/**
	 * One face, one person; the rest of its group is offered on that person's wall, never confirmed
	 * unseen. `changed` is read: the server skips a face that already carries somebody.
	 */
	async function nameIt(chosen: Choice[]) {
		const face = aimed;
		const who = chosen[0];
		if (!face || !who || busy) return;
		busy = true;
		try {
			const done = await nameFaces([face.track_id], { personId: who.id }, true);
			if (done.skipped > 0) {
				// Before the "already named" reading: a face left out by the vault is not that.
				announceSkipped(done, 'face');
			} else if (done.changed === 0) {
				toasts.show('That face already has a name', { tone: 'info' });
			} else if (done.offered > 0) {
				toasts.show(
					[
						'That face is ',
						thing('person', who.id, who.name),
						`. ${counted(done.offered)} more were grouped with it. Check them on their page.`
					],
					{
						tone: 'success',
						action: { label: 'Open', run: () => void goto(`/people/${who.id}`) }
					}
				);
			} else {
				toasts.show(['That face is ', thing('person', who.id, who.name), ' now'], {
					tone: 'success'
				});
			}
			await reload();
		} catch {
			toasts.show("That face couldn't be named", { tone: 'error' });
		} finally {
			busy = false;
		}
	}

	/* No toast: the sheet reports once, by what it goes on to do. */
	async function makePerson(name: string): Promise<Choice> {
		const made = await peopleStore.create(name);
		return { id: made.id, name: made.name };
	}

	async function reload() {
		faces = await facesOf(id);
	}

	async function takeTheNameOff() {
		const face = aimed;
		if (!face?.person_id || busy) return;
		busy = true;
		try {
			await rejectFace(face.track_id, face.person_id);
			toasts.show('That name has been taken off', { tone: 'success' });
			await reload();
		} catch {
			toasts.show("That name couldn't be taken off", { tone: 'error' });
		} finally {
			busy = false;
		}
	}

	/* Written here, before the act: "stop asking" is true either way. */
	function confirmedAside(): void {
		if (stopAsking) skipFaceRemovalConfirm();
		void setAside();
	}

	function confirmedRemove(): void {
		if (stopAsking) skipFaceRemovalConfirm();
		void remove();
	}

	async function setAside() {
		const face = aimed;
		if (!face || busy) return;
		busy = true;
		try {
			const done = await setAsideFaces([face.track_id]);
			if (done.skipped > 0) announceSkipped(done, 'face');
			else toasts.show('That face has been discarded', { tone: 'success' });
			await reload();
		} catch {
			toasts.show("That face couldn't be discarded", { tone: 'error' });
		} finally {
			busy = false;
		}
	}

	async function remove() {
		const face = aimed;
		if (!face || busy) return;
		busy = true;
		try {
			const done = await removeFaces([face.track_id]);
			if (done.skipped > 0) announceSkipped(done, 'face');
			else toasts.show('That face has been deleted', { tone: 'success' });
			await reload();
		} catch {
			toasts.show("That face couldn't be deleted", { tone: 'error' });
		} finally {
			busy = false;
		}
	}

	/* A string, so a replaced record object does not re-run everything. */
	const heldId = $derived(id);

	$effect(() => {
		// Cleared before the request, or the last file's faces sit under the new picture.
		const wanted = heldId;
		faces = [];
		void facesOf(wanted).then((found) => {
			if (wanted === heldId) faces = found;
		});
	});

	/*
	 * Names land on faces long after the scan, so the library bell re-reads
	 * (`FaceService.rematch`).
	 */
	reloadOnLibraryChange(() => void reload());

	/* `SideScroll`, shared with the lookalikes strip. */
	const strip = new SideScroll();

	$effect(() => strip.watch());
</script>

<!--
One face across: crop left, name top, verbs bottom right. No context menu: every verb is a button.
-->
{#snippet picture(face: Sighting)}
	<img src={cropUrl(face)} alt="" loading="lazy" />
{/snippet}

<!-- The name OVER the press, as a sibling, since a link cannot sit inside a button. -->
{#snippet nameAndWhen(face: Sighting)}
	{@const grouped = groupHref(face)}
	<span class="beside">
		<!-- Only the mark for a QUESTION beside the name. -->
		<span class="naming">
			{#if face.person_id && face.person_name}
				<a class="who goes" href={pageOf('person', face.person_id)}>{face.person_name}</a>
				{#if face.attribution === 'suggested'}
					<FaceMark kind="asking" size="small" />
				{/if}
				{@render turnedMark(face)}
			{:else if grouped}
				<Tooltip label="Show the face group" placement="top">
					<a class="who goes" href={grouped}>Not named yet</a>
				</Tooltip>
				{@render turnedMark(face)}
			{:else}
				<span class="who">{face.person_name ?? 'Not named yet'}</span>
				{@render turnedMark(face)}
			{/if}
		</span>
		{#if seekable && onseek}
			<span class="when">{formatRange(face.started_ms, face.ended_ms)}</span>
		{/if}
	</span>
{/snippet}

<!--
Turned away: matched, but never grouped or a reference; a mark, so the card keeps its shape.
-->
{#snippet turnedMark(face: Sighting)}
	{#if turnedAway(face)}
		<Tooltip label={TURNED} placement="top">
			<span class="turned"><Icon name="face_left" size={14} label={TURNED} /></span>
		</Tooltip>
	{/if}
{/snippet}

{#snippet verbButton(face: Sighting, verb: Verb)}
	<Tooltip label={verb.why ?? oneFaceSays(verb.id) ?? verb.label} placement="top">
		<Button
			tone={verb.destructive ? 'danger-ghost' : 'ghost'}
			size="small"
			icon={verb.icon}
			iconFilled={verb.filled ?? false}
			aria-label={verb.label}
			disabled={verb.disabled ?? false}
			onclick={() => verb.run?.([face.track_id])}
		/>
	</Tooltip>
{/snippet}

{#snippet crop(face: Sighting)}
	{@const acts = session.isAdmin ? verbsFor(face) : []}
	{@const keeps = acts.filter((verb) => !verb.destructive)}
	{@const destroys = acts.filter((verb) => verb.destructive)}
	<!--
	The whole card is the press; the verbs are a sibling raised over it, their room taken by
	`.beside`.
	-->
	<div
		class="card"
		class:acting={acts.length > 0}
		style:--verbs={acts.length}
		style:--apart={destroys.length > 0 && keeps.length > 0 ? 1 : 0}
	>
		{#if seekable && onseek}
			<!-- `Pressable`, from the moment of the PICTURE. -->
			<Pressable
				class="whole"
				radius="md"
				feedback="wash"
				onclick={() => onseek({ ms: face.picture_ms })}
				aria-label={`Play from ${formatRange(face.picture_ms, face.picture_ms)}`}
			>
				{@render picture(face)}
			</Pressable>
		{:else if groupOf(face)}
			<!-- A photograph's card presses through to the face's group. -->
			<Pressable
				class="whole"
				radius="md"
				feedback="wash"
				onclick={() => void openTheGroup(face)}
				aria-label={`Show the face group for ${face.person_name ?? 'this face'}`}
			>
				{@render picture(face)}
			</Pressable>
		{:else}
			<div class="whole">{@render picture(face)}</div>
		{/if}
		{@render nameAndWhen(face)}
		{#if acts.length > 0}
			<!--
			The same five verbs on every face, disabled with a reason where they do not apply.
			Discard and
			Delete carry `ONE_FACE_SAYS`; the destructive one comes last, a step apart.
			-->
			<div class="doings">
				{#each keeps as verb (verb.id)}{@render verbButton(face, verb)}{/each}
				{#if destroys.length > 0}
					<span class="apart" class:alone={keeps.length === 0}>
						{#each destroys as verb (verb.id)}{@render verbButton(face, verb)}{/each}
					</span>
				{/if}
			</div>
		{/if}
	</div>
{/snippet}

<!-- One snippet for both questions, worded for the stronger verb; the whole row is the control. -->
{#snippet dontAsk()}
	<div class="again">
		<Pressable
			class="tick"
			feedback="wash"
			radius="md"
			aria-pressed={stopAsking}
			onclick={() => (stopAsking = !stopAsking)}
		>
			<Checkbox state={stopAsking ? 'on' : 'off'} mark />
			<span>Don't ask me again before discarding or deleting a face</span>
		</Pressable>
	</div>
{/snippet}

{#snippet arrow(way: -1 | 1, name: 'chevron_left' | 'chevron_right', words: string)}
	<!-- Named shared buttons, reachable by everybody, at the shared pace (`nudgeDelay`). -->
	<Tooltip label={words}>
		<Button
			tone="ghost"
			size="small"
			icon={name}
			iconSize={16}
			tall
			aria-label={words}
			onclick={() => strip.nudge(way)}
			onpointerenter={() => strip.nudge(way)}
			onpointerleave={strip.stop}
			onpointerdown={() => strip.nudge(way)}
			onpointerup={strip.stop}
		/>
	</Tooltip>
{/snippet}

{#if faces.length > 0}
	<section class="faces" aria-label="Faces in this">
		<Fold section summary="Who is in this" remember="sift.file.fold.faces" weight={450}>
			<div class="strip">
				{#if strip.canBack}{@render arrow(-1, 'chevron_left', 'Earlier faces')}{/if}
				<Scroller horizontal onviewport={strip.take}>
					<ul>
						{#each faces as face (face.track_id)}
							<li>
								{@render crop(face)}
							</li>
						{/each}
					</ul>
				</Scroller>
				{#if strip.canOn}{@render arrow(1, 'chevron_right', 'Later faces')}{/if}
			</div>
		</Fold>
	</section>

	<!-- The sheet "Add to > Person" opens. -->
	<PickDialog
		bind:open={naming}
		title="Who is this?"
		subject="Naming a face files this file under them and takes the face out of the pile waiting to be identified."
		choices={personChoices}
		onsearch={searchPeople}
		placeholder="Who"
		createLabel="Create"
		kind="person"
		confirmLabel={() => 'Name them'}
		onpick={(chosen: Choice[]) => void nameIt(chosen)}
		oncreate={makePerson}
	/>

	<!-- One question in two words; the verb on the button is the card's word. -->
	<ConfirmDialog
		bind:open={confirmAside}
		title="Discard this face?"
		consequence={ONE_FACE_SAYS['set-aside']}
		confirmLabel="Discard"
		onconfirm={confirmedAside}
	>
		{#snippet below()}
			{@render dontAsk()}
		{/snippet}
	</ConfirmDialog>

	<ConfirmDialog
		bind:open={confirmRemove}
		title="Delete this face?"
		consequence={ONE_FACE_SAYS.remove}
		confirmLabel="Delete"
		destructive
		onconfirm={confirmedRemove}
	>
		{#snippet below()}
			{@render dontAsk()}
		{/snippet}
	</ConfirmDialog>
{/if}

<style>
	/* The column holding these owns the gap (`.under` in `AssetView`). */
	.faces {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.strip {
		display: flex;
		align-items: stretch;
		gap: var(--space-1);
		min-inline-size: 0;
	}

	.strip :global(.scroll-root) {
		min-inline-size: 0;
		flex: 1 1 auto;
	}

	/* ONE ROW, or thirty sightings would be four rows of crops. */
	ul {
		display: flex;
		flex-wrap: nowrap;
		gap: var(--space-2);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	li {
		flex: none;
		display: flex;
	}

	li > .card {
		flex: 1;
	}

	/*
	 * A grid of two columns, crop and name, the press spanning both and the name's column laid over
	 * it, so the name can be a link. The box from `Panel`'s tokens, which has no zero-inset state.
	 */
	.card {
		--crop: 4rem;
		position: relative;
		display: grid;
		grid-template-columns: var(--crop) minmax(0, 1fr);
		column-gap: var(--space-2);
		border: 1px solid transparent;
		border-radius: var(--radius-md);
		background: var(--sift-card);
	}

	/* The press IS the card; `:global` for `Pressable`. */
	.card :global(.whole) {
		grid-column: 1 / -1;
		grid-row: 1;
		min-inline-size: 0;
		display: flex;
		align-items: stretch;
	}

	/* Over the press; it takes no pointer itself. */
	.beside {
		grid-column: 2;
		grid-row: 1;
		position: relative;
		pointer-events: none;
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		min-inline-size: 0;
		padding-block-start: var(--space-1);
		padding-block-end: var(--space-1);
		padding-inline-end: var(--space-2);
	}

	/* The verbs' room, from `--control-height-sm` and `--verbs`, never a number written here. */
	.acting .beside {
		padding-block-end: calc(var(--control-height-sm) + var(--space-1));
		min-inline-size: calc(
			var(--verbs) * var(--control-height-sm) + (var(--verbs) - 1) * var(--space-1) + var(--apart) *
				var(--space-3)
		);
	}

	.doings {
		position: absolute;
		inset-block-end: var(--space-1);
		inset-inline-end: var(--space-2);
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}

	.apart {
		display: flex;
		align-items: center;
		gap: var(--space-1);
		margin-inline-start: var(--space-3);
	}

	.apart.alone {
		margin-inline-start: 0;
	}

	/* The card's corner on the two shared edges; `object-fit: cover`, the height stretched. */
	img {
		inline-size: var(--crop);
		align-self: stretch;
		border-start-start-radius: var(--radius-md);
		border-end-start-radius: var(--radius-md);
		object-fit: cover;
		background: var(--sift-surface-3);
	}

	/* Wraps: a mark on the name's line would widen the card past the strip. */
	.naming {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-1);
		min-inline-size: 0;
	}

	.who {
		font: var(--text-micro);
		line-height: 1.2;
		overflow-wrap: anywhere;
	}

	.goes {
		pointer-events: auto;
		color: inherit;
		text-decoration: none;
		border-radius: var(--radius-sm);
	}

	.goes:hover,
	.goes:focus-visible {
		text-decoration: underline;
	}

	.goes:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	.again {
		margin: var(--space-3) 0 var(--space-5);
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

	.when {
		font: var(--text-micro);
		color: var(--sift-ink-3);
		font-variant-numeric: tabular-nums;
	}

	.turned {
		display: inline-flex;
		color: var(--sift-ink-3);
		pointer-events: auto;
	}
</style>
