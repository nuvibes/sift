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
	 * The folders Sift thinks it can name, one question each, in `DecisionCard`'s shape: yes does
	 * everything, no is remembered for good, or somebody else. Worded per kind (`asking`). Every
	 * number is the server's.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import type { OnPaging } from '$lib/components/common/Pager.svelte';
	import { onDestroy } from 'svelte';
	import Scroller from '$lib/components/common/Scroller.svelte';
	import CardWall from '$lib/components/organize/CardWall.svelte';
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
		onpaging?: OnPaging;
	}

	/* No tab-line control: the folder pass runs as files settle, and Activity can press it. */
	let { onpaging }: Props = $props();
	$effect(() => {
		onpaging?.(paging.asPager(rows.length, total, 'folders'));
	});
	onDestroy(() => onpaging?.(null));
	let total = $state(0);

	/* A folder of eight hundred is one question; drawing them all would not scroll. */
	const SHEET = 24;

	let expanded = $state<Set<string>>(new Set());

	function shown(row: Proposal): string[] {
		return expanded.has(row.id) ? row.dissenting : row.dissenting.slice(0, SHEET);
	}

	function expand(id: string): void {
		expanded = new Set([...expanded, id]);
	}

	/* Absent means everything is in. */
	let leftOut = $state<Record<string, Set<string>>>({});

	/* Closed by default, so cards are one height. */
	let detailed = $state<Set<string>>(new Set());

	function toggleDetail(id: string): void {
		const next = new Set(detailed);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		detailed = next;
	}

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

	/* Where this wall was left (`$lib/grid/anchor`); `path` is captured once. */
	const path = address.url.pathname;
	let arriving = true;

	/** Through `land`, in the same step as the rows (`CardPaging.land`). */
	function settle(at: number, first: string | null | undefined) {
		paging.land(at);
		rememberAnchor(address.url, path, first, at);
	}

	async function load() {
		failed = false;
		try {
			// The rows go in as a function, read untracked.
			const page = await paging.fill(
				'',
				() => rows,
				(query) => {
					loading = true;
					return suggestions(query);
				},
				(answer) => ({ rows: answer.proposals, total: answer.total, offset: answer.offset })
			);
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

	/* `paging.size` too: a taller window holds more rows. */
	$effect(() => {
		void paging.offset;
		void paging.size;
		if (arriving) {
			arriving = false;
			// UNTRACKED: this effect's own answer writes the address.
			paging.arrive(untrack(() => anchorIn(address.url)));
		}
		/* UNTRACKED: tracked, `land` clearing the anchor would ask again. */
		untrack(() => void load());
	});

	reloadOnLibraryChange(() => void load());

	/*
	 * Pointed at one folder from the board (`$lib/organize/anchor`, `_claim_anchor`), once per
	 * fragment.
	 */
	let revealed = '';

	$effect(() => {
		const wanted = address.url.hash.slice(1);
		if (!wanted || wanted === revealed || rows.length === 0) return;
		if (revealAnchored(wanted)) revealed = wanted;
	});

	function unticked(row: Proposal): Set<string> {
		return leftOut[row.id] ?? new Set<string>();
	}

	/* Files for an ordinary folder, names for a Site one. */
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

	let correcting = $state<string | null>(null);
	let corrected = $state('');

	function correct(row: Proposal): void {
		correcting = correcting === row.id ? null : row.id;
		corrected = '';
	}

	/* Who a folder really is: the only correction a miss leaves a trace of. */
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
		/* A username folder files under that username and names nobody (`_confirm_account`). */
		if (row.evidence === 'username_folder')
			return 'The folder name gives the username in brackets. Yes adds no person';
		return 'Named like a person \u2014 no faces found here';
	}

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

	interface Asking {
		question: string;
		yes: string;
		no: string;
		other: string;
		refused: string;
	}

	/*
	 * Worded for what each answer DOES to the kind: a person's yes files and names
	 * (`SuggestionService.confirm`), a Site's makes the site (`_confirm_site`), a username's names
	 * nobody (`_confirm_account`). No sets the name aside; "Someone else" names a person.
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
	<!-- No bar: the count is the lit tab's. Only while nothing is on screen. -->
	{#if loading && rows.length === 0}
		<Skeleton lines={3} />
	{:else if failed}
		<Problem message="Folders couldn't be loaded. Try again in a moment." />
	{:else if rows.length === 0}
		<!-- Empty often because it works: a known person's folder is filed without asking. -->
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
		<CardWall cards={paging.cards}>
			{#each rows as row (row.id)}
				<!--
				Named so the board can point here (`slices/suggestions/queue._claim_anchor`).
				-->
				<li id="claim-{row.id}">
					<DecisionCard detailLines={2}>
						{#snippet question()}{asking(row).question}{/snippet}
						{#snippet detail()}{why(row)}{/snippet}
						<div class="who">
							<div class="face">
								{#if row.face_id}
									<!--
									A blank where the server refuses the face; with its token, so
									the crop may be kept.
									-->
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

						<!-- Opened on demand, so cards keep their neighbours' height. -->
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
										<!-- The whole row is the control; the box is a mark. -->
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
									<!-- The thumbnails scroll; the legend stays. -->
									<Scroller>
										<div class="thumbs">
											{#each shown(row) as assetId (assetId)}
												<Pressable
													class="ticked"
													feedback="wash"
													radius="sm"
													aria-pressed={!unticked(row).has(assetId)}
													aria-label="Include this one"
													onclick={() => toggle(row, assetId)}
												>
													<Checkbox state={unticked(row).has(assetId) ? 'off' : 'on'} mark />
													<!--
													The token rides with the row
													(`ProposalView.art`).
													-->
													<img
														src={thumbUrl({ id: assetId, art: row.art?.[assetId] ?? null })}
														alt=""
														loading="lazy"
													/>
												</Pressable>
											{/each}
										</div>
									</Scroller>
									<!-- A couple of dozen, the rest a press away. -->
									{#if row.dissenting.length > SHEET && !expanded.has(row.id)}
										<Button onclick={() => expand(row.id)}>
											and {counted(row.dissenting.length - SHEET)} more
										</Button>
									{/if}
								</fieldset>
							{/if}
						{/if}

						<!-- The third answer is the only one that says who it should have been. -->
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
											<!--
											The folder sheet's completing box; it never refuses a
											new name.
											-->
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
		</CardWall>
	{/if}
</section>

<style>
	.screen {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
	}

	.absence p {
		max-width: 60ch;
	}

	.absence {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.who {
		display: flex;
		gap: var(--space-3);
		align-items: flex-start;
	}

	.face {
		flex: 0 0 auto;
	}

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

	/* Whole, never cut; a path breaks anywhere. */
	.where,
	.why {
		margin: 0;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
		overflow-wrap: anywhere;
	}

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

	/* The sheet scrolls, so the answers stay reachable. */
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

	/* From `.odd`, to outrank `Pressable`'s `display: block`. */
	.odd :global(.ticked) {
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}

	.who-name {
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	.odd img {
		width: 48px;
		height: 48px;
		object-fit: cover;
		border-radius: var(--radius-sm);
		background: var(--sift-surface-3);
	}

	/* Under the answers: a box in the row would look like a fourth. */
	.correction {
		display: flex;
		gap: var(--space-3);
		align-items: flex-end;
	}
</style>
