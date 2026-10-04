<script lang="ts">
	/*
	 * What the stash-boxes make of ONE file, in a sheet, with the decision left to whoever opened it.
	 *
	 * ## Why this exists beside the pile
	 *
	 * Enrich on a person opens a chooser: their name, the answers, and a choice. Enrich on a FILE
	 * that only queued a job and said "watch it under Organize" would be a different promise from
	 * the same word on the same menu, and on one file the wrong one. Somebody who right-clicked a
	 * single clip is asking about that clip, not starting a batch.
	 *
	 * So this is the file's version of the same sheet. The pile under Organize is still what a
	 * library-wide sweep fills and is still where a hundred of them are worked through at once;
	 * this is the one-file door into exactly the same rows, the same consequences and the same two
	 * answers.
	 *
	 * ## Why it asks first and then waits
	 *
	 * A match only exists once a box has been asked, and asking is a request to somebody else's
	 * service. So opening this queues the per-file job (the same job the watcher runs for a file
	 * that has just landed) and then watches for what it wrote. Nothing here asks a stash-box
	 * directly: the job is where the pacing, the key and the recording live, and a second path to
	 * the same services would be a second place for all three.
	 *
	 * WHY NOT BITS-UI: it is a `Modal`, which is the one door to the dialog primitives.
	 */
	import Modal from '$lib/components/common/Modal.svelte';
	import CreatesList from '$lib/components/organize/CreatesList.svelte';
	import MatchRow from '$lib/components/organize/MatchRow.svelte';
	import { Button, Empty, Problem, SettingLink } from '$lib/components/common';
	import {
		answeredFor,
		apply,
		type Match,
		refuse,
		waitingFor,
		wouldCreate,
		wouldWrite,
		type Answer,
		toSettle,
		matchKey,
		type Missing,
		rowKey
	} from '$lib/entity/tagger.svelte';
	import { enrichFiles } from '$lib/entity/enrich-many.svelte';
	import { undoDecision } from '$lib/api/history';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { StashBoxes } from '$lib/settings-ui/stash-boxes.svelte';
	import { exactly } from '$lib/shell/when';

	interface Props {
		open?: boolean;
		/** The file being asked about. */
		assetId: string;
		/** Told when something was written, so the screen behind can redraw. */
		onapplied?: () => void;
	}

	let { open = $bindable(false), assetId, onapplied }: Props = $props();

	let items = $state<Match[]>([]);
	/* What a box already said about this file and somebody applied or discarded. Nothing waits for
	   a file whose match was applied, so without these the sheet would say nothing recognized it. */
	let settled = $state<Match[]>([]);
	let left = $state(new Set<string>());
	let asking = $state(false);
	let busy = $state(false);
	/* The names ticked for creation. Pruned at the press rather than watched: a row taken out of
	   the sheet can take a name off the offer, and a name nobody is offering any more must not be
	   created because it was ticked before that row left. */
	let making = $state<Missing[]>([]);
	/* What was chosen about each disagreeing field, by match and then by field key. Held here and
	   not in the row, because the press that sends it is here: a row keeping its own answer would
	   be an answer the button cannot see. */
	let answers = $state<Record<string, Record<string, Answer>>>({});
	let problem = $state<string | undefined>(undefined);
	/** Whether an answer has been looked for at all yet, which is what tells "none" from "not yet". */
	let looked = $state(false);
	/** Asked again, and nothing new came back: the settled answer stands, and the sheet says so. */
	let unchanged = $state(false);

	/* The boxes themselves, because "nothing came back" has more than one cause and only
	   one of them is about this file. See `blocked`. */
	const boxes = new StashBoxes();
	boxes.follow();

	const chosen = $derived(items.filter((one) => !left.has(matchKey(one))));

	/**
	 * Why nothing could be ASKED, when that is the answer rather than "nothing was found".
	 *
	 * An empty sheet would report a fact about the file (nobody recognised it) when the real
	 * state may be that no stash-box could be reached at all. Those look identical from here and are
	 * opposite situations: the first is finished, the second is a thing to go and fix, and saying
	 * the wrong one sends somebody looking at their file instead of at their settings.
	 *
	 * Ordered from the most specific cause to the least, because each one makes the ones after it
	 * meaningless: a key that cannot be opened is not a box that is switched off.
	 */
	const blocked = $derived.by(() => {
		const all = boxes.items;
		if (all.length === 0) return 'No stash-box is set up yet.';
		const on = all.filter((one) => one.enabled);
		if (on.length === 0) return 'Every stash-box is turned off.';
		const locked = on.filter((one) => one.has_key && !one.key_ready);
		if (locked.length === on.length) {
			return (
				`Sift couldn't look this file up. ${locked.length === 1 ? "The one stash-box that's turned on has" : `All ${locked.length} stash-boxes that are turned on have`} a key ` +
				"that's locked because Sift restarted. Enter your password in Unlock at the top of " +
				'Stash-boxes to use it again. Nothing was lost.'
			);
		}
		if (on.every((one) => !one.has_key)) {
			return "No stash-box that's turned on has a key.";
		}
		return null;
	});
	const creates = $derived(wouldCreate(chosen));
	const writes = $derived(wouldWrite(chosen, making));

	/* Opening asks, and then looks. Re-seeded every time rather than once, because the file behind
	   the sheet changes: the same dialog opened on a different row must not show the last one's
	   answers for the moment before the new ones arrive. */
	$effect(() => {
		void assetId;
		if (open) void begin();
	});

	async function begin() {
		items = [];
		settled = [];
		left = new Set();
		making = [];
		answers = {};
		problem = undefined;
		looked = false;
		unchanged = false;
		asking = true;
		try {
			// The boxes first. What comes back decides whether an empty answer is about this file or
			// about the install, and the sheet must not guess.
			await boxes.load();
			// Anything already waiting for this file shows at once: a sweep may have found it days
			// ago, and asking again to learn what is already known is a request nobody needed.
			const held = await waitingFor(assetId);
			if (held.matches.length > 0) {
				items = held.matches;
				return;
			}
			// Answered already: said as it is, and asked again only when somebody presses Ask again.
			const done = await answeredFor(assetId);
			if (done.matches.length > 0) {
				settled = done.matches;
				return;
			}
			// Quiet, because this sheet has somewhere better to put a refusal than a toast that
			// slides away over the top of it, and "switched off in Settings" is the one answer
			// somebody opening this needs, not "nothing was recognised".
			const refused = await enrichFiles([assetId], { quiet: true });
			if (refused) {
				problem = refused;
				return;
			}
			await look();
		} catch {
			problem = "Sift couldn't ask about this file.";
		} finally {
			asking = false;
			looked = true;
		}
	}

	/** Ask the boxes again about a file they have already answered, and watch for what comes back. */
	async function askAgain() {
		asking = true;
		problem = undefined;
		unchanged = false;
		try {
			// `again`: a settled answer is otherwise left alone by a later ask, so without it this
			// press would ask the boxes and then show exactly what it showed before.
			const refused = await enrichFiles([assetId], { quiet: true, again: true });
			if (refused) {
				problem = refused;
				return;
			}
			await look();
			unchanged = items.length === 0;
		} catch {
			problem = "Sift couldn't ask about this file.";
		} finally {
			asking = false;
			looked = true;
		}
	}

	/** One settled answer as a sentence: which box, what it matched, and what was done when. */
	function settledLine(one: Match): string {
		const done = one.state === 'refused' ? 'Discarded' : 'Applied';
		const at = one.decided_at ?? one.found_at;
		return `${one.box_name} matched this file to ${one.record.name}. ${done} ${exactly(at)}.`;
	}

	/*
	 * Watching for what the job wrote.
	 *
	 * The job is queued rather than run here, so the answer arrives after this returns. Six looks a
	 * second apart: a box that answers takes about a second, three boxes paced by their own limiter
	 * take a few, and a box that is slow or down produces nothing to wait for, so this stops
	 * rather than spinning, and says plainly that nothing came back.
	 */
	async function look() {
		for (let attempt = 0; attempt < 6; attempt += 1) {
			await new Promise((wake) => setTimeout(wake, 1000));
			const page = await waitingFor(assetId);
			if (page.matches.length > 0) {
				items = page.matches;
				return;
			}
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
		const id = matchKey(one);
		const next = new Set(left);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		left = next;
	}

	async function agree() {
		busy = true;
		problem = undefined;
		try {
			const done = await apply(
				chosen,
				making.filter((row) => creates.some((one) => rowKey(one) === rowKey(row))),
				toSettle(chosen, answers)
			);
			toasts.show(done.fields === 1 ? 'One field written' : `${done.fields} fields written`, {
				tone: 'success'
			});
			onapplied?.();
			open = false;
		} catch {
			problem = "That couldn't be applied.";
		} finally {
			busy = false;
		}
	}

	/**
	 * Discard an answer already APPLIED: the same press as a waiting one's, and the same word, with
	 * what it wrote taken back off the file (its title and fields, its people, tags, Site and
	 * usernames). No question first, as with a waiting answer's Discard; the toast offers the Undo
	 * the server wrote, and the file's History keeps it.
	 */
	async function discardApplied(one: Match) {
		busy = true;
		problem = undefined;
		try {
			const done = await refuse([one]);
			const receipt = done.decision_id;
			toasts.show(`Discarded. Sift took off what ${one.box_name} said about this file.`, {
				tone: 'info',
				action: receipt
					? {
							label: 'Undo',
							run: () => void undoDiscard(receipt)
						}
					: undefined
			});
			onapplied?.();
			await begin();
		} catch {
			problem = "That couldn't be discarded.";
		} finally {
			busy = false;
		}
	}

	/** The toast's Undo: the take-back's own receipt, through the one door every Undo uses. */
	async function undoDiscard(receipt: string) {
		try {
			await undoDecision(receipt);
			onapplied?.();
			if (open) await begin();
		} catch {
			toasts.show("That couldn't be undone", { tone: 'error' });
		}
	}

	async function decline() {
		busy = true;
		problem = undefined;
		try {
			await refuse(items);
			toasts.show("Discarded. Sift won't suggest this match again.", { tone: 'info' });
			open = false;
		} catch {
			problem = "That couldn't be discarded.";
		} finally {
			busy = false;
		}
	}
</script>

<Modal
	bind:open
	title="Stash-box matches for this file"
	description="Matched by the file's fingerprints. Apply writes what a match says onto the file, and Discard takes it back."
	sheetClass="file-matches"
>
	{#snippet children()}
		<Problem message={problem} />

		<!-- Loud, and above everything: when nothing could be ASKED, that is the answer, and the
		     quiet grey sentence underneath is about a different question. -->
		<Problem message={looked && items.length === 0 ? blocked : null}>
			{#snippet action()}
				<!-- Every one of these is fixed on the same pane, so the way out goes here rather than
				     at the end of four sentences that each had to name it. -->
				<SettingLink section="stash-boxes">Open Stash-boxes</SettingLink>
			{/snippet}
		</Problem>

		{#if asking}
			<Empty scope="block" busy>Looking this file up on each stash-box that's turned on.</Empty>
		{:else if items.length === 0 && settled.length > 0}
			<!-- A file a box already matched: what it matched and what was done, never "nothing
			     recognized it". Ask again is in the foot. -->
			<ul class="settled" aria-label="Already answered">
				{#each settled as one (matchKey(one))}
					<li>
						<span>{settledLine(one)}</span>
						{#if one.state === 'applied'}
							<!-- The waiting answer's own press, on an answer that stands: what it wrote
							     comes off this file. -->
							<Button tone="ghost" icon="remove" {busy} onclick={() => void discardApplied(one)}
								>Discard</Button
							>
						{/if}
					</li>
				{/each}
			</ul>
			{#if unchanged}
				<p class="again">Asked again. Nothing new came back, so this answer still stands.</p>
			{/if}
		{:else if items.length === 0 && (blocked || problem)}
			<!-- Said above, in the Problem, so not twice in two tones. A refusal the server sent
			     (kept local, or the boxes switched off) must not be followed by "No stash-box
			     recognised this file": nothing was asked, so nothing failed to recognise anything. -->
		{:else if items.length === 0}
			<!-- Told apart from "not asked yet" by `looked`, because they are opposite answers: one is
			     a finished question with no answer, the other is a question still in flight. -->
			<Empty scope="block">
				{looked
					? 'No stash-box recognized this file. This usually means no one has submitted it yet, not that anything is wrong.'
					: 'Nothing yet.'}
			</Empty>
		{:else}
			<ul class="rows">
				{#each items as one (matchKey(one))}
					<MatchRow
						match={one}
						taken={!left.has(matchKey(one))}
						ontoggle={() => toggle(one)}
						answers={answers[matchKey(one)] ?? {}}
						onanswer={(field, answer) => answer_(one, field, answer)}
					/>
				{/each}
			</ul>

			<!--
				In the BODY, which scrolls, and not in the foot, which does not. A sheet is capped at the
				height of the window and its foot takes whatever room it asks for: thirty-one entries in
				there would grow a foot taller than the whole sheet, so the scrolling region above it
				would collapse to nothing and the match this is all about (the thumbnail, the title,
				every field that would be written) would not be on the screen at all. Only the summary and
				the two answers stay pinned.
			-->
			{#if creates.length > 0}
				<div class="invent"><CreatesList names={creates} bind:chosen={making} /></div>
			{/if}
		{/if}
	{/snippet}

	{#snippet footer()}
		{#if items.length > 0}
			<!-- The consequences above the answer, exactly as the pile states them. The count of
			     fields, and the names that do not exist yet listed by name rather than only counted:
			     agreeing is only a decision if what it does is on the screen while you decide. -->
			<div class="settle">
				<p class="tally">
					<strong>{writes}</strong>
					{writes === 1 ? 'field' : 'fields'} would be written{creates.length > 0
						? `, ${making.length} of ${creates.length} new entries created`
						: ''}.
				</p>
				<div class="answers">
					<Button tone="ghost" icon="remove" {busy} onclick={() => void decline()}>Discard</Button>
					<Button tone="primary" {busy} disabled={chosen.length === 0} onclick={() => void agree()}>
						Apply
					</Button>
				</div>
			</div>
		{:else if settled.length > 0 && !blocked}
			<div class="answers">
				<Button tone="secondary" busy={asking} onclick={() => void askAgain()}
					>Identify again</Button
				>
			</div>
		{/if}
	{/snippet}
</Modal>

<style>
	/* Wider than an ordinary sheet, for the same reason the stash-box chooser is: a match row lays
	   a field name beside its value in two columns, and a pair of columns inside 420 pixels is two
	   columns of one word each: a details paragraph four words wide and a link cut off at the
	   edge. The default `.sheet` width is right for a question and wrong for a
	   record. */
	:global(.file-matches) {
		/* The width only. `.sheet` clamps it to the window. See app.css. */
		--sheet-inline: 46rem;
	}

	.rows {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		margin: var(--space-3) 0 0;
		padding: 0;
		list-style: none;
	}

	.settled {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		margin: var(--space-3) 0 0;
		padding: 0;
		list-style: none;
		font: var(--text-body);
		color: var(--sift-ink-2);
	}

	/* The sentence on the left, its press on the right (the row rule). */
	.settled li {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-3);
	}

	.again {
		margin: var(--space-3) 0 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.invent {
		margin-block-start: var(--space-4);
		padding-block-start: var(--space-4);
		border-block-start: 1px solid var(--sift-line);
	}

	.settle {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	.tally {
		margin: 0;
		color: var(--sift-ink-2);
	}

	.answers {
		display: flex;
		justify-content: flex-end;
		gap: var(--space-2);
	}
</style>
