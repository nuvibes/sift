<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	import {
		Button,
		Checkbox,
		Empty,
		Field,
		Pressable,
		Problem,
		Skeleton,
		SuggestInput
	} from '$lib/components/common';
	/*
	 * The folders Sift thinks it can put a name to, one short question each.
	 *
	 * Yes is one press and does everything: it attributes every file under the folder, names the
	 * face group behind the claim, teaches Sift the folder's spelling so the same person filed
	 * differently answers itself next time, and links the username a username folder names to the
	 * person. A folder is worth reading because it answers many files in one go.
	 *
	 * Three answers and no fourth: yes, no, or somebody else. A no is remembered for good, so a
	 * folder named after a place or a theme is refused once.
	 *
	 * The card asks in the one shape every question on Organize wears (`DecisionCard`): the evidence
	 * (the face, where the folder is) above, then the question said as one ("Is 'x' a person?"), why
	 * Sift thinks so under it, and the answers at the foot, the one that does everything as the button
	 * and the other two behind its chevron (`Answers`). The question and its answers are worded per
	 * kind, because the three kinds do different things on a yes: see `asking`.
	 *
	 * Every number is the server's: the count is how many files under that folder this account may
	 * open, a folder they may not see never arrives, and no file they cannot see is offered to be
	 * left out.
	 *
	 * Deliberately small: a list somebody works down, not a workbench.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import type { OnPaging } from '$lib/components/common/Pager.svelte';
	import { onDestroy } from 'svelte';
	import Scroller from '$lib/components/common/Scroller.svelte';
	import DecisionCard from '$lib/components/organize/DecisionCard.svelte';
	import PathText from '$lib/components/PathText.svelte';
	import Answers from '$lib/components/organize/Answers.svelte';
	import { untrack } from 'svelte';

	import { page as address } from '$app/state';
	import { anchorIn, rememberAnchor } from '$lib/grid/anchor';
	import { revealAnchored } from '$lib/organize/anchor';
	import { CardPaging } from '$lib/grid/cards.svelte';
	import { thumbUrl } from '$lib/entity/art';
	import { cropUrl, blankOnRefusal } from '$lib/people/faces.svelte';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { thing, type ToastWords } from '$lib/components/common/toast-pieces';
	import { decided } from '$lib/organize/organize.svelte';
	import {
		SUGGESTIONS_PER_PAGE,
		confirmSuggestion,
		rejectSuggestion,
		sayWhoAFolderIs,
		suggestions,
		type Proposal
	} from '$lib/search/suggestions.svelte';

	let rows = $state<Proposal[]>([]);
	let loading = $state(true);
	let failed = $state(false);
	let busy = $state<ReadonlySet<string>>(new Set());
	const paging = new CardPaging(SUGGESTIONS_PER_PAGE, 'organize.folders');

	interface Props {
		/** Where the pager goes: the frame's foot, drawn by the route. See `PagerProps`. */
		onpaging?: OnPaging;
	}

	/*
	 * No tab-line control: the folder pass runs as new files settle and again as every scan settles
	 * (`settles_into` in the composition root), and the Activity screen's run-now presses it by
	 * hand.
	 */
	let { onpaging }: Props = $props();
	$effect(() => {
		onpaging?.(paging.asPager(rows.length, total, 'folders'));
	});
	onDestroy(() => onpaging?.(null));
	let total = $state(0);

	/* How many of the odd files out a card puts on screen before it says "and N more".
	 *
	 * A folder of eight hundred is one question, and drawing eight hundred thumbnails to ask it is
	 * a screen that will not scroll on the machines Sift runs on. */
	const SHEET = 24;

	/** The rows whose full contact sheet has been asked for. */
	let expanded = $state<Set<string>>(new Set());

	function shown(row: Proposal): string[] {
		return expanded.has(row.id) ? row.dissenting : row.dissenting.slice(0, SHEET);
	}

	function expand(id: string): void {
		expanded = new Set([...expanded, id]);
	}

	/* What has been unticked, per row. Absent means everything is in, which is the answer for the
	   great majority of folders: a folder named after somebody usually is all of them. */
	let leftOut = $state<Record<string, Set<string>>>({});

	/* Which cards have their detail open: the names read out of the filenames, the files whose
	   faces do not agree. Closed by default so every card is the same height as its neighbours,
	   and a wall of them can be skimmed; a card with nothing to untick has no detail to open. */
	let detailed = $state<Set<string>>(new Set());

	function toggleDetail(id: string): void {
		const next = new Set(detailed);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		detailed = next;
	}

	/** What a card's detail holds, said as a count, or nothing when there is nothing to untick. */
	function detailWords(row: Proposal): string | null {
		const names = row.per_file.length;
		const odd = row.dissenting.length;
		if (names === 0 && odd === 0) return null;
		const parts: string[] = [];
		if (names > 0)
			parts.push(`${counted(names)} ${names === 1 ? 'name' : 'names'} found in filenames`);
		if (odd > 0) parts.push(`${counted(odd)} ${odd === 1 ? 'file does' : 'files do'} not match`);
		return parts.join(', ');
	}

	/* Where this wall was left, carried in the address. See `$lib/grid/anchor`.
	 *
	 * `path` is captured once so a background refresh cannot rewrite the address after somebody has
	 * navigated away, and `arriving` is true exactly once: after the first settle the anchor in the
	 * address is one WE wrote, and honouring it again would start a new question at the old one's
	 * position. */
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
		failed = false;
		try {
			// The rows go in as a function, never the array: `fill` reads them untracked, so the
			// effect that runs this load cannot come to depend on its own answer.
			const page = await paging.fill(
				'',
				() => rows,
				(query) => {
					// "Looking..." only when a request goes out: a landing or a trim asks nothing.
					loading = true;
					return suggestions(query);
				},
				(answer) => ({ rows: answer.proposals, total: answer.total, offset: answer.offset })
			);
			// Overtaken by a newer read, which finishes this one's work.
			if (page === null) return;
			rows = page.rows;
			total = page.total;
			settle(page.offset, rows[0]?.id);
		} catch {
			failed = true;
		} finally {
			loading = false;
		}
	}

	/* `paging.size` as well as the offset: a taller window holds more rows, so the page has to be
	   re-fetched at the new size rather than merely re-flowed. */
	$effect(() => {
		void paging.offset;
		void paging.size;
		if (arriving) {
			arriving = false;
			// UNTRACKED: this effect's own answer writes the address, and reading it here plainly
			// would make the effect depend on what it causes: the anchor written and deleted twice,
			// settling with nothing.
			paging.arrive(untrack(() => anchorIn(address.url)));
		}
		/* The load UNTRACKED: its dependencies are the ones named above. `fill` reads the paging's
		   anchor before its first await, and tracked, `land` clearing that anchor would re-run this
		   effect and ask again for the page just landed whenever the row resolved to the page already
		   open. */
		untrack(() => void load());
	});

	/* A share or a restrict moving changes which folders belong here and how big each one is, and
	   none of that produces an import job to announce it. */
	reloadOnLibraryChange(() => void load());

	/* Arrived here pointed at one folder: a still on the board's Folders card. See
	   `$lib/organize/anchor`, and `_claim_anchor` on the server for the other half of the name.
	   Once per fragment, so a card scrolled away from is not dragged back on every re-read. */
	let revealed = '';

	$effect(() => {
		const wanted = address.url.hash.slice(1);
		if (!wanted || wanted === revealed || rows.length === 0) return;
		if (revealAnchored(wanted)) revealed = wanted;
	});

	function unticked(row: Proposal): Set<string> {
		return leftOut[row.id] ?? new Set<string>();
	}

	/* One set per row of whatever that row offered to leave out: files for an ordinary folder,
	   names for a Site one. The row decides which, and the server reads it the same way. */
	function toggle(row: Proposal, what: string) {
		const chosen = new Set(unticked(row));
		if (chosen.has(what)) chosen.delete(what);
		else chosen.add(what);
		leftOut = { ...leftOut, [row.id]: chosen };
	}

	async function yes(row: Proposal) {
		busy = new Set([...busy, row.id]);
		try {
			const applied = await confirmSuggestion(row.id, [...unticked(row)]);
			decided(filedUnder(applied, named(row)), applied.decision_id, { after: load });
			await load();
		} catch (error) {
			toasts.show(error instanceof Error ? error.message : "That couldn't be applied", {
				tone: 'error'
			});
		} finally {
			busy = new Set([...busy].filter((id) => id !== row.id));
		}
	}

	/** Which row is having its name typed, and what has been typed so far. */
	let correcting = $state<string | null>(null);
	let corrected = $state('');

	/** Open or close the box for the name, on one card at a time. */
	function correct(row: Proposal): void {
		correcting = correcting === row.id ? null : row.id;
		corrected = '';
	}

	/* Say who a folder really is.
	 *
	 * Yes and "not a person" both answer the question Sift asked, so between them they record every
	 * case it got right and every case it was wrong to ask about, and nothing about a folder it
	 * read as the WRONG person. That correction is the only one a miss leaves a trace of, and it
	 * only exists if it is written down as it is made.
	 */
	async function actually(row: Proposal) {
		const name = corrected.trim();
		if (!name) return;
		busy = new Set([...busy, row.id]);
		try {
			const applied = await sayWhoAFolderIs(row.folder_id, name);
			decided(filedUnder(applied, name), applied.decision_id, { after: load });
			correcting = null;
			corrected = '';
			await load();
		} catch (error) {
			toasts.show(error instanceof Error ? error.message : "That couldn't be applied", {
				tone: 'error'
			});
		} finally {
			busy = new Set([...busy].filter((id) => id !== row.id));
		}
	}

	async function no(row: Proposal) {
		busy = new Set([...busy, row.id]);
		try {
			const aside = await rejectSuggestion(row.id);
			decided(
				`${named(row)} is ${asking(row).refused}. Sift won't ask about it again.`,
				aside.decision_id,
				{
					after: load
				}
			);
			await load();
		} catch (error) {
			toasts.show(error instanceof Error ? error.message : "That couldn't be applied", {
				tone: 'error'
			});
		} finally {
			busy = new Set([...busy].filter((id) => id !== row.id));
		}
	}

	function why(row: Proposal): string {
		if (row.evidence === 'face_group') return 'The same face runs through this folder';
		if (row.evidence === 'filenames') return 'Every filename here starts with this word';
		/* The brackets are the whole evidence, so the sentence says so. Saying yes files the folder
		   under that username and names nobody. See `_confirm_account` for why no person. */
		if (row.evidence === 'username_folder')
			return 'The folder name gives the username in brackets. Yes adds no person';
		return 'Named like a person \u2014 no faces found here';
	}

	/** How a row is called in a sentence: the username says which site it is on. */
	function filedUnder(applied: { files: number; person_id: string }, name: string): ToastWords {
		const files = applied.files === 1 ? 'one file' : `${counted(applied.files)} files`;
		return [
			`Filed ${files} under `,
			applied.person_id ? thing('person', applied.person_id, name) : name
		];
	}

	function named(row: Proposal): string {
		return row.kind === 'username' && row.site ? `${row.proposed} on ${row.site}` : row.proposed;
	}

	/** What one card asks, and what each of its three answers says. */
	interface Asking {
		question: string;
		yes: string;
		no: string;
		other: string;
		/** What a no is, in the sentence the toast says after it. */
		refused: string;
	}

	/*
	 * The question and its answers, worded for what each answer ACTUALLY DOES to this kind of row,
	 * read off the service rather than guessed, because a label that promises something the
	 * press does not do is the one thing this card must not say:
	 *
	 *  - a PERSON row's yes files every file under that person (made if new), names the face group,
	 *    keeps the folder's spelling as another name for them and links the username a folder names
	 *    (`SuggestionService.confirm`);
	 *  - a SITE row's yes makes the word a site, files the folder under it, and names each file's
	 *    person from that file's own name (`_confirm_site`): nobody is made out of the word;
	 *  - a USERNAME row's yes files the folder under that username on that site and names NOBODY
	 *    (`_confirm_account`), because a username is what a site calls somebody, not who they are.
	 *
	 * A no is the same act for all three: the name is set aside for good (`reject`), nothing is
	 * written onto any file. "Someone else" names the folder as a PERSON (`say_who_a_folder_is`,
	 * kind person): on a person row that is a different person, and on a site or username row it
	 * is the correction "this is one person's folder", so it is worded that way there.
	 */
	function asking(row: Proposal): Asking {
		const quoted = `\u2018${row.proposed}\u2019`;
		if (row.kind === 'site')
			return {
				question: `Is ${quoted} a Site?`,
				yes: 'Yes, a Site',
				no: 'No, not a Site',
				other: "No, it's one person",
				refused: 'not a Site'
			};
		if (row.kind === 'username')
			return {
				question: row.site
					? `Is this the ${row.site} username ${quoted}?`
					: `Is this the username ${quoted}?`,
				yes: 'Yes, add to that username',
				no: 'No, not that username',
				other: "No, it's one person",
				refused: 'not that username'
			};
		return {
			question: `Is ${quoted} a person?`,
			yes: 'Yes, a person',
			no: 'No, not a person',
			other: 'Someone else',
			refused: 'not a person'
		};
	}
</script>

<section class="screen">
	<!-- No bar above the cards: the count is the lit tab's own number.

	     Only while there is nothing on screen yet. A reload after a decision keeps what is drawn
	     and swaps it when the answer arrives; showing the skeleton again would blink the page for
	     every answer. `EntityGrid` does the same. -->
	{#if loading && rows.length === 0}
		<Skeleton lines={3} />
	{:else if failed}
		<Problem message="Folders couldn't be loaded. Try again in a moment." />
	{:else if rows.length === 0}
		<!-- An empty list here has three different causes, and the screen says which: a folder
		     named after somebody who already exists is filed without being asked (the point of not
		     asking twice), so on a tidy library this list is empty because the feature is working.
		     Saying so is the difference between "nothing happened" and "nothing needs you". -->
		<div class="absence">
			<Empty scope="block">Nothing to review.</Empty>
			<p class="quiet">
				When a folder's name or faces match a person you already have, Sift adds its files to that
				person. It lists the folder under Added without asking. Folders not named like a person,
				such as Videos, Downloads or 4K, are skipped. Folders named after someone Sift doesn't
				recognize yet appear here.
			</p>
			<p class="quiet">Sift checks new folders as they are imported, and again after every scan.</p>
		</div>
	{:else}
		<!-- A wall of cards, the same wall the faces queue is: one question per card, the picture
		     first, the answers at the foot, so the cards stand level rather than as rows of uneven
		     height with checklists opened out in the middle of each. -->
		<ul class="cards" {@attach paging.cards}>
			{#each rows as row (row.id)}
				<!-- Named so a still on the board's card can point at this folder rather than at one
				     file inside it. `slices/suggestions/queue._claim_anchor` writes the other half;
				     a claim has no screen of its own, so the address is this page and this name. -->
				<li id="claim-{row.id}">
					<DecisionCard detailLines={2}>
						<!-- The question in words at the card's foot, rather than the folder's bare
						     name leaving it to be inferred from the buttons, and why Sift asks under
						     it. -->
						{#snippet question()}{asking(row).question}{/snippet}
						{#snippet detail()}{why(row)}{/snippet}
						<!-- The evidence above it: the face the claim rests on, and where the folder is
						     and how much is in it. -->
						<div class="who">
							<div class="face">
								{#if row.face_id}
									<!-- The claim carries a face id and nothing about the vault. If the file
									     behind it is concealed, or its crop is not there, the server refuses the
									     picture, and what the browser draws then is its own torn-page glyph,
									     which reads as Sift being broken. A blank instead; the Hidden mark is
									     drawn only where the server says `locked`.
									     With its token, so the crop may be kept: without one the server answers
									     the careful way and every visit would re-ask about every face on the page. -->
									<img
										src={cropUrl({ track_id: row.face_id, art: row.face_art })}
										alt=""
										loading="lazy"
										onerror={blankOnRefusal}
									/>
								{:else}
									<span class="vacant"><Icon name="person" size={20} /></span>
								{/if}
							</div>
							<div class="what">
								<p class="where">
									{row.files === 1 ? '1 file' : `${counted(row.files)} files`} in
									<PathText path={row.path || row.folder} />
								</p>
								{#if row.near_miss}
									<p class="why">
										A person with a very similar name already exists. Check before you choose Yes.
									</p>
								{/if}
							</div>
						</div>

						<!-- The detail, behind one line: what could be left out of a yes. Opened on
						     demand, so a card's height is its neighbours' until somebody asks. -->
						{#if detailWords(row)}
							<div class="toggle">
								<Button
									tone="link"
									size="small"
									aria-expanded={detailed.has(row.id)}
									onclick={() => toggleDetail(row.id)}
								>
									{detailed.has(row.id) ? 'Hide the detail' : detailWords(row)}
								</Button>
							</div>
						{/if}
						{#if detailed.has(row.id)}
							{#if row.per_file.length > 0}
								<fieldset class="odd">
									<legend>Names found in the filenames. Deselect any to leave them out.</legend>
									{#each row.per_file as name, at (`${at}:${name}`)}
										<!-- The whole row is the control: the box is drawn as a
										     mark (a picture of the state, hidden from a screen
										     reader) inside the row that really is the button, and
										     the name is said once rather than twice. -->
										<Pressable
											class="ticked"
											feedback="wash"
											radius="sm"
											aria-pressed={!unticked(row).has(name)}
											onclick={() => toggle(row, name)}
										>
											<Checkbox state={unticked(row).has(name) ? 'off' : 'on'} mark />
											<span class="who-name">{name}</span>
										</Pressable>
									{/each}
								</fieldset>
							{/if}
							{#if row.dissenting.length > 0}
								<fieldset class="odd" class:sheet={expanded.has(row.id)}>
									<legend>These don't match the rest. Deselect any you want to leave out.</legend>
									<!-- The thumbnails scroll, not the whole fieldset: the legend says what the
									     ticking is FOR, and a caption that scrolls away from the thing it
									     captions is a caption nobody reads. -->
									<Scroller>
										<div class="thumbs">
											{#each shown(row) as assetId (assetId)}
												<!-- The row is the control, for the reason given at the names
												     above, and here the picture is what somebody is aiming at. -->
												<Pressable
													class="ticked"
													feedback="wash"
													radius="sm"
													aria-pressed={!unticked(row).has(assetId)}
													aria-label="Include this one"
													onclick={() => toggle(row, assetId)}
												>
													<Checkbox state={unticked(row).has(assetId) ? 'off' : 'on'} mark />
													<!-- The token rides down with the row (`ProposalView.art`), so these
													     stills may be kept for a week rather than re-checked on every
													     visit. Absent for a file whose pictures are not yet recorded,
													     which leaves the address bare and is only slower. -->
													<img
														src={thumbUrl({ id: assetId, art: row.art?.[assetId] ?? null })}
														alt=""
														loading="lazy"
													/>
												</Pressable>
											{/each}
										</div>
									</Scroller>
									<!-- Never eight hundred thumbnails. The DECISION is one decision whatever
									     the number is; a couple of dozen is enough to see what a folder holds,
									     and the rest are a press away. -->
									{#if row.dissenting.length > SHEET && !expanded.has(row.id)}
										<Button onclick={() => expand(row.id)}>
											and {counted(row.dissenting.length - SHEET)} more
										</Button>
									{/if}
								</fieldset>
							{/if}
						{/if}

						<!-- The three answers at the foot: the one that does everything as the button,
						     the refusal and the correction behind its chevron. The third is the only
						     one that teaches Sift something it could not have worked out: yes and
						     "not a person" say whether the guess was right, and neither says who it
						     should have been. -->
						{#snippet answers()}
							<Answers
								yes={{ label: asking(row).yes, icon: 'check', run: () => void yes(row) }}
								rest={[
									{ label: asking(row).no, icon: 'close', run: () => void no(row) },
									{ label: asking(row).other, icon: 'edit', run: () => correct(row) }
								]}
								about={named(row)}
								busy={busy.has(row.id)}
								disabled={busy.has(row.id)}
							/>
							{#if correcting === row.id}
								<div class="correction">
									<Field label="Who is this folder?">
										{#snippet control({ id, describedBy })}
											<!-- The same completing box the folder sheet uses, for the same
										     reason: the person this folder was read wrongly as is almost
										     always somebody the library already holds, and typing the name
										     again by hand is how a second spelling of them is made. It
										     never refuses what was typed: somebody new is a perfectly
										     good answer, and is the answer the first time. -->
											<SuggestInput
												{id}
												{describedBy}
												suggests="people"
												placeholder="Their name"
												value={corrected}
												oninput={(entered) => (corrected = entered)}
												onsubmit={(entered) => {
													corrected = entered;
													void actually(row);
												}}
											/>
										{/snippet}
									</Field>
									<Button
										tone="primary"
										icon="save"
										disabled={busy.has(row.id) || corrected.trim().length === 0}
										onclick={() => actually(row)}
									>
										Save
									</Button>
								</div>
							{/if}
						{/snippet}
					</DecisionCard>
				</li>
			{/each}
		</ul>
	{/if}
</section>

<style>
	.screen {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
	}

	/* The measure only. The ink and the face come from the `.quiet` utility in app.css. */
	.absence p {
		max-width: 60ch;
	}

	.absence {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	/* The same grid the faces wall lays its cards on, so the two walls read as one kind of screen. */
	.cards {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(16rem, 1fr));
		gap: var(--space-3);
	}

	/*
	 * Each card at its own height, with no floor: a folder card is a few lines of text, and the wall
	 * floor would leave most of them a band of empty ground. The grid already gives the cards of one row one
	 * height, and the two lines kept for the detail under the question hold the questions level.
	 */
	.cards > li {
		display: grid;
	}

	.who {
		display: flex;
		gap: var(--space-3);
		align-items: flex-start;
	}

	.face {
		flex: 0 0 auto;
	}

	/* The stand-in drawn where a claim carries no face. A vacant PICTURE slot, not an empty state,
	   which is why it is not called `.blank`. */
	.face img,
	.vacant {
		width: 56px;
		height: 56px;
		border-radius: var(--radius-full);
		object-fit: cover;
		display: grid;
		place-items: center;
		background: var(--sift-surface-3);
		color: var(--sift-ink-3);
	}

	.what {
		flex: 1 1 auto;
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}

	/* Whole, never cut: the path and the warning are what somebody reads to decide, and a card with
	   no floor grows to hold them. A path breaks anywhere, having no spaces to break at. */
	.where,
	.why {
		margin: 0;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
		overflow-wrap: anywhere;
	}

	/* The way into the detail is a line of the card, so it starts where every other line does
	   rather than centred as a stretched button's words are. */
	.toggle {
		display: flex;
		text-align: start;
	}

	.toggle :global(.btn) {
		text-align: inherit;
	}

	.thumbs {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2);
	}

	/* Expanded, the sheet scrolls rather than pushing the answers off the bottom of the card.
	   The buttons that answer the question have to stay reachable while somebody looks. The cap
	   goes on the box that SCROLLS, `:global` because that box is the shared region's. */
	.odd.sheet :global(.scroll-root) {
		max-block-size: 360px;
	}

	.odd {
		margin: 0;
		border: 1px solid var(--sift-line);
		border-radius: var(--radius-sm);
		padding: var(--space-2);
		display: flex;
		flex-direction: row;
		flex-wrap: wrap;
		gap: var(--space-1);
	}

	.odd legend {
		color: var(--sift-ink-3);
		font: var(--text-micro);
		padding: 0 var(--space-1);
	}

	/* `:global`, because the class is handed to `Pressable` and lands on its element. From `.odd`,
	   so it reaches these rows and no other file's `.ticked`, and so it outranks `Pressable`'s own
	   `display: block`. */
	.odd :global(.ticked) {
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}

	.who-name {
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	/* The pictures rather than the ids. Somebody deciding whether a file belongs to this person is
	   looking at the file, and an identifier tells them nothing they can judge. */
	.odd img {
		width: 48px;
		height: 48px;
		object-fit: cover;
		border-radius: var(--radius-sm);
		background: var(--sift-surface-3);
	}

	/* The correction sits under the answers rather than beside them: it is a second step somebody
	   chose, and putting a text box in the row of buttons would make it look like a fourth answer. */
	.correction {
		display: flex;
		gap: var(--space-3);
		align-items: flex-end;
	}
</style>
