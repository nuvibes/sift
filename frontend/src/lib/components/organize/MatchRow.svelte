<script lang="ts">
	/*
	 * ONE stash-box's claim about ONE file, with everything applying it would change.
	 *
	 * ## Why this is a component and not markup inside the pile
	 *
	 * Because two surfaces draw it. The Tagger pile under Organize works through everything
	 * waiting; the chooser on a file's own right-click menu shows what is waiting for that one
	 * file. They are the same row, and the row is the whole safety story of this feature: it is
	 * what lets somebody agree to a page of matches without it being a leap of faith, because
	 * every field that would be written is named with what is already there beside it.
	 *
	 * Written twice, the two would drift, and the drift would be silent and one-directional: a
	 * conflict marked in one place and not the other reads as "this will be written" on the screen
	 * that forgot. That is the one thing a screen stating consequences may not get wrong.
	 */
	import { Checkbox, Chip, Empty, Pressable } from '$lib/components/common';
	import { openAssetInstead } from '$lib/player/asset-view';
	import Icon from '$lib/components/Icon.svelte';
	import Thumb from '$lib/components/organize/Thumb.svelte';
	import { fields } from '$lib/entity/records.svelte';
	import type { Answer, FieldChange, Match } from '$lib/entity/tagger.svelte';

	interface Props {
		match: Match;
		/** Whether this one is included in the press. */
		taken: boolean;
		ontoggle: () => void;
		/** What was chosen for each disagreeing field, by field key. Absent means keep what is here. */
		answers?: Record<string, Answer>;
		/** Told which field was answered and how. Absent leaves the disagreements unanswerable. */
		onanswer?: (key: string, answer: Answer) => void;
		/**
		 * Drawn as a row already answered: no tick, the answer said in its place. The pile's
		 * Answered state shows the same rows as Waiting, so it is this row in another state rather
		 * than a second row.
		 */
		settled?: boolean;
	}

	let { match, taken, ontoggle, answers = {}, onanswer, settled = false }: Props = $props();

	/** What was answered, as the word the row wears in place of its tick. */
	const answered = $derived(match.state === 'refused' ? 'Discarded' : 'Confirmed');

	/*
	 * Answering a disagreement by pressing the values themselves: the two values are on the row
	 * side by side, so they are the control. Nothing is hidden behind a menu; the thing being
	 * decided about is the thing pressed.
	 *
	 * There is no "keep both". A conflict is by definition a field that holds exactly one value: a
	 * field holding many never conflicts, because both answers are merged and kept without asking
	 * (`enrichment.decide` returns a conflict only for a single-valued field). So pressing one
	 * turns the other off; "both" would promise a write that cannot happen and put "a, b" where one
	 * name goes. The server still refuses `take="both"` on the write, because that guards an
	 * untrusted body.
	 */
	function answerFor(change: FieldChange): Answer {
		return answers[change.key] ?? 'mine';
	}

	function keeps(change: FieldChange, side: 'mine' | 'theirs'): boolean {
		return answerFor(change) === side;
	}

	/** Press a value to keep it. Exactly one of the two survives: keeping neither is not an
	 *  answer, so pressing the one already kept does nothing rather than clearing the row. */
	function press(change: FieldChange, side: 'mine' | 'theirs'): void {
		// Pressing the side already kept changes nothing, so it says nothing. Keeping your own
		// answer is the default and sends nothing at all, which is what the write expects.
		if (answerFor(change) === side) return;
		onanswer?.(change.key, side);
	}

	/** What the press would do, said in words, because two struck-through values are not a sentence. */
	function outcome(change: FieldChange): string {
		return answerFor(change) === 'theirs' ? 'taking theirs' : 'keeping what is here';
	}

	function labelOf(field: string): string {
		return fields.one('asset', field)?.label ?? field;
	}

	/** A value as one short line. A record row draws these properly; here they only have to be
	 *  recognisable side by side, and a wrapped list of forty tags would bury the row it is in. */
	function short(value: unknown): string {
		if (value === null || value === undefined || value === '') return 'nothing';
		if (Array.isArray(value)) return value.map(said).join(', ') || 'nothing';
		return said(value);
	}

	/** One value in words. A username arrives as an entry (`handle`, `site`, `url`), and an entry
	 *  printed as text reads "[object Object]", so it is said the way a person would say it. */
	function said(value: unknown): string {
		if (value !== null && typeof value === 'object') {
			const entry = value as { handle?: unknown; site?: unknown; name?: unknown };
			if (typeof entry.handle === 'string' && entry.handle) {
				return typeof entry.site === 'string' && entry.site
					? `${entry.handle} on ${entry.site}`
					: entry.handle;
			}
			if (typeof entry.name === 'string' && entry.name) return entry.name;
		}
		return String(value);
	}

	function sureness(grade: Match['grade']): string {
		if (grade === 'certain') return 'Exact fingerprint';
		if (grade === 'likely') return 'Looks the same, length agrees';
		return 'Looks the same, length unchecked';
	}
</script>

<li class="row" class:left={!taken && !settled}>
	{#if settled}
		<Chip size="sm" tone={match.state === 'refused' ? 'quiet' : 'accent'} inert>{answered}</Chip>
	{:else}
		<Pressable
			class="tick"
			feedback="wash"
			radius="sm"
			aria-pressed={taken}
			aria-label={taken ? `Leave out ${match.record.name}` : `Include ${match.record.name}`}
			onclick={ontoggle}
		>
			<Checkbox state={taken ? 'on' : 'off'} mark />
		</Pressable>
	{/if}
	<!-- The token comes down with the row, so this still may be kept for a week. See `Thumb.art`. -->
	<!-- The picture opens the file exactly as the title does: in the viewer, in place. -->
	<Thumb
		kind="asset"
		id={match.asset_id}
		art={match.art}
		href="/asset/{match.asset_id}"
		onclick={(event) => openAssetInstead(event, match.asset_id)}
	/>
	<div class="what">
		<a
			class="name"
			href="/asset/{match.asset_id}"
			onclick={(event) => openAssetInstead(event, match.asset_id)}>{match.record.name}</a
		>
		<p class="where">
			<!-- A Chip and not a Badge, and the difference is what the word MEANS. A badge carries the
			     state of a piece of work (queued, working, failed) and nothing here is working.
			     This is how sure Sift is about a claim, which is a fact about the row rather than a
			     stage it is passing through. -->
			<Chip size="sm" tone={match.grade === 'certain' ? 'accent' : 'quiet'} inert>
				{sureness(match.grade)}
			</Chip>
			<span class="box">{match.box_name}</span>
		</p>
		{#if match.changes.length > 0}
			<dl class="changes">
				{#each match.changes as change (change.key)}
					<dt>{labelOf(change.key)}</dt>
					<dd class:asked={change.outcome === 'conflict'}>
						{#if change.outcome === 'conflict' && onanswer}
							<!--
								Both values, each one a press. "Left alone" and nothing more would be the end
								of the line on a FILE: the screen that settles disagreements reads linked
								people, sites and tags and never files, so there would be nowhere else to go and
								no way to take the stash-box's answer short of typing it in by hand.
							-->
							{#each [{ side: 'mine', value: change.mine, word: 'Keep' }, { side: 'theirs', value: change.theirs, word: 'Use' }] as one, at (one.side)}
								{#if at === 1}<Icon name="warning" size={16} />{/if}
								{@const on = keeps(change, one.side as 'mine' | 'theirs')}
								<Pressable
									class="side"
									feedback="wash"
									radius="sm"
									aria-pressed={on}
									aria-label="{one.word} {short(one.value)}"
									onclick={() => press(change, one.side as 'mine' | 'theirs')}
								>
									<!-- A BOX, the same box every other list of choices in the app uses. Two values
									     struck through and not struck through would say which survives and nothing
									     at all about being pressable: a value is text, and text on a row of
									     text does not look like a control. -->
									<Checkbox state={on ? 'on' : 'off'} mark />
									<span class:dropped={!on}>{short(one.value)}</span>
								</Pressable>
							{/each}
							<span class="note">{outcome(change)}</span>
						{:else if change.outcome === 'conflict'}
							<span class="dropped">{short(change.mine)}</span>
							<Icon name="warning" size={16} />
							<span class="dropped">{short(change.theirs)}</span>
							<span class="note">left alone — the two disagree</span>
						{:else}
							<span>{short(change.theirs)}</span>
						{/if}
					</dd>
				{/each}
			</dl>
		{:else}
			<Empty scope="block">This would change nothing you don't already have.</Empty>
		{/if}
	</div>
</li>

<style>
	.row {
		display: flex;
		align-items: flex-start;
		gap: var(--space-3);
		padding: var(--space-3);
		border: 1px solid var(--sift-line);
		background: var(--sift-surface-2);
	}

	/* A row taken out of the press stays exactly where it is and goes quiet. One that moved or
	   vanished would make the list jump under the pointer that is working down it. */
	.row.left {
		opacity: 0.5;
	}

	/* `:global`, and anchored by a class this file writes. The class lands on an element compiled
	   in `Pressable`, so a scoped rule for it here would match nothing at all, silently. */
	.row :global(.tick) {
		display: flex;
		align-items: center;
		padding: var(--space-1);
	}

	.what {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		min-width: 0;
		flex: 1 1 auto;
	}

	.name {
		color: var(--sift-ink);
		font: var(--text-label);
		overflow-wrap: anywhere;
	}

	.where {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		margin: 0;
	}

	.box {
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* Two columns, so the field names line up down the row and the values can be read as a column
	   rather than as a paragraph. */
	.changes {
		display: grid;
		grid-template-columns: minmax(6rem, max-content) 1fr;
		gap: var(--space-1) var(--space-3);
		margin: var(--space-1) 0 0;
	}

	.changes dt {
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/*
	 * Wraps, and breaks inside a word where it has to.
	 *
	 * A value here is whatever somebody else's database holds: a paragraph, a list of twenty names,
	 * or a seventy-character link with no space. A link has no break opportunity a browser will
	 * take on its own, so without this it would run past the sheet's right edge.
	 *
	 * `anywhere` rather than `break-word`, because it is also counted when the column is measured,
	 * so the grid stops sizing itself to a link nothing can break.
	 */
	.changes dd {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
		margin: 0;
		min-width: 0;
		overflow-wrap: anywhere;
		color: var(--sift-ink);
		font: var(--text-body-sm);
	}

	.changes dd > span {
		min-inline-size: 0;
	}

	/* A row that is a QUESTION rather than a statement. Every other row states what would happen;
	   this one is waiting to be told, so it wraps: two values, two presses and a sentence do not
	   fit on one line beside a field name. */
	.changes dd.asked {
		flex-wrap: wrap;
		row-gap: var(--space-1);
	}

	/* Struck through means "this one is going", and which one that is depends on the answer. */
	.dropped {
		color: var(--sift-ink-3);
		text-decoration: line-through;
	}

	/* `:global`, and anchored by a class this file writes. The class lands on an element compiled
	   in `Pressable`, so a scoped rule for it here would match nothing at all, silently. */
	.changes :global(.side) {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		padding: var(--space-1) var(--space-2);
		border: 1px solid var(--sift-line);
		border-radius: var(--radius-md);
		color: var(--sift-ink);
		font: var(--text-body-sm);
	}

	.note {
		color: var(--sift-warn);
		font: var(--text-body-sm);
	}
</style>
