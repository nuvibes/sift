<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';
	import { exactly } from '$lib/shell/when';

	export const design = {
		name: 'HistoryRow',
		category: 'composition',
		role: 'one thing that happened to something, as a mark, a sentence, a time and a way back',
		basis: 'composes:Button,HistorySentence',
		states: [
			'yours',
			"somebody else's",
			'automatic',
			'undoable',
			'undone',
			'unrecorded time',
			'names something',
			'stands for a whole press'
		]
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: there is nothing here for a component library to own. It is a glyph, two
	   lines of text and one button, and the only behaviour is the button, which is Sift's own
	   `Button`. Nothing opens, nothing floats, nothing traps focus. */

	/*
	 * One event, one row, and only one of these in the whole app on purpose.
	 *
	 * A person and a queue have histories of the same shape as a file's, drawn from the same
	 * events, so one row means one answer to where the mark sits, what a reversed entry looks like,
	 * and how an event with no time is drawn.
	 *
	 * It knows nothing about what it is drawing. The sentence arrives finished, written by the
	 * server in the app's own voice, so the words match the rest of Sift for the same act; a row
	 * that assembled them would be a second vocabulary.
	 *
	 * The mark says who, and that is the one thing colour is spent on: a ring means this account
	 * did it, and everything else (Sift's own passes, a stash-box, another account, somebody
	 * unnamed) is plain, because "which of these did I do" is the question somebody scanning a
	 * history asks.
	 *
	 * The line is the server's pieces, drawn by `HistorySentence`, the same drawing the Settings
	 * feed and its Decisions use. It begins with who did it ("You added ...", "Sift filed ..."),
	 * so no separate name sits beside the time.
	 *
	 * Undo is a request, never a promise. The button appears when the server said this event can be
	 * taken back (only the last move of a file, and only by an admin); an affordance the server
	 * would refuse is worse than none.
	 */
	import Button from './Button.svelte';
	import HistorySentence from './HistorySentence.svelte';
	import Tooltip from './Tooltip.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { BY_YOU, markOf, markWords, sinceOf, spanText, type HistoryEvent } from './history';

	interface Props {
		event: HistoryEvent;
		/**
		 * What to do when Undo is pressed. Absent, no Undo is drawn at all.
		 *
		 * The caller wires the request, because which address takes an event back is a fact about
		 * the API and not about a row, and the row is drawn in places that have no business
		 * knowing it. The event says which door through `undo.kind`.
		 */
		onundo?: (event: HistoryEvent) => void;
		/** Waiting on the undo this row asked for. Stops a second press while the first is in flight. */
		undoing?: boolean;
	}

	let { event, onundo, undoing = false }: Props = $props();

	/* Undoable, drawn: the server offered a way back, it has not already been taken, and somebody is
	   listening. All three, because any one of them alone draws a button that does nothing. */
	const canUndo = $derived(event.undo !== null && !event.reversed && onundo !== undefined);

	/* What the mark means, in words. `markOf` and this answer the same question. See there. */
	const means = $derived(markWords(event));

	/* What else a decision wrote, under its line while it stands; read through a narrow type until
	   the generated schema carries the field. */
	const more = $derived(event.reversed ? '' : (event.more ?? ''));
</script>

<div class="history-row">
	<!--
		The mark, with what it means on hover and on focus: a glyph is a picture of a category and
		nothing says which until you know. The words are the server's, sent beside the line
		(`means`); a task's own mark reads the filter's label for it (`markWords`).

		`role="img"` named by what the mark means, because a span holding a ligature would otherwise
		be read out as the icon's own name, and the stored kind is a machine word.
	-->
	<Tooltip label={means}>
		<span class="mark" class:mine={event.actor === BY_YOU} role="img" aria-label={means}>
			<Icon name={markOf(event.kind, event.via, event.how)} size={14} />
		</span>
	</Tooltip>

	<div class="said">
		<!-- The line, from the server's pieces, and what it stands for under "Show each". Every
		     History surface draws a line through `HistorySentence`, so a name, a thing that has
		     gone and a folded list look and behave the same everywhere. -->
		<div class="what" class:taken-back={event.reversed}>
			<HistorySentence
				pieces={event.pieces}
				what={event.what}
				detail={event.detail}
				quiet={event.reversed}
				away={event.away ?? null}
			/>
		</div>
		{#if more}
			<p class="more">{more}</p>
		{/if}
		<p class="when">
			<!-- A span where the line stands for a run (a day's downloads), one moment otherwise. -->
			{#if event.at !== null}
				<Tooltip label={exactly(event.at)}>
					<span class="moment">{spanText(event.at, sinceOf(event))}</span>
				</Tooltip>
			{:else}
				<span class="moment unrecorded">{spanText(event.at, sinceOf(event))}</span>
			{/if}
			{#if event.reversed}
				<span class="reversed">Undone</span>
			{/if}
		</p>
	</div>

	{#if canUndo}
		<Button icon="undo" tone="ghost" size="small" busy={undoing} onclick={() => onundo?.(event)}
			>Undo</Button
		>
	{/if}
</div>

<style>
	/*
	 * The mark, what was said, and whatever can be done about it. Aligned to the START rather than
	 * centred: a long sentence wraps to two or three lines, and a mark floating level with the
	 * middle of them reads as belonging to none of them.
	 */
	.history-row {
		display: flex;
		align-items: flex-start;
		gap: var(--space-3);
	}

	/*
	 * A small disc with the glyph in it. Round, because the line the list draws runs through the
	 * middle of these and a rounded rectangle on a vertical line reads as a step in a form.
	 *
	 * `flex: none` so a long sentence cannot squeeze it into an oval, which is what happens to any
	 * fixed-size flex child that is allowed to shrink.
	 */
	.mark {
		display: flex;
		flex: none;
		align-items: center;
		justify-content: center;
		inline-size: var(--space-6);
		block-size: var(--space-6);
		border: 1px solid var(--sift-line);
		border-radius: var(--radius-full);
		background: var(--sift-surface-2);
		color: var(--sift-ink-3);
	}

	/* This account's own act. The one thing colour is spent on here. See the header. The glyph
	   takes the accent's TEXT colour, not the fill: the fill on its own tint is 3.2:1, which the
	   contrast test refuses for anything drawn as a mark on a surface. */
	.mark.mine {
		border-color: var(--sift-accent);
		background: var(--sift-accent-bg);
		color: var(--sift-accent-text);
	}

	/* The two lines, and `min-inline-size: 0` because a flex item's floor is its content: without
	   it a long unbroken sentence pushes the Undo off the row instead of wrapping. */
	.said {
		flex: 1 1 auto;
		min-inline-size: 0;
	}

	.what {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink);
	}

	/* An event that was undone is still something that happened, so it stays legible rather than
	   being struck through: a line through a sentence is read as "this is not true", and it was true
	   at the time. Quieter ink says it no longer stands, and `HistorySentence` takes its links down. */
	.what.taken-back {
		color: var(--sift-ink-3);
	}

	/* What else the decision wrote: the line's own size, in caption ink, so it reads as part of
	   the line and not as its footnote. */
	.more {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	/* The footnote under it: when, and whether it still stands. Who did it is the line's first word. */
	.when {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2);
		margin: 0;
		font: var(--text-label);
		color: var(--sift-ink-3);
	}

	/* A time nobody recorded. Set in italic rather than given a colour of its own: it is a phrase
	   where every other row has a date, and the shape of the words is what says so at a glance. */
	.moment.unrecorded {
		font-style: italic;
	}

	/* Caption ink, not the decoration grey: "Undone" is a word somebody reads. */
	.reversed {
		color: var(--sift-ink-3);
	}
</style>
