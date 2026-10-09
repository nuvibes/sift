<script lang="ts">
	/* One stash-box's claim about one file, with every field it would change beside what is there:
	 * one component for the Tagger pile and a file's chooser, so the two cannot drift. */
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
		/** Drawn as answered: no tick, the answer in its place. */
		settled?: boolean;
	}

	let { match, taken, ontoggle, answers = {}, onanswer, settled = false }: Props = $props();

	/** What was answered, as the word the row wears in place of its tick. */
	const answered = $derived(match.state === 'refused' ? 'Discarded' : 'Confirmed');

	/* A disagreement is answered by pressing a value; one survives, as a conflicting field holds
	   one value (the server refuses `take="both"`). */
	function answerFor(change: FieldChange): Answer {
		return answers[change.key] ?? 'mine';
	}

	function keeps(change: FieldChange, side: 'mine' | 'theirs'): boolean {
		return answerFor(change) === side;
	}

	/** Press a value to keep it; pressing the kept one does nothing. */
	function press(change: FieldChange, side: 'mine' | 'theirs'): void {
		// Keeping your own sends nothing.
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

	/** A value as one short line, recognisable side by side. */
	function short(value: unknown): string {
		if (value === null || value === undefined || value === '') return 'nothing';
		if (Array.isArray(value)) return value.map(said).join(', ') || 'nothing';
		return said(value);
	}

	/** One value in words; a username entry is said as a person would. */
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
	<!-- The row's token lets the still be kept (`Thumb.art`); it opens the file in place. -->
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
			<!-- A Chip, not a Badge: how sure Sift is, not a state of work. -->
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
							Both values, each a press: a file has nowhere else to settle it.
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
									<!-- The app's checkbox, so a value reads as pressable. -->
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

	/* A row left out stays put and goes quiet, so the list never jumps. */
	.row.left {
		opacity: 0.5;
	}

	/* Global, anchored by this file's class: it lands on Pressable's element. */
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

	/* Two columns, so names and values line up. */
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

	/* Wraps anywhere, so a long link stays inside and the column is measured right. */
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

	/* A question row wraps: two values, two presses and a sentence. */
	.changes dd.asked {
		flex-wrap: wrap;
		row-gap: var(--space-1);
	}

	/* Struck through means "this one is going", and which one that is depends on the answer. */
	.dropped {
		color: var(--sift-ink-3);
		text-decoration: line-through;
	}

	/* Global, as above. */
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
