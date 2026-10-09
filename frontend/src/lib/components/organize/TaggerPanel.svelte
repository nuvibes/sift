<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/*
	 * Files a stash-box recognized, and the one press that settles a page of them.
	 *
	 * A list of rows rather than a wall of cards, the opposite choice from Handles Waiting: a
	 * handle is recognised by the picture under it, while a match is a claim about what would be
	 * written, which cannot be read off a thumbnail. So each row shows the file, what a stash-box
	 * says it is, and every field the answer would change beside what is already there.
	 *
	 * The whole page settles together: somebody with thousands of recognised files will not press a
	 * button per file, which is why the confirm shows its consequences. One press over a page is a
	 * decision when you can see what it does.
	 *
	 * Creating is a tick beside a counted list of names, sent with the press and never remembered:
	 * a stored "always make the people" would be the automatic creation this feature refuses.
	 *
	 * No "Scan the library" press here. The pile fills without one: once a batch of files has its
	 * fingerprints, the stash-box sweep asks about every file not yet asked
	 * (`fingerprints_settle_into` in the composition root, `stash_boxes.jobs.sweep`, under the
	 * enrichment switch and its "Ask about new files" sub-switch). A whole-library ask is pressed
	 * from Settings > Stash-boxes or the Activity screen.
	 */
	import { Button, Empty, Problem, SettingLink, Skeleton } from '$lib/components/common';
	import CreatesList from '$lib/components/organize/CreatesList.svelte';
	import MatchRow from '$lib/components/organize/MatchRow.svelte';
	import { answered } from '$lib/organize/organize.svelte';
	import { fields } from '$lib/entity/records.svelte';
	import {
		apply,
		PAGE,
		problemFrom,
		refuse,
		waiting,
		wouldCreate,
		wouldWrite,
		type Match,
		type Answer,
		toSettle,
		matchKey,
		type Missing,
		rowKey
	} from '$lib/entity/tagger.svelte';
	import { page as address } from '$app/state';
	import { onDestroy, untrack } from 'svelte';
	import { anchorIn, rememberAnchor } from '$lib/grid/anchor';
	import { CardPaging } from '$lib/grid/cards.svelte';
	import Chip from '$lib/components/common/Chip.svelte';
	import type { OnTools } from '$lib/components/organize/OrganizeHeader.svelte';
	import type { OnPaging } from '$lib/components/common/Pager.svelte';
	import type { MatchState } from '$lib/entity/tagger.svelte';

	/** Where the state pair goes: the far end of the tab line, drawn by the route. See `OnTools`. */
	let { onpaging, ontools }: { onpaging?: OnPaging; ontools?: OnTools } = $props();

	/*
	 * Where this page of the pile starts. Both states page, with the same paging every list in
	 * Organize has (`CardPaging`): the match the page starts at is written into the address as
	 * `from` (and where it was, `near`), so the way back from a file lands on this page of this
	 * state, and a match answered since is answered with where the page was. Back to the top when
	 * the state moves, because a page of the waiting rows is not a page of the answered ones.
	 */
	const paging = new CardPaging(PAGE);
	const path = address.url.pathname;
	let arriving = true;

	/*
	 * Which state of the pile: what still waits, or what was already answered. In the address, as
	 * the Unnamed faces wall's filter is, so it survives a refresh and Back. The answered files
	 * are the same rows in their other state, one press away on the tab line, so an empty waiting
	 * tab beside a full answered one does not read as switched off.
	 */
	const show = $derived<MatchState>(
		address.url.searchParams.get('show') === 'answered' ? 'answered' : 'waiting'
	);

	$effect(() => {
		ontools?.(stateChips);
	});
	onDestroy(() => {
		ontools?.(null);
		onpaging?.(null);
	});

	let items = $state<Match[]>([]);
	/**
	 * How many files were recognised and already answered, so an empty list can say why it is empty
	 * rather than reading as switched off beside a full second tab.
	 */
	let answeredFiles = $state(0);
	let total = $state(0);
	let loading = $state(true);
	let failed = $state<string | null>(null);
	let busy = $state(false);
	/* The names ticked for creation. Pruned at the press rather than watched: a row taken out of
	   the page can take a name off the offer, and a name nobody is offering any more must not be
	   created because it was ticked before that row left. */
	let making = $state<Missing[]>([]);
	/* What was chosen about each disagreeing field, by match and then by field key. Held here and
	   not in the row, because the press that sends it is here: a row keeping its own answer would
	   be an answer the button cannot see. */
	let answers = $state<Record<string, Record<string, Answer>>>({});
	/* Which rows are in the press. Everything, until somebody takes one out: the screen exists to
	   settle a page, so starting with nothing ticked would make the common case forty presses. */
	let left = $state<Set<string>>(new Set());

	const chosen = $derived(items.filter((one) => !left.has(matchKey(one))));
	const creates = $derived(wouldCreate(chosen));
	const writes = $derived(wouldWrite(chosen, making));

	/* A page turned, or the state moved. The anchor in the address is honoured once, on arrival;
	   the other state is another list, so it starts at its top with no anchor carried across. */
	let drawnFor: MatchState | null = null;
	$effect(() => {
		void paging.offset;
		void paging.size;
		const state = show;
		if (arriving) {
			arriving = false;
			drawnFor = state;
			paging.arrive(untrack(() => anchorIn(address.url)));
		} else if (state !== drawnFor) {
			drawnFor = state;
			// A page other than the first is moved to the first, and that move is what reads it.
			if (untrack(() => paging.restart())) return;
		}
		untrack(() => void load());
	});

	/*
	 * The pager, drawn in the frame's foot by the route (see `PagerProps`). Handed a noun, as its
	 * `noun` asks, so an empty tab reads properly.
	 */
	$effect(() => {
		onpaging?.(
			paging.asPager(
				items.length,
				total,
				show === 'answered' ? 'answered matches' : 'matches to review'
			)
		);
	});

	async function load() {
		failed = null;
		const state = show;
		try {
			const page = await paging.fill(
				state,
				() => items,
				(query) => {
					// Only when a request goes out: a landing asks nothing.
					loading = true;
					return waiting(query, state);
				},
				(answer) => ({ rows: answer.matches, total: answer.total, offset: answer.offset ?? 0 })
			);
			// Overtaken by a newer read, which finishes this one's work.
			if (page === null) return;
			items = page.rows;
			total = page.total;
			if (page.answer) {
				answeredFiles = page.answer.answered;
				left = new Set();
			}
			// In the same synchronous turn as the rows. See `CardPaging.land`.
			paging.land(page.offset);
			const first = items[0];
			rememberAnchor(address.url, path, first ? matchKey(first) : null, page.offset);
		} catch (error) {
			failed = problemFrom(error);
		} finally {
			loading = false;
		}
	}

	/** One disagreement, answered. Kept per match, because the same field on two files is two rows. */
	function answer_(one: Match, field: string, answer: Answer) {
		answers = {
			...answers,
			[matchKey(one)]: { ...(answers[matchKey(one)] ?? {}), [field]: answer }
		};
	}

	function toggle(one: Match) {
		const next = new Set(left);
		if (next.has(matchKey(one))) next.delete(matchKey(one));
		else next.add(matchKey(one));
		left = next;
	}

	async function confirm() {
		if (chosen.length === 0) return;
		busy = true;
		failed = null;
		try {
			await apply(
				chosen,
				making.filter((row) => creates.some((one) => rowKey(one) === rowKey(row))),
				toSettle(chosen, answers)
			);
			answered.stamp = Date.now();
			await load();
		} catch (error) {
			failed = problemFrom(error);
		} finally {
			busy = false;
		}
	}

	async function decline() {
		if (chosen.length === 0) return;
		busy = true;
		failed = null;
		try {
			await refuse(chosen);
			answered.stamp = Date.now();
			await load();
		} catch (error) {
			failed = problemFrom(error);
		} finally {
			busy = false;
		}
	}

	/** What one field is called, from the registry every record screen reads. A field this version
	 *  has never heard of falls back to its own key rather than drawing nothing. */
	function labelOf(field: string): string {
		return fields.one('asset', field)?.label ?? field;
	}

	/** A value as one short line. A record row draws these properly; here they only have to be
	 *  recognisable side by side, and a wrapped list of forty tags would bury the row it is in. */
	function short(value: unknown): string {
		if (value === null || value === undefined || value === '') return 'nothing';
		if (Array.isArray(value)) return value.map((one) => String(one)).join(', ') || 'nothing';
		return String(value);
	}

	function sureness(grade: Match['grade']): string {
		if (grade === 'certain') return 'Exact fingerprint';
		if (grade === 'likely') return 'Looks the same, length agrees';
		return 'Looks the same, length unchecked';
	}
</script>

<section class="pile" aria-label="Files a stash-box recognized">
	<!-- No heading and no lede of its own: the page's title names the pile and the page's lede
	     (the queue's own sentence, from the server) says what it is for, Auto-enrich and all. A
	     second paragraph here would say it again in a smaller type. -->

	<Problem message={failed} />

	<!-- Only while there is nothing on screen yet. A reload after a decision keeps what is
	     already drawn and swaps it when the answer arrives; showing the skeleton again makes the
	     page blink out and back for every single answer, which is the one thing somebody working
	     through a queue does over and over. `EntityGrid` does it this way too. -->
	{#if loading && items.length === 0}
		<Skeleton lines={3} />
	{:else if items.length === 0}
		<Empty
			scope="page"
			icon="shoppingmode"
			title={show === 'answered'
				? 'Nothing answered yet'
				: answeredFiles > 0
					? 'Nothing waiting'
					: 'Nothing to review'}
		>
			{#if show === 'answered'}
				The matches you answer here are listed on this tab.
			{:else if answeredFiles > 0}
				<!-- Every recognised file has been answered: 0 here beside a full Enriched tab would
				     read as a feature nobody had switched on. -->
				All {counted(answeredFiles)}
				{answeredFiles === 1 ? 'file' : 'files'} a stash-box recognized
				{answeredFiles === 1 ? 'has' : 'have'} been answered.
				<a href="/browse?enriched=stash">See them in Browse</a>.
			{:else}
				Sift looks up new files on the stash-boxes once their fingerprints are ready, and matches
				that aren't certain appear here. Turn on matching first, in
				<SettingLink section="stash-boxes" setting="stash_boxes.scan">Settings</SettingLink>.
			{/if}
		</Empty>
	{:else}
		<ul class="rows">
			{#each items as one (matchKey(one))}
				{@const taken = !left.has(matchKey(one))}
				{@const match = one}
				<MatchRow
					{match}
					{taken}
					settled={show === 'answered'}
					ontoggle={() => toggle(one)}
					answers={answers[matchKey(one)] ?? {}}
					onanswer={(field, answer) => answer_(one, field, answer)}
				/>
			{/each}
		</ul>

		{#if show === 'waiting'}
			<!--
			The list of what would be invented sits IN the page, above the bar, and not inside it. The
			bar is sticky, so a list of thirty-one entries inside it grows a pinned block taller than
			the window: the rows it is a consequence OF are pushed off the screen by the statement
			about them. Long content scrolls with the page; only the summary and the press stay put.
		-->
			{#if creates.length > 0}
				<CreatesList names={creates} bind:chosen={making} />
			{/if}

			<!--
			The consequences, above the button and not after it. This is the whole reason a page can be
			settled in one press: the count of fields, and how many rows would be invented.
		-->
			<footer class="settle">
				<p class="tally">
					{counted(chosen.length)} of {counted(items.length)} selected —
					<strong>{writes}</strong>
					{writes === 1 ? 'field' : 'fields'} would be written{creates.length > 0
						? `, ${making.length} of ${creates.length} new entries created`
						: ''}.
					{#if total > items.length}
						<span class="quiet">{total} waiting in all.</span>
					{/if}
				</p>

				<div class="verbs">
					<Button
						type="button"
						tone="primary"
						{busy}
						disabled={chosen.length === 0}
						onclick={confirm}
					>
						Apply to {chosen.length}
						{chosen.length === 1 ? 'file' : 'files'}
					</Button>
					<Button
						type="button"
						tone="quiet"
						disabled={busy || chosen.length === 0}
						onclick={decline}
					>
						Discard
					</Button>
				</div>
			</footer>
		{/if}
	{/if}
</section>

<!-- The pile's two states, at the far end of the tab line: the same rows, waiting or answered. -->
{#snippet stateChips()}
	<span class="states">
		<Chip shape="square" tone={show === 'waiting' ? 'accent' : 'quiet'} href="/organize/tagger"
			>Waiting</Chip
		>
		<Chip
			shape="square"
			tone={show === 'answered' ? 'accent' : 'quiet'}
			href="/organize/tagger?show=answered">Answered</Chip
		>
	</span>
{/snippet}

<style>
	.states {
		display: inline-flex;
		gap: var(--space-2);
	}

	.pile {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
	}

	.rows {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.settle {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		padding: var(--space-4);
		border: 1px solid var(--sift-line);
		background: var(--sift-surface-2);
		position: sticky;
		bottom: 0;
	}

	.tally {
		margin: 0;
		color: var(--sift-ink);
		font: var(--text-body-sm);
	}

	.verbs {
		display: flex;
		gap: var(--space-2);
	}
</style>
