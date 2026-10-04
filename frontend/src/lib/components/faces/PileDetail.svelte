<script lang="ts" module>
	import { counted as countedWords } from '$lib/entity/entity-counts';
	import { thing, type ToastWords } from '$lib/components/common/toast-pieces';

	/* Who they were added to, in the name the answer gives (the row's own spelling, so a name
	   typed in another case reads as the person it landed on). "4 faces have been added to
	   Wren Halloway". Without a name to give, the count alone. */
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
	 * One pile of faces, in full, so that part of it can be answered for.
	 *
	 * The list next door shows a handful of each pile because it is drawing hundreds of them. That
	 * is enough to recognise a pile and not enough to decide about one, and the grouping is
	 * deliberately tuned to split rather than merge, so a pile is usually one person and
	 * occasionally one person plus a stranger. An answer only about the whole pile is, for the case
	 * the tuning is built around, exactly the wrong shape.
	 *
	 * So: every face, the same picking gestures as every other grid in the app, and decisions that
	 * run over what is picked. What is not picked stays a pile.
	 *
	 * Pressing a face goes to the file at the moment that face was found. That is the other half of
	 * what this screen is for: a crop is a hundred pixels of somebody and sometimes the only way
	 * to tell who it is, or whether the crop is any good, is to look at where it came from.
	 *
	 * A component rather than a page, because Ignored needs exactly this screen and a second copy of
	 * it would be two screens drifting apart: one of them getting a fix the other did not. What
	 * differs between the two is small and is read from the pile itself: where the back link goes,
	 * and whether the group can be set aside or restored. Everything else is the same question.
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
	import Hint from '$lib/components/insights/Hint.svelte';
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

	/* Read from the pile rather than passed in by whichever route rendered this. The two can
	   disagree (a group set aside in another tab is still reachable at the address it had) and
	   the pile is the one that knows. */
	let status = $state<PileStatus>('open');

	let faces = $state<Sighting[]>([]);
	let total = $state(0);
	let loading = $state(true);
	let missing = $state(false);
	/* A failure that is NOT the group being gone. Kept apart from `missing` because they are
	 * different sentences and only one of them is about the library. See `isMissing`. */
	let failed = $state(false);
	const paging = new CardPaging(FACES_PER_PAGE, 'faces.group');

	const selection = new Selection();
	const gesture = new TileGesture(selection, () => faces.map((face) => face.track_id));

	let naming = $state(false);
	/** The face a right-click was aimed at, while its menu is open. Not a selection. See `aimAt`. */
	let aimed = $state<string | null>(null);
	/*
	 * What the verb somebody chose is ABOUT, recorded when they choose it.
	 *
	 * Every one of these verbs opens a question (a name to type, a place to move to, a
	 * confirmation) and the answer arrives later. Reading the selection again at that point is
	 * reading it at the wrong moment: a right-click aims at one face without picking it, so the
	 * selection may be empty or hold something else, and the dialog would act on whatever it found.
	 * The component that drew the verb hands it its ids, and that is the one thing that certainly
	 * knows what was pressed.
	 */
	let acting = $state<string[]>([]);
	/* Read once for the screen rather than per candidate. A picker asking as you type would be one
	   request per name per keystroke, for a hint. */
	let strengths = $state<ReferenceStrengths | null>(null);
	let busy = $state(false);
	let confirmAside = $state(false);
	let confirmRemove = $state(false);
	let moving = $state(false);

	const picked = $derived(selection.ordered(faces.map((face) => face.track_id)));

	/*
	 * What can be done to the faces in this pile, declared once for the bar and the menu.
	 *
	 * Naming and moving open something rather than doing something, which is why they are handlers
	 * that raise a flag: who somebody is, and where these should go, are questions with no answer
	 * inside a menu row.
	 *
	 * Restore is the one row here that is not about the faces that are picked: there is no route
	 * that brings back part of a group, so it brings back the whole one, whatever is picked.
	 */
	const verbs = $derived(
		faceVerbs({
			name: (ids) => {
				acting = ids;
				pickTheAimed();
				naming = true;
			},
			move: (ids) => ((acting = ids), (moving = true)),
			setAside: status === 'ignored' ? undefined : (ids) => ((acting = ids), (confirmAside = true)),
			restore: status === 'ignored' ? () => void bringBack() : undefined,
			remove: (ids) => ((acting = ids), (confirmRemove = true))
		}).map((verb) => ({ ...verb, disabled: busy }))
	);
	/* The bar's two halves. "Add as person" is what this screen is for and Delete destroys
	   something, so both keep their words; Move them, Discard and Restore are behind the door at the
	   end. Split by the shared rule (see `barShape`) and the right-click menu still draws
	   `verbs` whole, which is what keeps the two surfaces one list. */
	const shape = $derived(barShape(verbs));

	/** Every face on this page, for the control in the header that acts on the whole page. */
	const onPage = $derived(faces.map((face) => face.track_id));

	/*
	 * The header's control: every verb this pile answers, opened out into the two things it can
	 * be for: the whole page, or what is picked.
	 *
	 * Working through a group one face at a time is the slow way: with the bar rising only over a
	 * selection, "these forty are all her" would mean picking forty. The main half
	 * names what is picked, or the whole page when nothing is; behind the chevron each verb says
	 * which of the two it is about, in words, so nobody deletes a page meaning to delete three.
	 * Restore is the one row without the choice: there is no route that brings back part of a
	 * group, and the bar says so.
	 */
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

	/*
	 * The whole pile, past the page. The page holds two hundred faces and a pile can hold two
	 * thousand, so "all on this page" alone would not be all. The ids
	 * are read from the server when the row is chosen (one request, ids only) and the verb
	 * runs over them exactly as it runs over a page.
	 */
	async function overTheWholePile(verb: Verb) {
		try {
			const ids = await pileTrackIds(pileId);
			if (ids.length === 0) return;
			verb.run?.(ids);
		} catch {
			toasts.show("The whole group couldn't be read. Try again in a moment.", { tone: 'error' });
		}
	}

	/** The main half: name what is picked, or the whole page when nothing is. */
	function nameThese() {
		const chosen = picked.length > 0 ? picked : onPage;
		if (chosen.length === 0) return;
		// The naming field lives on the bar, and the bar rises over a selection, so the faces
		// about to be named are picked first, which also puts a ring on exactly what will change.
		if (picked.length === 0) selection.toggleAll(onPage);
		acting = chosen;
		naming = true;
	}

	/*
	 * Right-clicking aims at a face. It does not pick it, and the bar does not rise: opening a menu
	 * is a question, and raising a toolbar over what you are choosing from is the wrong answer to
	 * it.
	 *
	 * The aimed face wears the same ring a picked one does, so what the menu is about is on screen
	 * and nothing acts on a selection the person cannot see; it is not a selection, so nothing else
	 * treats it as one. Right-clicking inside a selection aims at nothing, and the menu means all
	 * of it.
	 *
	 * Naming is the one verb that still needs the bar, because the field it types into lives there.
	 * It picks what it is aimed at as it opens, so the bar arrives with the form and what gets
	 * named is what was under the pointer.
	 */
	function aimAt(trackId: string) {
		aimed = selection.has(trackId) ? null : trackId;
	}

	/** What the MENU acts on: the aimed face, or the whole selection when the menu was opened in it. */
	const menuIds = $derived(aimed ? [aimed] : picked);

	/** Pick what the menu was aimed at, for the one verb whose form lives in the bar. */
	function pickTheAimed() {
		if (aimed) selection.only(aimed);
		aimed = null;
	}

	/*
	 * WHO THESE ARE, as one page of the library's people.
	 *
	 * From the server and not from `people.items`: that is one page of the WALL, in the wall's own
	 * order, so somebody past it could not be found by typing their name at all. `total` comes back
	 * with the page so the picker can say how many it is not showing: a box that asked for six
	 * names and drew six would leave a seventh person matching what was typed simply not existing
	 * as far as this screen was concerned.
	 */
	async function askPeople(typed: string): Promise<PickPage> {
		const asked = await people.choices(typed);
		return {
			/*
			 * Drawn by their face, as the "Add to" flyout and the People wall draw a person: one
			 * rule, in `$lib/people/person-row`, rather than a name here and a picture there.
			 */
			choices: asked.items.map(personRow),
			more: Math.max(0, asked.total - asked.items.length)
		};
	}

	/*
	 * Somebody the library has never heard of, made and then named.
	 *
	 * Two writes: the person is created here, as the picker's contract ("create one and pick it")
	 * says, and the naming is the ordinary `onpick` path afterwards. Creating somebody is then the
	 * same act as in every other picker (the same row and words, and the new person recorded in
	 * what this account reaches for, which a single `nameFaces(ids, { name })` call could not do
	 * since it never returns the id). The cost is atomicity: a name failing after the person is
	 * created leaves a person with no faces, which somebody can see and delete, not damage.
	 *
	 * It also forgoes the server's name resolution: `/faces/name` resolves a typed name against
	 * existing people before creating one, and `POST /people` deliberately does not (two people may
	 * share a name). The guard is structural: the picker offers its create row only when no row on
	 * the page is called that, and the page is the server's own answer for what was typed, so an
	 * existing person is on screen above the row before the row exists.
	 */
	async function makePerson(named: string): Promise<PickChoice> {
		const made = await people.create(named);
		return { id: made.id, name: made.name };
	}

	/*
	 * Which tab this group was opened from, when the address says.
	 *
	 * One screen draws a group whichever tab it was reached through, so it cannot work this out
	 * itself. The wall writes it into the address (`pileHref`) and `tabOpenedFrom` refuses a name
	 * that is not a tab of this page, so a typed address cannot draw a trail through an unrelated
	 * screen.
	 *
	 * The lit tab follows it for the same reason the trail does: a detail opened from Ignored is
	 * still on Ignored.
	 */
	const opened = $derived(
		tabOpenedFrom(
			heldBoard.found?.queues ?? [],
			'faces-to-name',
			address.url.searchParams.get('via')
		) ?? 'faces-to-name'
	);

	/*
	 * Where this screen leaves for on its own, once a decision has emptied the group or brought it
	 * back: the tab it was opened from (`opened`, above), the place its crumb names. Through
	 * `leaveFor`, so it is the browser's own step back where it can be, and the wall comes back on
	 * its page and scroll.
	 */
	const home = $derived(`/organize/${opened}`);

	/*
	 * Where this wall was left, carried in the address; see `$lib/grid/anchor`.
	 *
	 * `path` is captured once so a background refresh cannot rewrite the address after somebody has
	 * navigated away, and `arriving` is true exactly once: after the first settle the anchor in the
	 * address is one this screen wrote, and honouring it again would start a new question at the
	 * old one's position.
	 */
	const path = address.url.pathname;
	let arriving = true;

	/**
	 * Read the address once, then keep it in step with where the page actually landed.
	 *
	 * Through `land`, never a plain `paging.offset = at`, and in the same synchronous step as the
	 * rows are written (every caller writes them just before calling this). An anchored page is
	 * found by a row and only its answer says what offset that row is at, so the offset moves after
	 * the rows land; moved plainly, the effect watching it would ask again for the page it was just
	 * handed. Landed, the next `fill` answers from the rows held. See `CardPaging.land`.
	 */
	function settle(at: number, first: string | null | undefined) {
		paging.land(at);
		rememberAnchor(address.url, path, first, at);
	}

	/*
	 * Whether this group looks like somebody a facial fingerprints file holds, while making people
	 * from fingerprints is off: then the group's first question is whether to make them a person,
	 * the same question its card asks on the wall. Naming it as somebody else stays the row below.
	 */
	let offer = $state<FingerprintOffer | null>(null);
	/* The person the Yes made, linked until the matching that follows names these faces. */
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
			// Through `fill`, so a re-size trims the faces held or asks for the rest only.
			const page = await paging.fill(
				pileId,
				() => faces,
				(query) => {
					// "Looking..." only when a request goes out: a landing or a trim asks nothing.
					loading = true;
					return faceGroup(pileId, query);
				},
				(answer) => ({ rows: answer.group.faces, total: answer.total, offset: answer.offset })
			);
			// Overtaken by a newer read, which finishes this one's work: the loading line too.
			if (page === null) {
				overtaken = true;
				return;
			}
			faces = page.rows;
			total = page.total;
			if (page.answer) status = page.answer.group.status;
			settle(page.offset, faces[0]?.track_id);
		} catch (error) {
			// Only a 404 says the group has gone. Anything else is Sift's fault and is said as one.
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
			// UNTRACKED: this effect's own answer writes the address, and reading it here plainly
			// would make the effect depend on what it causes: the anchor written and deleted twice,
			// settling with nothing.
			paging.arrive(untrack(() => anchorIn(address.url)));
		}
		selection.clear();
		/* The load UNTRACKED: its dependencies are the ones named above. `fill` reads the paging's
		   anchor before its first await, and tracked, `land` clearing that anchor would re-run this
		   effect and ask again for the page just landed whenever the row resolved to the page
		   already open. */
		untrack(() => void load());
	});

	/*
	 * The one thing read once for the screen, in an effect of its own. Its guard reads the state
	 * the work then writes (`!strengths`), so inside another effect the answer landing would re-run
	 * that effect, refetching the pile and clearing a selection just made. Nothing here clears a
	 * selection or re-reads the pile, so a second pass costs nothing.
	 *
	 * The hint is a hint: if the count cannot be read the picker still names everybody, so a
	 * failure here is swallowed rather than left as an unhandled rejection. `quietly` inside only
	 * absorbs a 404 or a 409; a server error or a dropped connection comes back out.
	 */
	$effect(() => {
		if (!strengths) {
			void referenceStrengths()
				.then((found) => (strengths = found))
				.catch(() => (strengths = null));
		}
	});

	/*
	 * ARRIVED HERE TO NAME THEM, said by the address that brought you.
	 *
	 * The board's face card offers two doors to this screen (Show me, and Add as person) and
	 * they mean different things: one is "let me look", the other is "I know who that is". The
	 * second opens the naming field with the page picked, which is exactly what `nameThese` does for
	 * the button in the header, so it is that function rather than a second arrangement of the same
	 * three lines.
	 *
	 * ONCE, and only once the faces are on screen: the field is opened over a selection, so running
	 * it against an empty page would pick nothing and raise nothing. `asked` is what keeps it to the
	 * arrival: without it, every re-read after a decision would put the field back up under
	 * somebody who had just cancelled it.
	 */
	let asked = false;

	$effect(() => {
		if (asked || !address.url.searchParams.has('name') || faces.length === 0) return;
		asked = true;
		nameThese();
	});

	/* Every decision here empties the selection and re-reads the pile.
	 *
	 * Re-read rather than patched in the browser: naming faces can empty a pile entirely, which
	 * takes the pile itself away, and a screen that worked out for itself what the server would do
	 * is a second opinion about it that will eventually be wrong.
	 */
	/**
	 * What every decision on this screen does afterwards.
	 *
	 * The answer is passed in so what was LEFT OUT is said in one place rather than at each of the
	 * four call sites: silent when nothing was, which is why it is unconditional. A face on a file
	 * this account's own locked vault is concealing is skipped by the server and counted; without
	 * this the screen would announce a decision that was only partly made.
	 */
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

	/* Merge these faces into another group, or split them into one of their own.
	 *
	 * Both go to the same place, because they are the same operation with a different destination.
	 * The grouping is deliberately set to split rather than merge, so its two standing mistakes are
	 * "this pile is really two people" and "these two piles are one person", and this is the
	 * answer to both, without inventing a Person to hold a decision that is not about a name.
	 *
	 * Moving everything out empties this group, which takes it away; `afterDeciding` already leaves
	 * for the wall when nothing is left, so the screen does not sit on a group that has gone.
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

	/* Bring a whole group back out of Discarded.
	 *
	 * Not a confirmation step, unlike setting one aside: this is the undo, and putting a dialog in
	 * front of an undo is asking somebody to be sure about becoming less sure.
	 */
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

	/* A plain click on a face, with nothing picked, opens the file at that moment. Anything else
	   (ctrl, shift, a long press, or a click while something is already picked) is about picking.
	 *
	 * Over this page rather than away from it. Every other grid in the app opens a file in the
	 * player that sits over what you were looking at, and going somewhere else from here would mean
	 * losing the group, and the selection in it, to check one face. Closing the player puts the
	 * group back exactly as it was.
	 *
	 * No neighbours are passed: next and previous mean "the next one in the grid you opened this
	 * from", and the things on screen here are faces rather than files. Two faces can be in one
	 * file, so stepping through them is not stepping through a list of files.
	 */
	function pressed(face: Sighting, event: MouseEvent) {
		/* Read BEFORE the click is applied, not after.
		 *
		 * Asking afterwards gets the answer for the state the click just produced, and letting go of
		 * the LAST selected face takes the count to zero, so the press that was meant to deselect
		 * would read as an ordinary press and open the file. */
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
		<!-- The same shape every screen under Organize wears. The trail is the way back, as on
		     every other screen, and it says where you are as well, leaving the row free for the
		     tabs.

		     Nothing is in this band's `controls`: the group's one control is on the group's own
		     row, directly above the faces it acts on, rather than at the far end of the tabs' row. -->
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

	<!-- Only while there is nothing on screen yet. A reload after a decision keeps what is
	     already drawn and swaps it when the answer arrives; showing the skeleton again makes the
	     page blink out and back for every single answer, which is the one thing somebody working
	     through a queue does over and over. `EntityGrid` has always done it this way. -->
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
		{#if status === 'open'}
			<!-- The second hint of Get to know Sift, once per account, on the first group of faces opened to be
			     named. Not on a set-aside group: there is nothing to name there. -->
			<Hint name="first_pile" />
		{/if}
		{#if status === 'open' && (made || offer)}
			<!-- The fingerprints question, above the group's own row and in its shape: the question
			     on the left, its one press on the right; once answered, who was made, as a link. -->
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
		<!-- The group's own row: how many faces there are on the left, the one control for them on
		     the right. See the header above for why it is here. -->
		<div class="bar">
			<p class="count">{total === 1 ? '1 face' : `${counted(total)} faces`}</p>
			{#if status === 'open'}
				<!-- Second to the fingerprints question while it is asked: one primary press a row. -->
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
				<!-- A discarded group's one control is the way back out. Without it, bringing a
				     group back from its own page would mean picking a face or right-clicking one to
				     find Restore, while the card on the Discarded wall carries the button in plain
				     sight. The same verb and the same words as that card,
				     and the same `bringBack`, which lands on the tab the group was opened from. -->
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

<ActionBar count={picked.length} noun="face" onclear={() => selection.clear()}>
	{#snippet actions()}
		{#if naming}
			<!--
				The same selector the "Add to" flyouts are, with its box at the bottom: `PickMenu`
				pages the whole library, says how many it is not showing, remembers who this account
				reaches for, and creates from a row worded like every other picker's.

				The box sits at the bottom because this bar sits at the foot of the window, so the
				menu opens upwards; a box at the top would be at the far end of the list from the
				button pressed. See `PickMenu`'s `filterAt`.

				`bind:open` is what closes the naming: dismissing the menu (Escape, a press outside)
				puts the bar back to its verbs.

				Opens upwards, said rather than discovered: the box is 480px tall against a bar near
				the bottom edge, and a direction that depends on how much room a window has would
				open one way on a tall screen and another on a short one. The menu touches the
				button; the visible space is the bar's own padding above its buttons (`--space-2`)
				plus the menu's inner `--space-1`.
			-->
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
					filterAt="bottom"
					ask={askPeople}
					onpick={(choice) => void name({ personId: choice.id })}
					oncreate={makePerson}
				>
					{#snippet hint(choice)}
						<!-- How many reference photos this person already has, which is what decides
						     between two names that read identically. See `ReferenceCount`. -->
						<ReferenceCount personId={choice.id} {strengths} />
					{/snippet}
				</PickMenu>
			</MenuButton>
		{:else}
			<!-- The same declared list the right-click menu on every face draws. There is no markup
			     for a verb here, which makes "the bar and the menu offer the same things" true by
			     construction. -->
			<VerbButtons verbs={shape.named} ids={picked} />
		{/if}
	{/snippet}
	{#snippet overflow()}
		<!-- Nothing while the naming box is up: the bar is one question then, and a door onto the
		     verbs beside it would answer a different one. -->
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
	/*
	 * The count and the group's control on one line, the control at the trailing end, so the two
	 * cannot drift apart.
	 */
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

	/* This component dresses its own controls. A base rule on the bare element reaches every button
	   in the app including the ones that are not boxes. See the note in the groups list. */
	/* `:global`, because the class is handed to a component and so is compiled in this file's scope
	   but applied to an element this file does not write. The reset, the focus ring, the hover wash
	   and the chosen ring all come from `Pressable`; what is left here is what this card looks like. */
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
