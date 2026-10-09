<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/* What Sift has decided lately, gathered by person, newest decision first: one card per person
	 * with what was confirmed, named by Sift and still waiting. The one narrowing is by starter
	 * pictures (`?starters=`), set at the tab line's end beside the search box, whose words go to
	 * the server as `who` so every count describes what was found. */
	import Icon from '$lib/components/Icon.svelte';
	import Chip from '$lib/components/common/Chip.svelte';
	import type { OnTools } from '$lib/components/organize/OrganizeHeader.svelte';
	import FaceCovers from '$lib/components/faces/FaceCovers.svelte';
	import {
		agreeingWith,
		matchingSaid,
		rematching
	} from '$lib/components/faces/WaitingForYou.svelte';
	import {
		ActionBar,
		Button,
		ContextMenuGroup,
		ContextMenuItem,
		Empty,
		Selection,
		Skeleton,
		Spinner,
		SplitButton,
		TileGesture,
		Tooltip
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
	import CardWall from '$lib/components/organize/CardWall.svelte';
	import DecisionCard from '$lib/components/organize/DecisionCard.svelte';
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

	/* No filter by how a face was named; `?show=` is left for the person's screen. `?starters=`
	   lives in the address, so it survives a refresh and Back. */
	const starters = $derived<StartersShow | null>(
		((value) => (value === 'only' || value === 'without' ? value : null))(
			address.url.searchParams.get('starters')
		)
	);
	/* The words the wall is searched by, read from the address; the box writes them there. */
	const addressWords = $derived(wordsIn(address.url, FACES_WORDS));
	const words = new WallWords(FACES_WORDS);
	let term = $state(untrack(() => wordsIn(address.url, FACES_WORDS)));
	/* Words reaching the address another way are put in the box (see `WallWords`). */
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

	/** Where the pager and the starter pictures' control go, both drawn by the route. */
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

	/* Where this wall was left, in the address (`$lib/grid/anchor`); `path` captured once. */
	const path = address.url.pathname;
	let arriving = true;
	let arrivedFor: string | null = null;

	/**
	 * Read the address once, then land the offset in the same step as the rows (`CardPaging.land`).
	 */
	function settle(at: number, first: string | null | undefined) {
		paging.land(at);
		rememberAnchor(address.url, path, first, at);
	}

	async function load() {
		// Everybody, unfiltered, through `fill`.
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
		// A landing brings no answer, so the counts on screen stand.
		if (page.answer) {
			startersOnly = page.answer.starters_only ?? 0;
			others = page.answer.others ?? 0;
		}
		// The first card may be the nameless one, which cannot be named in an address.
		settle(page.offset, people[0]?.person_id);
		loading = false;
	}

	$effect(() => {
		void paging.offset;
		void paging.size;
		/* Arrived again whenever the narrowing moves: its anchor is this narrowing's. */
		const narrowing = `${starters ?? ''}:${addressWords}`;
		if (arriving || narrowing !== arrivedFor) {
			arriving = false;
			arrivedFor = narrowing;
			// Untracked: this effect's answer writes the address.
			paging.arrive(untrack(() => anchorIn(address.url)));
		}
		/* The load untracked, or `land` would re-run it. */
		untrack(() => void load());
	});

	/* Their first name for the three lines, "them" without one. */
	function firstOf(person: IdentifiedPerson): string {
		return person.person_name?.trim().split(/\s+/)[0] || 'them';
	}

	function facesHref(person: IdentifiedPerson): string {
		const id = encodeURIComponent(person.person_id as string);
		return `/organize/known-people/${id}?show=${person.waiting > 0 ? 'suggested' : 'matched'}`;
	}

	/* And when a share or restrict moves what this scoped wall may show. */
	reloadOnLibraryChange(() => void load());

	/* Agree with everything proposed for one person, asked of the person, not the card's crops. */
	async function agree(person: IdentifiedPerson) {
		if (!person.person_id || person.waiting === 0 || busy) return;
		busy = person.person_id;
		try {
			const answer = await confirmLookAlikes(person.person_id);
			if (answer.changed > 0) rematching.after(person.person_id);
			toasts.show(agreeingWith(answer.changed), { tone: 'success' });
			answered.changed();
			await load();
		} catch {
			toasts.show("Those couldn't be confirmed", { tone: 'error' });
		} finally {
			busy = null;
		}
	}

	/* And agree with everything Sift matched on its own, the card's other half. */
	async function agreeToMatches(person: IdentifiedPerson) {
		if (!person.person_id || person.matched === 0 || busy) return;
		// The card's own id, as the presses beside it use.
		busy = person.person_id;
		try {
			const answer = await confirmMatches(person.person_id);
			if (answer.confirmed > 0) rematching.after(person.person_id);
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

	/* And say the same set is not them, refused where proposed or matched; both write a receipt. */
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

	/* How reliably Sift identifies each person, read once for the screen. */
	let strengths = $state<ReferenceStrengths | null>(null);
	$effect(() => {
		// Read so the bars move when a press here makes new reference pictures.
		void answered.stamp;
		void referenceStrengths()
			.then((found) => (strengths = found))
			.catch(() => (strengths = null));
	});

	/* Picking several people by the shared gesture; the bar only agrees, and only people with
	   something waiting can be picked. */
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

	/* Let go on a page turn or a resize, so the bar never names cards off screen. */
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
		// Ctrl+Z and Ctrl+Shift+Z on the pick only (`TileGesture.undoKeys`).
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
				// Asked of the person, as the card's own press is.
				const answer = await confirmLookAlikes(id);
				if (answer.changed > 0) rematching.after(id);
				agreed += answer.changed;
			}
			selection.clear();
			toasts.show(agreeingWith(agreed), { tone: 'success' });
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

<!-- The tab line's end: the search box, then the starter pictures' two halves. -->
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
	<!-- Only while nothing is on screen yet, so a reload does not blink. -->
	{#if loading && people.length === 0}
		<Skeleton lines={3} />
	{:else if people.length === 0 && addressWords}
		<!-- A search that found nobody says so: a wall narrowed to nothing is not an empty one. -->
		<Empty scope="page" icon="search">{emptyWallSays('people', addressWords, false, '')}</Empty>
	{:else if people.length === 0}
		<Empty scope="page" icon="person" title="Nobody identified yet">
			People appear here as their faces are named. Start by naming a group under Unnamed faces.
		</Empty>
	{:else}
		<!-- No count of its own: the tab and the pager say it. -->
		<CardWall cards={paging.cards}>
			{#each people as person, at (`${at}:${person.person_id ?? 'nameless'}`)}
				<li>
					<DecisionCard opens={person.person_id ? facesHref(person) : undefined}>
						<!-- The thumbnails are the link; a concealed person has no page. -->
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

						<!--
						No id here is the concealed gather: it takes the vault's word, not "Not
						named".
						-->
						{#if person.person_id}
							<p class="who">
								<a href={`/people/${person.person_id}`}>{person.person_name}</a>
								{#if rematching.people.has(person.person_id)}
									<Tooltip label={matchingSaid(person.person_name)} placement="bottom">
										<Spinner size={12} label={matchingSaid(person.person_name)} />
									</Tooltip>
								{/if}
							</p>
						{:else}
							<p class="who">Hidden</p>
						{/if}
						<!--
						Confirmed, named by Sift, and waiting, in this order; a nought is left out
						but the last.
						-->
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
								<!-- The singular in full, never "1 need your input". -->
								<span class="one pending">
									{person.waiting === 1
										? '1 awaiting your input'
										: `${person.waiting.toLocaleString()} awaiting your input`}
								</span>
							{:else}
								<span class="one settled">Nothing needs your input</span>
							{/if}
						</p>

						<!-- From the one reading taken for the screen. -->
						{#if person.person_id}
							<RecognitionStrength
								personId={person.person_id}
								name={person.person_name}
								{strengths}
							/>
						{/if}

						{#if person.person_id && (person.matched > 0 || person.waiting > 0)}
							<!-- One control at the card's foot: the answer leads when anything is asked, else the nod;
							the label is Yes (N), N exactly what it confirms. The menu holds the bulk No and a row to look
							first. SplitButton wraps rather than spilling. -->

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
										<!--
										The refusal of exactly what the lead confirms; it writes.
										-->
										<ContextMenuGroup>
											<ContextMenuItem
												label={`No (${answering.toLocaleString()})`}
												icon="close"
												onselect={() => void refuse(person)}
											/>
										</ContextMenuGroup>
										<ContextMenuGroup>
											<!--
											The row to look first: it navigates and changes nothing,
											and says so.
											-->
											<ContextMenuItem
												label="No, one at a time"
												icon="arrow_forward"
												onselect={() => void goto(facesHref(person))}
											/>
											<!-- The thumbnails' address, as a menu row. -->
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
					</DecisionCard>
				</li>
			{/each}
		</CardWall>
	{/if}
</section>

<!-- The bar counts people, the button faces. No refusal here: a selection spans people, with
no one screen to open. -->

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

	.who {
		margin: 0;
		overflow-wrap: anywhere;
		color: var(--sift-ink);
	}

	/* The name, and the working mark after it while a re-match about her runs. */
	.who a {
		margin-inline-end: var(--space-2);
		color: inherit;
		text-decoration: none;
	}

	.who a:hover {
		text-decoration: underline;
	}

	.who a:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
		border-radius: var(--radius-sm);
	}

	/* Name and count pushed down as one block, so names align across a row. */
	.who {
		margin-block-start: auto;
	}

	/* No second auto margin, which would share the space. */
	.counts {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		/* Three lines held, however many it says, so every card on the wall is one height. */
		min-block-size: calc(3lh + 2 * var(--space-1));
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.one {
		color: var(--sift-ink-3);
	}

	/* Faces still wanting an answer; not `.waiting`, which is a state. */
	.pending {
		color: var(--sift-ink-2);
	}

	/* Nothing left: green, an answer at a glance. */
	.settled {
		color: var(--sift-ok);
	}

	/* The one control; min 0 holds for any child, and SplitButton handles its own overflow. */
	.row {
		display: flex;
		/* The answer starts the line, as every Organize card's does. */
		justify-content: flex-start;
		gap: var(--space-2);
		min-inline-size: 0;
	}
</style>
