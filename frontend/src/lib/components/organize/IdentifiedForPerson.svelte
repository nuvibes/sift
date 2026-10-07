<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/*
	 * One person's decided faces, in full, so that part of them can be answered for.
	 *
	 * The wall next door shows a handful of each person because it is drawing a page of them. That is
	 * enough to identify who a card is about and not enough to decide about one face on it, and
	 * deciding about one face is the whole point, because a wrong name is always a wrong name on a
	 * particular appearance rather than on the person.
	 *
	 * Two actions, and they are opposites of each other:
	 *
	 *   Agree:       for a face Sift proposed and has not acted on. This is the one place to say yes
	 *                to a suggestion: a face marked as waiting for somebody is waiting for this
	 *                control.
	 *   Take off:    for a face that is not them. Remembered, so the same suggestion does not come
	 *                round again. The face stays and goes back to being unidentified.
	 *
	 * Pressing a face goes to the file at the moment it was found, which is usually the only way to
	 * tell whether Sift got it right.
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
	 * The three states a named face can be in, drawn as three groups rather than one wall with a
	 * word under every tile.
	 *
	 * One wall repeating three sentences in no order would make "what still wants me" answerable
	 * only by reading all of it. The one that is work leads; the other two are the record of what
	 * is settled, kept apart because "you said so" and "Sift worked it out" are different kinds of
	 * certainty.
	 *
	 * The words are the same on every faces screen: Needs your input, Recognized by Sift (the word
	 * the attach setting's help and History's receipts use), and Confirmed. What a beginner
	 * needs off this row is whether Sift is asking them anything, not where a name came from, so
	 * each tab says that, in the words the people wall and the person's own page use.
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

	/*
	 * How many are in each of the three, from the same answer the page came in.
	 *
	 * The server counts all three on the read it is already doing (see `AppearancePage`), so the
	 * row arrives whole on the first answer rather than filling in a tab at a time; a tab with no
	 * number would read as empty.
	 *
	 * Null until the first answer lands, which `Tabs` draws as no number at all rather than a
	 * zero: a zero that becomes an eight a moment later reports a fault that is not there.
	 */
	let counts = $state<Record<Shown, number> | null>(null);
	let total = $state(0);

	/* Which of the three is showing, from the address so it survives a refresh and a shared link.
	   The guesses lead, because they are the only one of the three that is work. */
	const asked = $derived(SHOWN.find((one) => one.key === page.url.searchParams.get('show'))?.key);
	/*
	 * And when the address names none, the first tab with something on it.
	 *
	 * The guesses suit somebody arriving to work, but a card on the wall next door whose lead is
	 * the nod has nothing waiting, so defaulting to the guesses would open an empty tab. The wall's
	 * own doors name their tab; this is the same rule for a typed address, an old link, or a door
	 * that forgets.
	 *
	 * The guesses still win whenever there are any, because they are the only one of the three that
	 * is work. Nothing in any of them leaves it on the guesses, whose empty state says what this
	 * screen is for.
	 *
	 * `counts` arrives with the first answer whatever was filtered to, so this settles on the
	 * second pass at the latest, and the second pass asks for the same counts: it cannot oscillate.
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
	/* A failure that is NOT "there is nothing here". Different sentences, and only one of them is
	 * about the library. See `isMissing`. */
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
	 * What the verbs on screen are ABOUT: the aimed face, or the selection.
	 *
	 * The two rows that can be dim (agreeing, and taking a name off) are dim when nothing they
	 * apply to is in hand, so they have to be judged against the same faces the menu will act on.
	 * While a menu is open over the bar the bar reads this too, which is a state nobody can see and
	 * which lasts exactly as long as the menu.
	 */
	const aiming = $derived(aimed ? [aimed] : picked);
	/** What the verb somebody chose is about, recorded when they choose it. See the pile's note. */
	let acting = $state<string[]>([]);

	/*
	 * Which tab this person was opened from, when the address says.
	 *
	 * This screen belongs to People Sift can recognize, and the trail should lead back to the tab somebody
	 * pressed. The wall that drew the card writes the tab into the address, and `tabOpenedFrom`
	 * refuses a name that is not a tab of this page, so a typed address cannot draw a trail through
	 * an unrelated screen. The lit tab follows the trail, because the tabs are where you are.
	 */
	const opened = $derived(
		tabOpenedFrom(
			heldBoard.found?.queues ?? [],
			'known-people',
			address.url.searchParams.get('via')
		) ?? 'known-people'
	);

	/*
	 * Whose page this is. Off the answer, not off its first face: a tab with nothing on it has no
	 * first face. Held once and kept across tabs; the name does not change with the filtering.
	 */
	let personName = $state<string | null>(null);

	/*
	 * Which of some faces carry a name nobody has confirmed. Agreeing with a settled one is a no-op
	 * the server tolerates, but the button should say what it will actually do.
	 *
	 * Any unconfirmed name counts, matched as well as suggested: a match is a decision Sift took
	 * without being asked, the one most worth somebody's yes, and `accept_suggestions` on the
	 * server confirms a face as whoever is on its row whatever put them there.
	 *
	 * A function of the ids rather than a value derived from the selection, because two moments ask
	 * it: the row asks "should I be dim?" about what the menu is aimed at, and the action asks
	 * "what am I agreeing to?" about what the verb was handed, by which time the menu has closed
	 * and the aim is gone.
	 */
	const proposalsIn = (ids: readonly string[]) =>
		faces.filter(
			(face) => ids.includes(face.track_id) && face.person_id && face.attribution !== 'confirmed'
		);

	/* Which of some faces have somebody to take off. A face whose person this account may not be
	   told about has no name here and is left out: there is nothing to say no to that could be
	   said without naming them. */
	const namedIn = (ids: readonly string[]) =>
		faces.filter((face) => ids.includes(face.track_id) && face.person_id);

	const agreeable = $derived(proposalsIn(aiming));
	const undoable = $derived(namedIn(aiming));

	/*
	 * What can be done to the faces picked, declared once for the bar and for the menu on each one.
	 *
	 * Naming is absent and that is the whole difference between this wall and the pile's: everything
	 * here already has a name, so the question is whether the name is right rather than what it is.
	 *
	 * The two rows that can be true of nothing picked are drawn dim rather than dropped. Agreeing
	 * applies only to a proposal and taking a name off only to a face that has one, so a selection
	 * of settled faces leaves both with nothing to do, and a row that vanished as the selection
	 * changed would move every other row out from under the pointer.
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
	/* The bar's two halves. "Yes, that is them" is the work of this wall and Remove destroys
	   something, so both keep their words; taking a name off, moving and ignoring go behind the
	   door at the end. The right-click menu still draws `verbs` whole. See `barShape`. */
	const shape = $derived(barShape(verbs));

	/* Right-clicking AIMS at a face without picking it, so the bar does not rise over the menu.
	   As on the pile, for the same reason and with the same care: the aimed face
	   wears the ring, so nothing acts on something that is not on screen. Inside a selection it
	   aims at nothing and the menu still means all twelve. */
	function aimAt(trackId: string) {
		aimed = selection.has(trackId) ? null : trackId;
	}

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

	async function load() {
		if (!personId) return;
		failed = false;
		let overtaken = false;
		try {
			/*
			 * Filtered by the server, per tab, so each tab and the pager under it count the same
			 * population rather than bands cut from one page in the browser.
			 *
			 * Through `fill`, so a resize trims the faces held or asks for the rest only. The
			 * person and the tab are the question: faces served for one are never kept for another.
			 */
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
			/*
			 * Only a 404 says the person has gone, and an empty tab is not one: two of the three
			 * are routinely empty (nobody has agreed to anything yet; Sift has matched nothing),
			 * and reading that as "gone" would tell somebody their faces were gone while the tab
			 * beside it held forty.
			 */
			missing = false;
			settle(page.offset, faces[0]?.track_id);
		} catch (error) {
			// Only a 404 says these have gone. Anything else is a fault, and saying somebody's faces
			// have been taken back when the request merely failed is the worst way to be wrong here.
			missing = isMissing(error);
			failed = !missing;
			faces = [];
			total = 0;
		} finally {
			if (!overtaken) loading = false;
		}
	}

	/* The tab that was showing last time this ran, so a CHANGE of tab can be told from a page turn.
	   Not state: writing it would make the effect below depend on what it writes. */
	let shownLast = show;

	$effect(() => {
		void personId;
		void show;
		void paging.offset;
		void paging.size;
		/* A different tab is a different list, so the page starts again at the top. Without this,
		   moving from a tab you were forty rows into lands forty rows into one that holds five,
		   which the server answers with an empty page, correctly, and which reads as a broken tab. */
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
			// UNTRACKED: this effect's own answer writes the address, and reading it here plainly
			// would make the effect depend on what it causes: the anchor written and deleted twice,
			// settling with nothing.
			paging.arrive(untrack(() => anchorIn(address.url)));
		}
		/* The load UNTRACKED: its dependencies are the ones named above. `fill` reads the paging's
		   anchor before its first await, and tracked, `land` clearing that anchor would re-run this
		   effect and ask again for the page just landed whenever the row resolved to the page
		   already open. */
		untrack(() => void load());
	});

	async function agree() {
		const agreeing = proposalsIn(acting);
		if (agreeing.length === 0) return;
		busy = true;
		try {
			// What the verb was HANDED, not what the menu is aimed at: `agreeable` is derived from
			// `aiming`, which the closing menu has already emptied by the time this runs. The same
			// distinction the note on `proposalsIn` draws.
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

	/* Three decisions every other face is offered, offered here too.
	 *
	 * A face here is a face like any other: the crop can be useless, it can belong to somebody who
	 * is not worth asking about, and it can be in the wrong group. Being named does not change any
	 * of that, and the answer should not live on a different screen, reachable only by taking the
	 * name off first and then going to find it.
	 *
	 * Setting aside and moving take the name off on the way through, because they are decisions
	 * about faces nobody has placed and a face cannot be both listed under somebody and sitting in a
	 * group of strangers. Removing does not: the face goes entirely, so there is nothing left to be
	 * in two states.
	 */
	/**
	 * What every decision on this screen does afterwards.
	 *
	 * The answer is passed in so what was LEFT OUT is said in one place rather than at each of the
	 * four call sites: silent when nothing was, which is why it is unconditional. A face on a file
	 * this account's own locked vault is concealing is skipped by the server and counted; without
	 * this the screen would announce a decision that was only partly made.
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
	 * Yes and no for the tab, on the tab line: over this page, what is picked, or the whole tab.
	 *
	 * Shaped like the group screen's "Add as person" control, word for word where the meaning is
	 * the same: the main half answers what is picked, or the whole page when nothing is, and behind
	 * the chevron each answer says which of the three it is about ("All N on this page", "The N
	 * picked", "All N in this tab"), so nobody refuses a tab meaning to refuse three faces. On the
	 * tab line's free far end rather than a second band of controls above the faces.
	 *
	 * The scope is the server's to honour, not this screen's to fake. It goes up as a declared
	 * parameter (`RunWrite`): a page and a pick send the faces they are about and the server
	 * filters them against what stands for this person on this tab; the whole tab sends none,
	 * because that set is the server's to know. One receipt per press whichever it was, so History
	 * takes the whole press back.
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

	/* The two doors per tab, with the scope in the body: the four in `$lib/people/faces.svelte` take it.
	   Each answers with the receipt its press wrote where the reply carries one, so the toast can
	   offer Undo immediately (agreeing with Sift's matches does not hand its receipt back yet:
	   History takes that one back). */
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
			if (yes) {
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
	 * The two answers opened out into the three things each can be for, in the group screen's own
	 * words (see `PileDetail`'s `pageRows`). "In this tab" where it says "in this group", because
	 * that is the one place the meaning differs: the whole of this is a tab of one person's faces.
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
		/* Read BEFORE the click is applied. Asking afterwards gets the answer for the state the
		   click just produced, so letting go of the LAST selected face takes the count to zero and the
		   press that meant "deselect" opens the file instead. */
		if (gesture.handled(face.track_id, event)) return;
		// At the moment of the picture pressed, not the first frame the face was seen in.
		openAsset(face.asset_id, [], face.picture_ms);
	}

	/*
	 * The three states a named face can be in, in the words the rest of Organize uses. What
	 * somebody is trying to find out is whether anything is asked of them, not where the name came
	 * from: one phrase per state, the same three everywhere.
	 */
	function describe(face: Sighting): string {
		if (face.attribution === 'confirmed') return 'Confirmed';
		if (face.attribution === 'suggested') return 'Needs your input';
		return 'Recognized by Sift';
	}

	/**
	 * What the reference mark says, in the person's own first name: whose face this one helps Sift
	 * find, rather than how the matcher works, and whether Sift took it from its own name. "Them"
	 * where there is no name, and as the pronoun: Sift holds nothing to choose one by.
	 */
	/* One mark for a face Sift named and took as a reference, in the glyph History gives faces. */
	const learned = (face: Sighting) => (face.attribution === 'matched' ? 'learned' : 'reference');

	function learnsFrom(face: Sighting): string {
		const first = (personName ?? '').trim().split(' ')[0];
		if (face.attribution !== 'matched') return `Sift uses this one to identify ${first || 'them'}`;
		const whom = first ? `${first} in this one` : 'this face';
		return `Sift recognized ${whom} and uses it to identify them`;
	}

	/* What lets the press-and-drag SWEEP find this tile.
	 *
	 * `TileGesture` reads the id off whatever is under the pointer: the pointer is somewhere else
	 * entirely by the time it matters, which is the whole gesture, so the id has to be in the DOM.
	 * Without it the hold still picks the tile it started on and the drag then picks nothing, which
	 * reads as the gesture being half-broken. */
	const sweepable = (trackId: string) => ({ [TILE_ID]: trackId });

	/* The three, as the row of tabs. Every tab is a real address on this same page, so the back
	   button steps between them and a link somebody sends opens on the one they were looking at. */
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

<!-- `known-people`, the queue's name, so the trail can find it on the board and step back to the
     tab the person was pressed from. A crumb that cannot be named is not drawn, which is right for
     a board that has not landed yet. -->
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
			Titled with the person's name rather than the page's: this page is about somebody, so
			their name is the page (see `OrganizeHeader`).

			The tabs here are this person's three states rather than the queue's siblings, so they
			are this screen's own `controls` rather than the shared header's band. One page with
			tabs, not three bands each in its own short scrolling box, so the page size is measured
			against one real scrolling region.
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

	<!-- Only while there is nothing on screen yet. A reload after a decision keeps what is
	     already drawn and swaps it when the answer arrives; showing the skeleton again makes the
	     page blink out and back for every single answer, which is the one thing somebody working
	     through a queue does over and over. `EntityGrid` does it this way too. -->
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
		<!-- An empty TAB, which is not an empty person. Two of the three are routinely empty and
		     say so in their own words, because "nothing here" on a screen with a full tab beside it
		     reads as the screen having failed. -->
		<Empty scope="page" icon="person" title={show === 'suggested' ? 'Nothing waiting' : 'None yet'}>
			{show === 'suggested'
				? 'Sift has nothing to suggest for them. Faces Sift is unsure about appear here.'
				: showing.blurb}
		</Empty>
	{:else}
		<!-- No count line: the lit tab beside the heading carries this tab's number, and saying it
		     again under the sentence would be the same figure twice. The tab's Yes and No are on
		     the tab line (see `tabAnswers`).

		     One wall, one scrolling region: the page's own. A capped `Scroller` inside a body that
		     already scrolls nests scrolling regions, and `CardPaging` measures a page against the
		     nearest scrolling ancestor, so a nested box would give it the wrong measurement. -->
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
								The foot of the card: what Sift knows about this face, then when it
								was found.

								The marks sit here beside the timestamp rather than over the crop,
								which is the part being read and judged. The selection tick stays on
								the picture: it is about the card, and must be visible while the
								pointer sweeps across a wall of them.

								The status is the tab rather than a word under every tile: on one
								tab every face has the same one, and repeating it would make the
								wall unreadable at a glance. It stays in the accessible name above,
								where there is no tab to read.
							-->
							<span class="foot">
								<span class="marks">
									<!--
										Whether this face is one Sift matches against.

										Agreeing to a group OFFERS every face in it and several rules can
										decline one, so naming four faces could move the reference count by
										one with nowhere to find out which, which reads as the number
										being broken. Marked only where it is a reference: a mark on the
										few beats a caveat on the many.

										The tooltip says what the mark DOES rather than what it is called:
										"Sift matches against this one" is machinery, and the sentence
										somebody needs is whose face it helps Sift find.
									-->
									{#if face.is_reference}
										<FaceMark kind={learned(face)} label={learnsFrom(face)} decorative />
									{/if}
									<!-- And which of the three states it is in, for the two that are not
									     settled. Nothing at all on a face you confirmed: the mark that says
									     "there is nothing to do here" is the absence of one. -->
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

<!--
	The tab's own answers, at the far end of the TAB LINE: the slot `OrganizeHeader.controls` is,
	and the same place a queue's panel reports its tools to. Nothing on the settled tab: there is no
	act left to offer on a face you already answered for, and a control that can do nothing is
	worse than none.
-->
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

	/* This component dresses its own controls. A base rule on the bare element reaches every button
	   in the app including the ones that are not boxes. See the note in the groups list. */
	/* A face you press, with a caption under it. `Pressable`: what decides the size is the picture,
	   not the control. `none` for feedback because these are in a dense wall and a lift on one
	   shifts its neighbours: the chosen ring is Pressable's own. `:global` throughout, because
	   the class is handed to a component and lands in that component's scope. */
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
		/* The change steps over --dur-instant rather than happening between frames. */
		transition: background var(--dur-instant) var(--ease);
	}

	.faces :global(.face:hover) {
		background: var(--sift-surface-3);
	}

	/* A proposal reads differently from a decision at a glance, so a page of mostly-settled faces
	   shows which few are still asking. */
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

	/* The card's own foot: the marks, then the moment the face was found.
	   Three columns rather than two, so the timestamp stays CENTRED under the crop whether this face
	   wears marks or not: a middle column between two equal edges cannot be pulled off centre by
	   what sits in one of them. */
	.foot {
		display: grid;
		grid-template-columns: 1fr auto 1fr;
		align-items: center;
		gap: var(--space-1);
	}

	/* The marks, at the leading edge. They sit left of the timestamp and nothing else is over
	   there, which is the rule for everything that is not an action. */
	.marks {
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}

	/*
	 * The moment the face was found, under the crop, with its own rule; it must not fall through to
	 * the rule below, which would draw the timestamp as the tick's blue circle over the picture.
	 */
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
