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
	 * Who is in this file, as faces rather than as names.
	 *
	 * The block beside the People row on the item detail, and it answers a different question from
	 * that row. People says who this file is filed under; this says which faces are actually in it
	 * and when, so a video where somebody appears for four seconds shows the moment, and a
	 * photograph of three people shows three faces rather than one line of three names.
	 *
	 * **A face with no name is drawn as a face with no name, and nothing here asks why.** The server
	 * has already settled who this account may be told about, and a face arriving unnamed is one
	 * whose person they have no other way of knowing exists. Filling that gap in from anywhere
	 * (another request, a cached list, a guess from the People row) would put back exactly what was
	 * withheld. So an unnamed face is a face, labelled as one.
	 *
	 * Absent entirely when there is nothing to draw. The feature is off on most installs, and a
	 * heading over an empty strip is worse than no heading: it reports a fault where there is none.
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
		/** Whether pressing a face can take the player anywhere. False for a photograph. */
		seekable?: boolean;
		/** Ask to be taken to a moment. A new object each time, so the same face twice works. */
		onseek?: (moment: { ms: number }) => void;
	}

	let { id, seekable = false, onseek }: Props = $props();

	let faces = $state<Sighting[]>([]);
	let busy = $state(false);
	let confirmRemove = $state(false);
	let confirmAside = $state(false);
	let naming = $state(false);

	/*
	 * The offer to stop being asked, and the one answer both questions share.
	 *
	 * ONE box for both verbs because it is one key. See `confirm.face_removal`. Ticking it on the
	 * Ignore question stops the Remove question too, which is why the sentence beside it is worded
	 * for the stronger of the two: nobody should be able to agree to less than what they get.
	 *
	 * Reset every time a question opens, and WRITTEN only after the button is pressed. A box left
	 * ticked on a dialog somebody then cancelled is not an agreement to anything: the same rule
	 * the chip's cross and the delete guard both follow.
	 */
	let stopAsking = $state(false);

	/* Started when the strip is drawn, so the press itself is instant. Until it lands
	   `faceRemovalConfirmSkipped` answers false, which leaves the guard in place: the safe way for
	   an unread answer to fall. */
	$effect(() => {
		if (session.isAdmin) void recallInterfaceState();
	});

	/*
	 * The face a verb was pressed on. Each verb button is handed its own face; what needs
	 * remembering is the destructive pair's, because a confirm dialog is answered after the pointer
	 * has moved on, so the face the question is about has to outlive the press that asked it.
	 */
	let aimed = $state<Sighting | null>(null);

	/*
	 * What can be done to one face from the file it was found in.
	 *
	 * Naming is offered, to an admin. An unnamed face here is almost always one Sift has not
	 * identified yet, sitting in the unidentified pile, and this is the place somebody is actually
	 * looking at it. A face named to a person this account may not see is handled by the rule
	 * below, and an admin may see everybody. Naming here is the same call as from the wall
	 * (`nameFaces`), so the face leaves the pile exactly as it would there.
	 *
	 * Moving belongs to the wall where groups are compared, not to one file. Taking a name off is
	 * offered only where there is a name this account can see: there is no way to refuse an
	 * attribution without naming the person it is to.
	 *
	 * Every verb for one face, built per card, because the card draws them as buttons and a button
	 * must act on the face it is drawn on. `aimed` is set by whichever verb was pressed, and is
	 * still needed: a confirm dialog is answered after the pointer has moved on, so the two
	 * destructive verbs leave behind which face they were about.
	 *
	 * Whether a face waits in a group that can be opened needs both halves. The server sends a pile
	 * only for a face with no name, and sends the pile's status only while that pile still exists;
	 * grouping rebuilds piles, so an id with no status is a group that is gone.
	 */
	function verbsFor(face: Sighting): Verb[] {
		const grouped = groupOf(face);
		const aim = (act: () => void) => () => {
			aimed = face;
			act();
		};
		/* Every face is handed every verb, and the ones it cannot take are drawn disabled with their
		   reason: the card is one shape across the strip, so the same press sits in the same place
		   on every face, and a press that is missing on one card is a press nobody learns. */
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

	/*
	 * The two destructive verbs, each through the same guard.
	 *
	 * Written as two three-line functions rather than one taking which dialog to open, because what
	 * they share is the QUESTION (one key, one box, one sentence) and not the act. A single
	 * `ask(kind)` would be one call site standing for two different verbs, which is how the wrong
	 * one comes to be run the day a third arrives.
	 *
	 * The skip check reads the account's answer, so somebody who ticked the box on the other
	 * question is not asked here either. `stopAsking` is cleared on the way in: see its note.
	 */
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

	/*
	 * Open the naming sheet, and ask for the list it draws. `personChoices` reads
	 * `peopleStore.items`, a cache nothing on a file's page fills, so without this the sheet would
	 * open on "Nothing matched." with nothing typed. Asked on opening rather than on mount, because
	 * most files are never named from here.
	 */
	function askWhoThisIs(): void {
		void peopleStore.load();
		naming = true;
	}

	/*
	 * Whether a face is turned past the quality bar's angle: kept so somebody can name it, asked
	 * about by Sift and never named by it alone. The server sends it on a file's own faces only.
	 */
	function turnedAway(face: Sighting): boolean {
		return face.turned === true;
	}

	/* What the turned mark says, in its tooltip and to a screen reader. A turned face is matched
	   like any other; what sets it apart is that it joins no group and never becomes a reference. */
	const TURNED = 'Turned away from the camera';
	/* Why a press on a face card cannot act, in the strip's own words. */
	const NOT_IN_A_GROUP = 'Not in a group';
	const NOT_NAMED = 'Not named yet';

	/*
	 * The group a face waits in, where there is one that can be opened, and its address.
	 *
	 * One answer read by every door onto the group: the verb, the press on a photograph's card and
	 * the unnamed face's name. The server sends a pile only for a face with no name, and sends the
	 * pile's status only while that pile still exists; grouping rebuilds piles, so an id with no
	 * status is a group that is gone, and no door is drawn to it.
	 */
	function groupOf(face: Sighting): Sighting | null {
		return face.pile_id && face.pile_status ? face : null;
	}

	/* The address is built by the one function that knows where a pile lives, so no door here can
	   send somebody to the open queue for a group that was set aside. */
	function groupHref(face: Sighting): string | null {
		const grouped = groupOf(face);
		return grouped?.pile_id ? pileHref({ id: grouped.pile_id, status: grouped.pile_status }) : null;
	}

	/* To the pile this face is waiting in. Navigated rather than drawn as an anchor because a
	   declared verb runs a handler: that is what a `Verb` is, drawn as a button or a menu row.
	   The name, which is not a verb, is the anchor to
	   the same address. */
	async function openTheGroup(face: Sighting): Promise<void> {
		const href = groupHref(face);
		if (href) await goto(href);
	}

	/** The server's own answer for a typed name. See `peopleNamed`. */
	const searchPeople = async (typed: string) => (await peopleNamed(typed)).map(personRow);
	/*
	 * Through `personRow`, the one rule for what a person's row shows (the same face the Add to
	 * flyout and the faces box draw), rather than a bare id and name: the sheet draws a picture
	 * column whenever it is given a kind, and a row without one is a letter where a face belongs.
	 */
	const personChoices = $derived(peopleStore.items.map(personRow));

	/*
	 * Name the aimed face. One face and one person, so the first ticked is the answer.
	 *
	 * The whole group goes with it. The face is often one of many already grouped by clustering at
	 * import, and the group is a claim that they are one person. The rest are offered, not decided:
	 * they arrive on that person's wall as suggestions to agree with or take the name off, because
	 * confirming unseen faces into the gallery a person is recognized by would degrade it quietly.
	 *
	 * `changed` is read rather than assumed: the server skips a face that already carries somebody,
	 * so a call can write nothing and still answer 200.
	 */
	async function nameIt(chosen: Choice[]) {
		const face = aimed;
		const who = chosen[0];
		if (!face || !who || busy) return;
		busy = true;
		try {
			const done = await nameFaces([face.track_id], { personId: who.id }, true);
			if (done.skipped > 0) {
				// Before the "already named" reading below, which is what a change of nought
				// otherwise means, and a face left out by a locked vault is not that.
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

	/*
	 * Make the person and hand them back, with no toast here: the sheet finishes on Create and then
	 * writes what it was for, so what happened is reported once, by whatever the sheet went on to
	 * do.
	 */
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

	/* The box is written only HERE, after the button has been pressed, and it is written before the
	   act rather than after it: what was agreed to is "stop asking", which is true whether or not
	   the call that follows succeeds. A tick on a dialog somebody then cancelled is not an agreement
	   to anything, which is why `stopAsking` is cleared on the way in and read only on the way out. */
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

	/*
	 * The file this strip holds, as a string that only moves when the file does. A caller may hand
	 * `id` through an object that is replaced whenever its record is read again (a view counted,
	 * a job's bell), and a prop read through it re-runs everything keyed on it even though the id
	 * is the same; a derived stops at an equal string, so the strip below empties only for another
	 * file and never blinks under a playing clip.
	 */
	const heldId = $derived(id);

	$effect(() => {
		// A different asset is a different set of faces, so what was held for the last one goes
		// before the request rather than when it lands: otherwise the previous file's faces sit
		// under the new file's picture for as long as the fetch takes.
		const wanted = heldId;
		faces = [];
		void facesOf(wanted).then((found) => {
			if (wanted === heldId) faces = found;
		});
	});

	/*
	 * And whenever something this strip draws moves: a pass over the library attaches names to
	 * faces long after the scan that found them, and the cards must drop "Not named yet" without a
	 * page refresh.
	 *
	 * `reloadOnLibraryChange` rather than `arrivals`: arrivals ring on every beat of an import,
	 * while the library bell means what you may see, or what is on it, has moved, which is what a
	 * name landing on a face is. The server rings it once for a whole pass (see
	 * `FaceService.rematch`). `reload` rather than the effect above, because this is the same file
	 * and blanking the strip first would flash it empty.
	 */
	reloadOnLibraryChange(() => void reload());

	/*
	 * One row that scrolls sideways, with an arrow at each end while there is more that way: a
	 * fixed height whatever the number of sightings.
	 *
	 * The state machine (measuring both ends, nudging by one face, the eased hold) is `SideScroll`
	 * in `common/`, shared with the lookalikes strip; what stays here is this strip's own arrows
	 * and their words. `Scroller horizontal` owns the scrolling and the sideways wheel, which a
	 * strip cannot do for itself since most mice have one wheel.
	 */
	const strip = new SideScroll();

	$effect(() => strip.watch());
</script>

<!--
	One face, across rather than down: the crop hard against the left and as tall as the card, the
	name at the top, and what can be done to this face along the bottom. Content top and left,
	actions right and bottom, as the whole app is arranged.

	The verbs are on the card because a right-click on a strip of small pictures is a gesture nobody
	discovers. There is no context menu beside them: every verb this face has is a labelled button,
	and the card has no selection to address, so a second door onto the same rows would be a gesture
	to teach for nothing. A right-click gets the browser's own menu.

	A snippet rather than the markup written twice, so the copies cannot drift.
-->
{#snippet picture(face: Sighting)}
	<img src={cropUrl(face)} alt="" loading="lazy" />
{/snippet}

<!--
	The name and when, laid OVER the card's press rather than inside it, because the name goes
	somewhere of its own: a person's name is a link to their page, the same
	address the People chips above use, and an unnamed face's words open its face group, the same
	address "Show the face group" goes to. A link cannot sit inside the press (an anchor in a button
	is markup browsers take apart), so the column is a sibling on the card's own grid cell and lets
	every pointer through to the press except on the name itself: the ground around the words,
	the time under them and the picture all still play, or open the group.

	A face with no name and no group has nowhere to go, so its words stay words.
-->
{#snippet nameAndWhen(face: Sighting)}
	{@const grouped = groupHref(face)}
	<span class="beside">
		<!--
			The name, and beside it only the mark for a QUESTION. What somebody reads off a strip under
			a file is who is in the picture; where each name came from is answered in full on the
			review screens, and a label beside most names would be furniture. A name Sift is asking
			about is different: without a mark it would read here as settled, the one screen where a
			question looked like a name. So it wears the mark the faces screens' tabs and cards use.
		-->
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
	A face turned too far from the camera is marked as such: it is matched like any other face, but
	it joins no group and never becomes a reference, so a turned face with no name is not Sift
	having missed somebody in the picture. A mark beside the name with the words in its tooltip, as
	the question's mark is, rather than a line of its own: a line under one name would push that
	card's presses below every other card's.
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
		The whole card is the press, and the name and the verbs are raised over it: a card is one
		object to whoever is looking at it, so the ground beside the name opens it too and shares its
		hover state, while the name itself, where it goes somewhere, goes there. `Pressable`, not `Button`, because the picture decides the shape and a button would
		put its own padding and minimum height around the crop.

		The verbs are a sibling of the press, not a child, as on the media tile: a button inside a
		button is markup browsers disagree about, and the card's press would swallow every click
		meant for a verb. The frame raises them over the card, so a press on a verb is never a press
		on the card.

		The room they need is taken out of the card by `.beside`, from the verbs' own size and count
		rather than a number written here; see the stylesheet.
	-->
	<div
		class="card"
		class:acting={acts.length > 0}
		style:--verbs={acts.length}
		style:--apart={destroys.length > 0 && keeps.length > 0 ? 1 : 0}
	>
		{#if seekable && onseek}
			<!-- `Pressable` and not `Button`: the content decides the shape, which is exactly the line
			     those two are separated along. A button would put its own padding and minimum height
			     around a card built out of a 4rem crop. -->
			<!-- From the moment of the PICTURE on the card, not the first frame the face was seen in:
			     the picture is the clearest view of the face, and pressing it means "play this". The
			     range beside the name still says where the face is on screen. -->
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
			<!-- A face on a photograph, which cannot be played to. Its card presses through to the
			     face's group (what its first icon does), so every card is a press with the same
			     ground whatever the file is. -->
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
			<!-- A photograph whose face is in no group: nothing to press through to, so no ground
			     that would answer a pointer with nothing. -->
			<div class="whole">{@render picture(face)}</div>
		{/if}
		{@render nameAndWhen(face)}
		{#if acts.length > 0}
			<!--
				The declared verbs, as glyphs with their words in a tooltip: four labelled buttons
				under the crop would be wider than the strip. Each carries its label as its
				accessible name, so the tooltip and the name say the same thing.

				The same five on every face, in the same places: one a face cannot take is drawn
				disabled with its reason in the tooltip (a face in no group, a face with no name),
				never left out, so the presses stand on one line across the strip.

				Discard and Delete say what they will do, not only their names, since both take the
				face out of the strip and only one can be taken back. Their sentence is
				`ONE_FACE_SAYS`, the same words the question under each press states, so tooltip and
				dialog cannot disagree. The accessible name stays the verb's label; the sentence
				reaches a screen reader as the description through `aria-describedby`.
			-->
			<!-- At the button's own glyph size. The one that destroys something comes last, a
			     wider step away from the rest, so it is never the one reached for by habit. -->
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

<!--
	The offer to stop being asked, drawn under the buttons on BOTH questions.

	One snippet and not two copies, for the reason this whole component exists: the pair would drift,
	and a box that says one thing on one question and another on the other is a box nobody can have
	agreed to. The sentence is worded for the STRONGER verb because one tick covers both, so what is
	agreed to is the claim that is true of deleting a face.

	The whole ROW is the control rather than the 16-pixel box, which is how every guard in the app
	draws this: a tick beside a sentence is a thing to aim at, and what people press is the words.
-->
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
	<!--
		THE SHARED BUTTON, not a hand-drawn one, and it is NAMED rather than hidden.

		`Scroller`'s own arrows are `aria-hidden` with no tab stop, on the reasoning that a menu's
		rows are walked by the arrow keys and each is brought into view automatically, so the arrow
		there is a shortcut for a hand and nothing else. That does not carry across: these are two
		controls the fence requires drawn with the shared component anyway, and a named one is
		reachable by everybody rather than by a pointer alone. They exist only while there is strip
		that way, so a file with a few faces has no extra stops at all.

		The PACE is the shared one. `SideScroll` holds the arrow down through `nudgeDelay`, which is
		what every arrow in the app accelerates by, so a held arrow here, a held arrow on the strip
		above and a held arrow in a chooser all move at the same rate.
	-->
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
		<!-- Folds under its heading like every section under the picture, remembered in this browser. -->
		<Fold section summary="Who is in this" remember="sift.file.fold.faces" weight={450}>
			<div class="strip">
				{#if strip.canBack}{@render arrow(-1, 'chevron_left', 'Earlier faces')}{/if}
				<Scroller horizontal onviewport={strip.take}>
					<ul>
						{#each faces as face (face.track_id)}
							<li>
								<!-- The card, and nothing wrapped around it. See the note on the snippet for
							     why there is no right-click menu here: the card carries every verb this
							     face has as a button, so a menu offering the same five rows would be a
							     hidden copy of what is already on screen. -->
								{@render crop(face)}
							</li>
						{/each}
					</ul>
				</Scroller>
				{#if strip.canOn}{@render arrow(1, 'chevron_right', 'Later faces')}{/if}
			</div>
		</Fold>
	</section>

	<!-- The same sheet "Add to > Person" opens on a file, for the same reason: one way to say who
	     somebody is, reached from wherever you happen to be looking at them. -->
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

	<!--
		The two questions are one question in two words: one title, one sentence of consequence, the
		verb on the button, and the same offer to stop being asked. The verb on each button is the
		word on the card it was pressed from, so nobody has to re-read a dialog that renamed it.
	-->
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
	/*
	 * No padding of its own: the column holding these owns the gap (`.under` in `AssetView`), so
	 * the distances under the player come from one rule.
	 */
	.faces {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	/* The arrows and the scrolling region, on one line. The arrows take their own width and the
	   region takes the rest; `min-inline-size: 0` is what lets it, because a flex item's floor is
	   its content and the run of faces inside is wider than the box by definition. */
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

	/* ONE ROW. Wrapped, a video with thirty sightings would be four rows of crops between the
	   picture and everything under it. `flex: none` on each face so the run keeps its natural
	   width inside the scrolling box rather than being squeezed to fit it. */
	ul {
		display: flex;
		flex-wrap: nowrap;
		gap: var(--space-2);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	/* Every card as tall as the tallest, so the presses at each card's foot stand on one line
	   across the strip whatever the names above them wrap to. */
	li {
		flex: none;
		display: flex;
	}

	li > .card {
		flex: 1;
	}

	/*
	 * Across. The crop is flush against the left edge and as tall as the card, since the card is
	 * only ever as tall as what is beside the crop and a fixed height would leave ground above and
	 * below the picture.
	 *
	 * The card keeps an edge and a ground so a run of faces shows where one stops and the next
	 * begins. `Panel` has no zero-inset state and must not grow one for this, so the box is drawn
	 * here from the same four tokens it would use.
	 *
	 * It frames both the press and the verbs, which is why it is positioned; it holds no room
	 * inside and lays out one child.
	 *
	 * No `overflow: hidden`: nothing overflows (the press is the card and the picture rounds its
	 * own two left corners), and `--focus-ring` is inset, so there is nothing for a clip to
	 * protect.
	 */
	/*
	 * A GRID OF TWO COLUMNS, the crop's and the name's, with the press spanning both on the one row
	 * and the name's column laid over the second half of it. That is what lets the name be a link
	 * of its own without leaving the card: the press still covers every piece of the card, and the
	 * column over it is sized as it would be inside the press (the crop's width, the gap, then the
	 * column), so the card keeps one shape.
	 */
	.card {
		--crop: 4rem;
		position: relative;
		display: grid;
		grid-template-columns: var(--crop) minmax(0, 1fr);
		column-gap: var(--space-2);
		/* The card's light, its edge under this transparent border (see `--sift-card`). */
		border: 1px solid transparent;
		border-radius: var(--radius-md);
		background: var(--sift-card);
	}

	/* The press IS the card: the crop, the name and every piece of ground between them. `:global`
	   because the class is handed to `Pressable`, which puts it on an element compiled in its own
	   file; the plain `div` the photograph branch draws wears the same class and is laid out the same
	   way, minus everything a press does. The hover is `Pressable`'s own `wash`: the ground steps
	   over `--dur-instant`, which is the register for a surface that HAS a ground. */
	.card :global(.whole) {
		grid-column: 1 / -1;
		grid-row: 1;
		min-inline-size: 0;
		display: flex;
		align-items: stretch;
	}

	/* The name at the top, and whatever is spare under it. The room inside the card is spent HERE
	   rather than on the card, which is the whole arrangement: the picture pays none of it and sits on
	   the edge, and the column beside it pays all of it. */
	/* Over the press, in the second column. Positioned so it paints above the press, which is
	   positioned too; it takes no pointer itself, so a press anywhere on it that is not the name
	   lands on the card underneath. */
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

	/*
	 * THE ROOM THE VERBS NEED, TAKEN OUT OF THE CARD BY THE COLUMN THEY SIT UNDER.
	 *
	 * They are out of the flow, so they size nothing: a card left to the name alone would be "Not
	 * named yet" wide and one line tall, and four 32-pixel buttons would hang off the end of it and
	 * out through the bottom. The column reserves both, and it reserves them from the verbs
	 * THEMSELVES: `--control-height-sm` is the token the small button is built out of, and
	 * `--verbs` is how many this face actually has, which is the same list that drew them. A number
	 * written here instead would be a second answer to "how big is a row of verbs", and it would be
	 * wrong the first time a face carried three of them.
	 */
	.acting .beside {
		padding-block-end: calc(var(--control-height-sm) + var(--space-1));
		min-inline-size: calc(
			var(--verbs) * var(--control-height-sm) + (var(--verbs) - 1) * var(--space-1) + var(--apart) *
				var(--space-3)
		);
	}

	/* Raised over the card, in its bottom corner on the side actions belong on. The end inset is the
	   column's own end padding, so the last verb stops where the name does. */
	.doings {
		position: absolute;
		inset-block-end: var(--space-1);
		inset-inline-end: var(--space-2);
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}

	/* The destructive verbs, a wider step from the rest (the `--apart` reserved above). */
	.apart {
		display: flex;
		align-items: center;
		gap: var(--space-1);
		margin-inline-start: var(--space-3);
	}

	.apart.alone {
		margin-inline-start: 0;
	}

	/* THE CARD'S CORNER ON THE TWO IT SHARES WITH IT, and square on the two inside. The crop sits on
	   the card's edge, so its left corners ARE the card's: a smaller radius there would be a second
	   curve inside the first, which is what the concentric rule refuses. Square on the right because
	   there is no edge there to follow.

	   ONE SIZE AND NOT TWO: the width is the crop's own and the height is the card's, taken by
	   stretching rather than stated. `object-fit: cover` is what keeps it a face while the card is
	   whatever height the name and the verbs make it. */
	img {
		inline-size: var(--crop);
		align-self: stretch;
		border-start-start-radius: var(--radius-md);
		border-end-start-radius: var(--radius-md);
		object-fit: cover;
		background: var(--sift-surface-3);
	}

	/* The name and its mark. A row that WRAPS: the column beside a 4rem crop is as narrow as the
	   verbs under it make it, so a mark kept on the name's line would push the card wider than the
	   strip it sits in. Beside the name where there is room, under it where there is not. */
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

	/* A name that goes somewhere: the name's own ink at rest, underlined on hover and on keyboard
	   focus, the rule every heading that leads onwards follows. It takes the pointer back from the
	   column it sits in. */
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

	/* The tick row under the buttons on both questions. `:global` because the class is handed to
	   `Pressable`, which puts it on an element of its own: the same shape the chip's guard uses. */
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

	/* In the time's own quiet ink, beside the name: a note about the face. It takes the pointer
	   back from the column it sits in, so its tooltip opens. */
	.turned {
		display: inline-flex;
		color: var(--sift-ink-3);
		pointer-events: auto;
	}
</style>
