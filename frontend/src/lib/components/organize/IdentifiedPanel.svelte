<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/*
	 * What Sift has decided lately, gathered by person, and the way to act on it.
	 *
	 * The counterpart of the queue next door: that one is the questions, this is the answers,
	 * filling in as files are scanned and as re-matching attributes faces long after the scan that
	 * found them. A feature that attaches names to files without being asked owes somebody one
	 * place to see what it concluded.
	 *
	 * Gathered by person rather than face by face: one card per face would make thirteen
	 * appearances of one person read as thirteen separate answers, and somebody with two faces
	 * waiting would look like somebody with none. The card carries that number because it is the
	 * one thing here worth acting on.
	 *
	 * Ordered by the most recent decision about each person, which is what it is for; grouping must
	 * not turn it into an alphabetical list.
	 *
	 * Which of these Sift did by itself is the mark on a card: what Sift read on its own, with how
	 * close the best came, and what somebody confirmed, so a mixed card says both. There is no
	 * filter over the wall by how a face was named, since every card already carries both numbers.
	 *
	 * The one narrowing is by what Sift knows a person FROM. A library linked to a stash-box holds
	 * hundreds of People known from starter pictures alone (a stash-box's photos: Sift may ask
	 * about them and never names them on their strength), drawn among the few it knows from
	 * pictures of their own. The control on the tab line sets them apart, where Faces to name keeps
	 * its small groups: one press shows only them, the other everybody else, each with its count.
	 *
	 * And the tab's search box, the walls' own (`WallControls`), beside that control on the tab
	 * line. Its words live in the address as `who` (`FACES_WORDS`) and go to the server, which narrows
	 * the wall by the person's name and aliases before the page is taken, so the pager, the tab's
	 * count and the two counts on the control all describe what was found.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import Chip from '$lib/components/common/Chip.svelte';
	import type { OnTools } from '$lib/components/organize/OrganizeHeader.svelte';
	import FaceCovers from '$lib/components/faces/FaceCovers.svelte';
	import {
		ActionBar,
		Button,
		ContextMenuGroup,
		ContextMenuItem,
		Empty,
		Selection,
		Skeleton,
		SplitButton,
		TileGesture
	} from '$lib/components/common';
	import type { OnPaging } from '$lib/components/common/Pager.svelte';
	import WallControls from '$lib/components/entity/WallControls.svelte';
	import { FACES_WORDS, WallWords, emptyWallSays, wordsIn } from '$lib/components/shell/wall-words';
	import { onDestroy } from 'svelte';
	import { untrack } from 'svelte';

	import { goto } from '$app/navigation';
	import { page as address } from '$app/state';
	import { anchorIn, rememberAnchor } from '$lib/grid/anchor';
	import { CardPaging } from '$lib/grid/cards.svelte';
	import { answered } from '$lib/organize/organize.svelte';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { thing } from '$lib/components/common/toast-pieces';
	import RecognitionStrength from '$lib/components/RecognitionStrength.svelte';
	import {
		CROPS_ON_A_CARD,
		IDENTIFIED_PEOPLE_PER_PAGE,
		confirmLookAlikes,
		confirmMatches,
		identifiedPeople,
		referenceStrengths,
		rejectLookAlikes,
		rejectMatches,
		type IdentifiedPerson,
		type ReferenceStrengths,
		type StartersShow
	} from '$lib/people/faces.svelte';

	/*
	 * No filtering on this wall by how a face was named. Every card already says both numbers in
	 * the same words a tab would use, so a tab filtering to one half would narrow cards to a figure
	 * none of them hides. The split worth having is on a person, which is what that person's own
	 * screen's tabs are.
	 *
	 * `?show=` is not read here. It is still a live key on the person's screen (a card's own door
	 * names a tab with it), so nothing anybody saved stops working.
	 *
	 * `?starters=` is: the People known from starter pictures alone, `only` them or `without` them,
	 * in the address as the small groups are next door, so it survives a refresh and Back.
	 */
	const starters = $derived<StartersShow | null>(
		((value) => (value === 'only' || value === 'without' ? value : null))(
			address.url.searchParams.get('starters')
		)
	);
	/* The words the wall is searched by, read from the address; the box writes them there. */
	const addressWords = $derived(wordsIn(address.url, FACES_WORDS));
	const words = new WallWords(FACES_WORDS);
	let term = $state(untrack(() => wordsIn(address.url, FACES_WORDS)));
	/* Words that reach the address any other way than the box (Back, a link) are put in the box;
	   the box's own write is not handed back to somebody still typing. See `WallWords`. */
	$effect(() => {
		const arrived = addressWords;
		untrack(() => {
			if (!words.echoed(arrived)) term = arrived;
		});
	});

	/* How many People each press shows, from the server's last answer. */
	let startersOnly = $state(0);
	let others = $state(0);

	let people = $state<IdentifiedPerson[]>([]);
	let total = $state(0);
	let loading = $state(true);
	const paging = new CardPaging(IDENTIFIED_PEOPLE_PER_PAGE, 'organize.people-sift-knows');

	/** Where the pager goes: the frame's foot, drawn by the route. See `PagerProps`. And where the
	 *  starter pictures' control goes: the far end of the tab line, drawn by the route. See
	 *  `OnTools`. */
	let { onpaging, ontools }: { onpaging?: OnPaging; ontools?: OnTools } = $props();
	$effect(() => {
		onpaging?.(paging.asPager(people.length, total, 'people'));
	});
	/* The control, while there is anybody to set apart or a narrowing to step out of. */
	$effect(() => {
		ontools?.(tabTools);
	});
	onDestroy(() => {
		onpaging?.(null);
		ontools?.(null);
	});
	let busy = $state<string | null>(null);

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
	let arrivedFor: string | null = null;

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
		// Everybody, unfiltered: the wall itself does not filter (see the note above). Through
		// `fill`, so a resize trims the cards held or asks for the rest only; see `CardPaging`.
		const narrowing = starters;
		const asked = addressWords;
		const page = await paging.fill(
			`${narrowing ?? ''}:${asked}`,
			() => people,
			(query) => {
				// "Looking..." only when a request goes out: a landing or a trim asks nothing.
				loading = true;
				return identifiedPeople(query, narrowing, asked);
			},
			(answer) => ({
				rows: answer.people,
				total: answer.total,
				offset: answer.offset
			})
		);
		// Overtaken by a newer read, which finishes this one's work.
		if (page === null) return;
		people = page.rows;
		total = page.total;
		// A landing answers from the rows held and brings no answer: the counts are then the ones
		// already on the control.
		if (page.answer) {
			startersOnly = page.answer.starters_only ?? 0;
			others = page.answer.others ?? 0;
		}
		// The first card may be the nameless one: everybody this account may not be told about,
		// gathered under a single card with no id. `rememberAnchor` writes nothing for it, which is
		// the point: a card that exists in order to have no name cannot be named in an address.
		settle(page.offset, people[0]?.person_id);
		loading = false;
	}

	$effect(() => {
		void paging.offset;
		void paging.size;
		/* Arrived again whenever the narrowing moves: a page of the People known from starter
		   pictures is not a page of everybody, and the anchor in the address is one of THIS
		   narrowing's, because the address it was written onto is this narrowing's. */
		const narrowing = `${starters ?? ''}:${addressWords}`;
		if (arriving || narrowing !== arrivedFor) {
			arriving = false;
			arrivedFor = narrowing;
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

	/*
	 * Where a card's faces are looked at, naming the tab rather than just the person.
	 *
	 * The door names what it opens: the tab the card's own lead act is about. A card that leads
	 * with the nod has nothing waiting, and the person's screen defaults to the faces that need an
	 * answer, so an address with no `show=` would open an empty tab while the card's count said
	 * six. The screen at the other end also falls back to a tab with something in it when an
	 * address names none (see `IdentifiedForPerson`): two halves of one rule, each written where
	 * its half is known.
	 */
	/* Their first name for the three lines ("Sift recognized 14 as Ada"), "them" without one. */
	function firstOf(person: IdentifiedPerson): string {
		return person.person_name?.trim().split(/\s+/)[0] || 'them';
	}

	function facesHref(person: IdentifiedPerson): string {
		const id = encodeURIComponent(person.person_id as string);
		return `/organize/known-people/${id}?show=${person.waiting > 0 ? 'suggested' : 'matched'}`;
	}

	/* And again whenever a share or a restrict moves. This wall is scoped: an appearance in a file
	   this account may not see is left out of it, so what belongs on the screen changes without
	   anything being imported and with nothing else to announce it. */
	reloadOnLibraryChange(() => void load());

	/*
	 * Agree with everything Sift proposed for one person, in one press.
	 *
	 * Asked of the person rather than of the faces on the card: a card draws twelve crops of
	 * however many are standing, so agreeing from its own list would settle only those twelve. The
	 * server knows what is standing for somebody.
	 */
	async function agree(person: IdentifiedPerson) {
		if (!person.person_id || person.waiting === 0 || busy) return;
		busy = person.person_id;
		try {
			const answer = await confirmLookAlikes(person.person_id);
			const faces = answer.changed === 1 ? 'One face is ' : `${counted(answer.changed)} faces are `;
			const who = thing('person', person.person_id, person.person_name ?? 'them');
			const more = answer.offered > 0 ? `. ${counted(answer.offered)} more were suggested.` : '';
			toasts.show([faces, who, more], { tone: 'success' });
			answered.changed();
			await load();
		} catch {
			toasts.show("Those couldn't be confirmed", { tone: 'error' });
		} finally {
			busy = null;
		}
	}

	/* And agree with everything Sift MATCHED on its own, which is the other half of the card.
	 *
	 * A different question from the one above and the reason the card offers two acts: a proposal
	 * is Sift asking, and a match is Sift having already decided and put a name on files without
	 * being asked. Saying those are right is what turns arithmetic into somebody's own answer,
	 * and every one of them then teaches Sift what that person looks like.
	 */
	async function agreeToMatches(person: IdentifiedPerson) {
		if (!person.person_id || person.matched === 0 || busy) return;
		// The card's own id, as the two presses beside it use; the lead is one shape, so a second
		// key would be a state nothing reads.
		busy = person.person_id;
		try {
			const answer = await confirmMatches(person.person_id);
			toasts.show(
				answer.confirmed === 1
					? 'One match is confirmed'
					: `${answer.confirmed.toLocaleString()} matches are confirmed`,
				{ tone: 'success' }
			);
			answered.changed();
			await load();
		} catch {
			toasts.show("Those couldn't be confirmed", { tone: 'error' });
		} finally {
			busy = null;
		}
	}

	/*
	 * And say the same set is not them, the other answer to the card's own question.
	 *
	 * Two doors, chosen by the same rule the lead is: the guesses are refused where they were
	 * proposed and the matches where they were matched, so a card offering to agree with thousands
	 * of faces in one press offers the other direction too.
	 *
	 * Both write a receipt, so History can take the whole press back; that is what makes a bulk
	 * refusal safe on a card.
	 */
	async function refuse(person: IdentifiedPerson) {
		if (!person.person_id || busy) return;
		const matches = person.waiting === 0;
		if (matches ? person.matched === 0 : person.waiting === 0) return;
		busy = person.person_id;
		try {
			const answer = matches
				? await rejectMatches(person.person_id)
				: await rejectLookAlikes(person.person_id);
			toasts.show(
				answer.changed === 1
					? 'One face is no longer theirs'
					: `${answer.changed.toLocaleString()} faces are no longer theirs`,
				{ tone: 'success' }
			);
			answered.changed();
			await load();
		} catch {
			toasts.show("Those names couldn't be cleared", { tone: 'error' });
		} finally {
			busy = null;
		}
	}

	/* How reliably Sift can identify each of these people, read ONCE for the screen.
	 *
	 * One request rather than one per card: the bar on a person's own page asks about that person,
	 * and twenty-four cards asking it twenty-four times is an N+1 read. The same read the naming
	 * picker on the groups wall makes.
	 */
	let strengths = $state<ReferenceStrengths | null>(null);
	$effect(() => {
		// `answered.stamp` is read so the bars move when a press here makes new reference pictures,
		// which is the whole point of the two acts below.
		void answered.stamp;
		void referenceStrengths()
			.then((found) => (strengths = found))
			.catch(() => (strengths = null));
	});

	/*
	 * What Sift knows of one person, in three numbers.
	 *
	 * One reading, drawn in this order: what you confirmed, the only thing that teaches Sift; what
	 * Sift named on its own with what it was taught; and what needs your input. The person's own
	 * page says the same three in the same words, the same reading asked for one person there.
	 *
	 * A zero is absent rather than drawn, except the last: "Nothing needs your input" is what
	 * somebody looks for on a card with nothing waiting, saying what is left rather than that the
	 * card is fine.
	 *
	 * Picking several people at once, by the gesture every other wall of tiles uses.
	 *
	 * The action is the same one the card carries, and deliberately the only one: agreeing. Taking
	 * a name back is done face by face on the person's own page, where the face is visible; a "no"
	 * over a whole selection would refuse decisions without showing them.
	 *
	 * Only people with something waiting can be picked: a card with nothing waiting has no action,
	 * and including it would make the button's count disagree with the count picked.
	 */
	const selection = new Selection();
	const selecting = $derived(selection.count > 0);
	const pickable = $derived(
		people.filter((one) => one.waiting > 0 && one.person_id).map((one) => one.person_id as string)
	);
	const gesture = new TileGesture(selection, () => pickable);
	const picked = $derived(selection.ordered(pickable));
	const waitingPicked = $derived(
		people
			.filter((one) => one.person_id && picked.includes(one.person_id))
			.reduce((run, one) => run + one.waiting, 0)
	);

	/* Let go whenever the wall changes underneath: a page turn or a resize. A selection carried
	   across either of those is a set of cards that are no longer on screen, and the bar would then
	   offer to agree to people nobody can see. */
	$effect(() => {
		void paging.offset;
		void paging.size;
		void starters;
		selection.clear();
	});

	/** Where a press of one half goes: into it, or out of it again when it is the one showing. */
	function startersHref(which: StartersShow): string {
		return which === starters ? path : `${path}?starters=${which}`;
	}

	function onEscape(event: KeyboardEvent) {
		// Ctrl+Z takes back the last thing PICKED, Ctrl+Shift+Z picks it again. It touches no
		// data and never reaches the server. See `TileGesture.undoKeys`.
		if (gesture.undoKeys(event)) {
			event.preventDefault();
			event.stopPropagation();
			return;
		}
		if (event.key !== 'Escape') return;
		if (gesture.escaped(event)) event.stopPropagation();
	}

	/** Agree with everything proposed for every person picked. */
	async function agreeToPicked() {
		if (picked.length === 0 || busy !== null) return;
		busy = 'selection';
		let agreed = 0;
		try {
			for (const id of picked) {
				// Asked of the PERSON, exactly as the card's own press is: a card holds twelve crops
				// of however many are standing, so agreeing from its list settles twelve of them.
				const answer = await confirmLookAlikes(id);
				agreed += answer.changed;
			}
			selection.clear();
			toasts.show(agreed === 1 ? 'One face is named' : `${counted(agreed)} faces are named`, {
				tone: 'success'
			});
			answered.changed();
			await load();
		} catch {
			toasts.show("Those couldn't be confirmed", { tone: 'error' });
		} finally {
			busy = null;
		}
	}
</script>

<svelte:window onkeydown={onEscape} />

<!-- The two halves of the wall by what Sift knows each person from, at the far end of the tab
     line: the one showing is the accent, and pressing it again shows everybody. -->
<!-- The tab line's end: the search box, then the starter pictures' control while there is anybody
     to set apart or a narrowing to step out of. -->
{#snippet tabTools()}
	<span class="tab-tools">
		<WallControls
			noun="person"
			plural="people"
			bind:term
			onsettled={(typed) => words.write(address.url, typed)}
		/>
		{#if startersOnly > 0 || starters !== null}{@render startersChips()}{/if}
	</span>
{/snippet}

{#snippet startersChips()}
	<span class="starters">
		<Chip
			shape="square"
			tone={starters === 'only' ? 'accent' : 'quiet'}
			selected={starters === 'only'}
			href={startersHref('only')}
		>
			Starter pictures only
			{#snippet trail()}<span class="data">{counted(startersOnly)}</span>{/snippet}
		</Chip>
		<Chip
			shape="square"
			tone={starters === 'without' ? 'accent' : 'quiet'}
			selected={starters === 'without'}
			href={startersHref('without')}
		>
			Everyone else
			{#snippet trail()}<span class="data">{counted(others)}</span>{/snippet}
		</Chip>
	</span>
{/snippet}

<section class="panel">
	<!-- Only while there is nothing on screen yet. A reload after a decision keeps what is
	     already drawn and swaps it when the answer arrives; showing the skeleton again makes the
	     page blink out and back for every single answer, which is the one thing somebody working
	     through a queue does over and over. `EntityGrid` does it this way too. -->
	{#if loading && people.length === 0}
		<Skeleton lines={3} />
	{:else if people.length === 0 && addressWords}
		<!-- A search that found nobody says so: a wall narrowed to nothing is not an empty one. -->
		<Empty scope="page" icon="search">{emptyWallSays('people', addressWords, false, '')}</Empty>
	{:else if people.length === 0}
		<Empty scope="page" icon="person" title="Nobody identified yet">
			People appear here as their faces are named. Start by naming a group under Faces to name.
		</Empty>
	{:else}
		<!-- No count of its own: the tab says how many, and the pager says it again under the
		     cards, as on every other tab of this page. -->
		<ul class="people" {@attach paging.cards}>
			{#each people as person, at (`${at}:${person.person_id ?? 'nameless'}`)}
				<li class="person">
					<!-- The thumbnails are the link, exactly as on the group wall: a card shows a handful
					     and checking a decision usually needs all of them, at the moment in the file
					     where each was found. Somebody this account may not be told about has no page
					     to open, so their card is not a link. -->
					{#if person.person_id}
						<FaceCovers
							faces={person.faces}
							most={CROPS_ON_A_CARD}
							href={facesHref(person)}
							label={`Open every face named ${person.person_name ?? 'as them'}`}
							picked={selection.has(person.person_id)}
							onpointerdown={(event) => gesture.pressStart(person.person_id as string, event)}
							onpointerup={() => gesture.pressEnd()}
							onclickcapture={(event) => gesture.clicked(person.person_id as string, event)}
							sweepId={person.person_id}
						/>
					{:else}
						<FaceCovers faces={person.faces} most={CROPS_ON_A_CARD} />
					{/if}

					<!-- A card with no id on THIS wall is the concealed gather and can be nothing else.
					     Every face here is already attached to somebody; a face nobody has named is a
					     question and lives on the other tab. So the server withholds the name AND the
					     id together, and everybody this account may not be told about arrives as one
					     nameless card. "Not named" would describe the opposite state (somebody Sift
					     found and nobody has named), so the one card that means "there is a name and
					     it is not yours to see" takes the word the vault uses everywhere else. -->
					{#if person.person_id}
						<a class="who" href={`/people/${person.person_id}`}>{person.person_name}</a>
					{:else}
						<p class="who">Hidden</p>
					{/if}
					<!-- The three numbers, in this order and these words. See the note above the
					     selection for why they are one reading. -->
					<p class="counts">
						{#if person.confirmed > 0}
							<span class="one"
								>You confirmed {person.confirmed.toLocaleString()} as {firstOf(person)}</span
							>
						{/if}
						{#if person.matched > 0}
							<span class="one"
								>Sift recognized {person.matched.toLocaleString()} as {firstOf(person)}</span
							>
						{/if}
						{#if person.waiting > 0}
							<!-- Singular said in full rather than a plural with a number in front of it:
							     "1 need your input" is the easy fault for a count, and it is the state a
							     card is in one press from empty. -->
							<span class="one pending">
								{person.waiting === 1
									? '1 awaiting your confirmation'
									: `${person.waiting.toLocaleString()} awaiting your confirmation`}
							</span>
						{:else}
							<span class="one settled">Nothing needs your input</span>
						{/if}
					</p>

					<!-- How reliably Sift can identify them, drawn from the one reading taken for the
					     whole screen rather than a request per card. -->
					{#if person.person_id}
						<RecognitionStrength
							personId={person.person_id}
							name={person.person_name}
							{strengths}
						/>
					{/if}

					{#if person.person_id && (person.matched > 0 || person.waiting > 0)}
						<!--
							One control at the foot of the card, not two: the card is about fourteen
							rems wide, and the two acts side by side would run off its edge. Both
							are real questions (agreeing with what Sift matched on its own, and with
							what it proposed), so the shape is the group cards': the common act on
							the lead half, everything else behind the chevron.

							Which one leads is decided by state, not pile size: Sift's guesses are
							the only thing on the card asking a question, while what Sift named on
							its own is done and wants a nod at most. So the answer leads whenever
							there is one to give, however few, and the nod leads only where nothing
							is asked.

							One label shape: Yes (N), where N is exactly what the press confirms. A
							label that rewords itself with the state would make somebody re-read the
							button before every press; the counts above already say the state.

							The menu carries both refusals: the bulk No, which writes a receipt
							History can take back as the yes does, and the row that opens the faces
							one at a time for somebody who wants to look.

							The act this card is not leading with is not a row here: it is on that
							person's own screen, on the tab holding those faces, where what is being
							agreed to is on the page.

							Nothing can fold out of the card: `SplitButton` wraps rather than
							spilling.
						-->
						{@const leadsWithWaiting = person.waiting > 0}
						{@const answering = leadsWithWaiting ? person.waiting : person.matched}
						<div class="row">
							<SplitButton
								tone="primary"
								icon="check"
								disabled={busy !== null}
								trailingLabel={`More for ${person.person_name ?? 'them'}`}
								onclick={() =>
									leadsWithWaiting ? void agree(person) : void agreeToMatches(person)}
							>
								{busy === person.person_id
									? 'Answering\u2026'
									: `Yes (${answering.toLocaleString()})`}
								{#snippet menu()}
									<!-- The refusal of exactly what the lead confirms, and it
									     writes. Two doors behind one row: the guesses are refused
									     where they were proposed, the matches where they were
									     matched, and which one this card is about is the same
									     question the lead answers. The answer decided here is one
									     part; the two that go somewhere to look first are the next. -->
									<ContextMenuGroup>
										<ContextMenuItem
											label={`No (${answering.toLocaleString()})`}
											icon="close"
											onselect={() => void refuse(person)}
										/>
									</ContextMenuGroup>
									<ContextMenuGroup>
										<!-- And the row for somebody who wants to look first. It navigates
										     rather than writing, and it says so: a "No" that goes somewhere
										     and changes nothing reads as a press that did not work. -->
										<ContextMenuItem
											label="No, one at a time"
											icon="arrow_forward"
											onselect={() => void goto(facesHref(person))}
										/>
										<!-- The same address the thumbnails open, so there is one way to this
										     person's faces rather than two. A row rather than a link because this
										     menu's rows are the app's menu rows and none of them is an anchor; and
										     `arrow_forward` rather than the eye, which is the vault's reveal and
										     the count of times a file was opened. -->
										<ContextMenuItem
											label="Show me"
											icon="arrow_forward"
											onselect={() => void goto(facesHref(person))}
										/>
									</ContextMenuGroup>
								{/snippet}
							</SplitButton>
						</div>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}
</section>

<!-- The count on the bar is of PEOPLE and the button says how many FACES that comes to: two
     different numbers, and the one being agreed to is the second.

     NO REFUSAL HERE, and the asymmetry is deliberate. A card's "No" opens that person's faces so
     each can be looked at; a selection is several people, and there is no one screen a refusal
     across them could open. A bar offering a "No" that had to be a WRITE would refuse thousands of
     faces belonging to several people in one press, on the surface furthest from any of them. -->
<ActionBar count={picked.length} noun="person" plural="people" onclear={() => selection.clear()}>
	{#snippet actions()}
		<Button tone="primary" disabled={busy !== null} onclick={() => void agreeToPicked()}>
			<Icon name="check" size={16} />
			<!-- The same shape as the lead on a card: one word and the number it settles. -->
			{busy === 'selection' ? 'Answering\u2026' : `Yes (${waitingPicked.toLocaleString()})`}
		</Button>
	{/snippet}
</ActionBar>

<style>
	/* The starter pictures' two halves, side by side at the end of the tab line. */
	.starters {
		display: inline-flex;
		gap: var(--space-2);
	}

	/* The box and the control on one line, wrapping under each other where the line is short. */
	.tab-tools {
		display: inline-flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
	}

	.starters .data {
		font-variant-numeric: tabular-nums;
	}

	.panel {
		display: flex;
		flex-direction: column;
	}

	/*
	 * The same column the groups wall uses (`FaceGroups`), so a card is one width on both walls of
	 * this feature and a control that fits one fits the other. It holds four crops and a row of
	 * controls.
	 */
	.people {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(15.25rem, 1fr));
		gap: var(--space-3);
	}

	.person {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		padding: var(--space-3);
		border-radius: var(--radius-lg);
		/* The card's light (see `--sift-card`). It has no border, so the fill is the whole of it. */
		background: var(--sift-card);
	}

	.who {
		margin: 0;
		overflow-wrap: anywhere;
		color: var(--sift-ink);
		text-decoration: none;
	}

	a.who:hover {
		text-decoration: underline;
	}

	a.who:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
		border-radius: var(--radius-sm);
	}

	/* The name and the count are one block at the bottom of the card.
	 *
	 * Pushing only the count down would leave the name pinned under the thumbnails, so a row of cards
	 * would have its names at three different heights: somebody with eight faces a whole row lower
	 * than somebody with one. Moving the gap to the top of the pair aligns both. */
	.who {
		margin-block-start: auto;
	}

	/* The three numbers, one per line. No second `auto` here: two of them in one column do not both
	   push to the bottom: they SHARE the free space, which would leave the name hanging in the middle of
	   the card with a gap above and below it. The gap belongs to the top of the pair and nowhere
	   else. */
	.counts {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.one {
		color: var(--sift-ink-3);
	}

	/* How many of this person's faces still want an answer: a count beside the row, not an
	   in-flight state, which is why it is not called `.waiting`. */
	.pending {
		color: var(--sift-ink-2);
	}

	/* Nothing left to answer for this person. Green rather than quiet grey: the line is an ANSWER
	   and reads as one at a glance, next to the cards that still want something. */
	.settled {
		color: var(--sift-ok);
	}

	/*
	 * The one control, at the trailing edge of the card: actions sit on the right.
	 *
	 * `min-inline-size: 0` is this row's own floor, kept because it holds for any child this row is
	 * given. The control inside is what could refuse to shrink (both halves are nowrap buttons
	 * sized by their words), and since the row ends its children that overflow would land on the
	 * leading side; `SplitButton` handles it.
	 */
	.row {
		display: flex;
		/* The answer starts the line, as every Organize card's does. */
		justify-content: flex-start;
		gap: var(--space-2);
		min-inline-size: 0;
	}
</style>
