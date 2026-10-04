<script lang="ts">
	/*
	 * What happened to one thing, as a panel a screen puts inside a tab.
	 *
	 * The thread is `HistoryList`, which draws events and does not fetch, so the addresses
	 * answering one shape can all use it. This is the other half: the asking, the two states an
	 * array cannot tell apart, and the Undo. A file of its own so it can be tested without mounting
	 * a whole page.
	 *
	 * Not in `common/`: that is the design system (a declaration, a gallery section, a role no
	 * other primitive claims), and a panel that fetches is none of that. The shared part, the list,
	 * is in `common/`.
	 *
	 * NOT ON THE GALLERY: it fetches on mount, so an entry would draw whatever the running library
	 * held. What it draws is on the gallery: `HistoryList`, `HistoryRow`, `Empty` and `Problem`, in
	 * each state named below.
	 *
	 * The subject is a table, not a branch: a person, a site, a tag, a shelf and a Photo Set are
	 * the same panel at five addresses with different words for "it". The table is typed by the
	 * prop, so adding a kind to the prop's type is a missing row the type checker names.
	 *
	 * It justifies left and sets its own measure. The page frame's `measure` bounds a line and
	 * centres it, which would float the thread away from the tabs and title at the page's left
	 * edge. So pages draw it without `measure` and the bound is here, on the same token: no longer
	 * on a large monitor than a laptop, but starting where the page starts.
	 *
	 * Read once per showing. The tab is a branch of the page's markup, so closing it removes this
	 * component, and something happens to a person on the other tabs, so coming back to History
	 * shows it. The price is one read of the default fifty rows per press of the word. The effect
	 * below depends only on which thing this is, so it runs once per subject.
	 *
	 * The check after the await is needed: two people are one address apart and this panel survives
	 * the step between them, so a slow answer for the one somebody left must not land under the
	 * name of the one they are looking at.
	 */
	import type { Snippet } from 'svelte';
	import { Empty, HistoryList, Problem } from '$lib/components/common';
	import {
		historyOfCollection,
		historyOfPerson,
		historyOfPhotoSet,
		historyOfSite,
		historyOfSong,
		historyOfTag,
		undoHistoryEvent
	} from '$lib/api/history';
	import type { HistoryEvent } from '$lib/components/common/history';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { rereadOnHistoryChange } from '$lib/library/changes.svelte';

	interface Props {
		/**
		 * What kind of thing this is the history of.
		 *
		 * It decides the address and the words. See the table below.
		 */
		subject: 'person' | 'site' | 'tag' | 'collection' | 'photo_set' | 'song';
		/** Which one of them, as the server knows it. */
		id: string;
		/**
		 * What to call them while the answer is on its way.
		 *
		 * Optional because the sentence is still true without it, and a panel that refused to draw
		 * until its screen had loaded a name would be blank for exactly as long as the name took.
		 */
		name?: string;
		/**
		 * What is waiting on this thing, drawn in a column beside the thread.
		 *
		 * A snippet, because what goes there (where a stash-box disagrees with the record) belongs
		 * to three of the five kinds and to a slice this panel should not name; the page hands in
		 * what it has.
		 *
		 * On this tab because a question about a record can be as tall as it needs here, where the
		 * header's capped column could not hold it; the mark beside the tab word says it is there.
		 *
		 * Beside the thread, not above or below it: stacked on top, the cards push down the thread
		 * somebody came to read; put underneath, they sit below fifty rows nobody scrolls. As a
		 * column, both are on screen at once. On a window too narrow for two columns the cards go
		 * under the thread: squeezed into half a phone, two values side by side with a button under
		 * each is not a comparison anybody can read, and the tab's mark says they are there.
		 */
		waiting?: Snippet;
	}

	let { subject, id, name, waiting }: Props = $props();

	/** Every address and every word that differs between the kinds, in one place. */
	const SUBJECTS: Record<
		Props['subject'],
		{
			read: (id: string) => Promise<HistoryEvent[]>;
			/** In flight. The name when the screen knows it; a true sentence when it does not. */
			busy: (who: string | undefined) => string;
			/** Asked, and the server holds nothing. */
			nothing: string;
			/** Not asked successfully. Says what failed, not what to do: the way to retry is the tab. */
			failed: string;
		}
	> = {
		person: {
			read: historyOfPerson,
			busy: (who) => (who ? `Reading what happened to ${who}` : 'Reading what happened'),
			nothing: 'Nothing has been recorded about them yet.',
			failed: "That history couldn't be read."
		},
		/* "them" for a person and a noun for the other four, which is the whole of what differs in
		   the words. A site's empty sentence is the one that is not merely theoretical: `sites`
		   records no moment at all, so a site nothing has yet been filed under has a genuinely empty
		   thread rather than one waiting to be written. */
		site: {
			read: historyOfSite,
			busy: (who) => (who ? `Reading what happened to ${who}` : 'Reading what happened'),
			nothing: 'Nothing has been recorded about this Site yet.',
			failed: "That history couldn't be read."
		},
		tag: {
			read: historyOfTag,
			busy: (who) => (who ? `Reading what happened to ${who}` : 'Reading what happened'),
			nothing: 'Nothing has been recorded about this tag yet.',
			failed: "That history couldn't be read."
		},
		collection: {
			read: historyOfCollection,
			busy: (who) => (who ? `Reading what happened to ${who}` : 'Reading what happened'),
			nothing: 'Nothing has been recorded about this collection yet.',
			failed: "That history couldn't be read."
		},
		photo_set: {
			read: historyOfPhotoSet,
			busy: (who) => (who ? `Reading what happened to ${who}` : 'Reading what happened'),
			nothing: 'Nothing has been recorded about this Photo Set yet.',
			failed: "That history couldn't be read."
		},
		song: {
			read: historyOfSong,
			busy: (who) => (who ? `Reading what happened to ${who}` : 'Reading what happened'),
			nothing: 'Nothing has been recorded about this song yet.',
			failed: "That history couldn't be read."
		}
	};

	const words = $derived(SUBJECTS[subject]);

	/*
	 * `null` is "not asked yet" and an empty list is "asked, and nothing is recorded": two states
	 * one array cannot tell apart, and the difference is what is drawn: a busy line or a sentence.
	 */
	let events = $state<HistoryEvent[] | null>(null);
	let failed = $state(false);
	/** The event whose undo is in flight, by its own `undo.id`. One at a time, and named rather
	 *  than a flag, because a spinner on every row would say the whole history is being taken back. */
	let undoing = $state<string | null>(null);

	/** Which thing an answer belongs to. Compared after the await, never before the ask. */
	function whose(subject: Props['subject'], id: string): string {
		return `${subject}:${id}`;
	}

	/**
	 * `quietly` is a re-read of the SAME thing after something was decided about it: the thread on
	 * screen stays up until the new one lands, because blanking a history somebody is reading to a
	 * busy line, for a decision that adds one row to it, reads as the history going away.
	 */
	async function read(quietly = false) {
		const wanted = whose(subject, id);
		// Cleared before the request rather than when it lands, or the last subject's events sit
		// under this one's name for as long as the fetch takes: the words around them are the
		// same either way, so nothing else on screen would give it away.
		if (!quietly) {
			events = null;
			failed = false;
		}
		try {
			const what = await words.read(id);
			if (whose(subject, id) === wanted) {
				events = what;
				failed = false;
			}
		} catch {
			// The failure belongs to the thing that was asked about, as the answer does: a read
			// that failed for somebody nobody is looking at must not tell the reader that this
			// history is unreadable.
			if (whose(subject, id) === wanted) failed = true;
		}
	}

	// Depends on which thing this is, and on nothing it writes: once per subject, and the step to
	// the next one is what asks again.
	$effect(() => {
		if (id) void read();
	});

	/*
	 * And again, quietly, when anything it records may have moved: a stash-box answer pressed
	 * beside this thread, a file added to a collection, a person named or a tag added. The thread
	 * listens to the one bell every write rings (`recorded` for a write in this tab, which
	 * `answered` also rings, and the server's `libraryChanges` for one made anywhere else) through
	 * the helper that owns the pairing and the settling, so this file cannot wire one and forget
	 * the other. The helper never fires on mount, so opening a page still reads once. A write about
	 * something else costs one small read that draws nothing new.
	 */
	rereadOnHistoryChange(() => {
		if (id) void read(true);
	});

	/*
	 * TAKE ONE EVENT BACK, through whichever door recorded it.
	 *
	 * Nothing in any of these five histories carries a door today: an arrival, the files
	 * something was put on, the faces agreed to and refused, a stash-box link, a share: none of
	 * those is a decision that can be found again and reversed, and a row with no door draws no
	 * button. It is wired all the same, through the one module that knows which door a kind of
	 * event opens, so the day a workbench decision can be found from one of these subjects the row
	 * arrives able to be taken back rather than needing this remembered.
	 */
	async function undoEvent(event: HistoryEvent) {
		const door = event.undo;
		if (!door || undoing) return;
		undoing = door.id;
		try {
			await undoHistoryEvent(event);
			await read();
		} catch {
			toasts.show("That couldn't be undone", { tone: 'error' });
		} finally {
			undoing = null;
		}
	}
</script>

<div class="entity-history">
	<div class="thread">
		{#if failed}
			<Problem message={words.failed} />
		{:else if events === null}
			<Empty scope="block" busy>{words.busy(name)}</Empty>
		{:else}
			<HistoryList
				{events}
				{undoing}
				onundo={(event) => void undoEvent(event)}
				emptyText={words.nothing}
			/>
		{/if}
	</div>
	<!-- Whatever is waiting on this thing, in its own column. It draws nothing at all when nothing
	     is, which is the ordinary case, and it is deliberately OUTSIDE the three states beside it: a
	     read of the thread that failed says nothing about a question waiting on the record, and
	     hiding one behind the other would take it away for a reason that has nothing to do with it.

	     The column is here whether or not it has anything in it, and that is the honest shape: this
	     file cannot know what the snippet will draw, and a track reserved for nothing is invisible
	     where a track arriving late would move the thread sideways as the answer landed. -->
	{#if waiting}
		<div class="beside">{@render waiting()}</div>
	{/if}
</div>

<style>
	/*
	 * Two columns: the thread on the left, whatever is waiting on the right.
	 *
	 * Each column is bounded by the reading measure and not centred (see the head); the token is
	 * the frame's own, so a line here is never longer than on any other reading screen, it just
	 * starts where the page starts.
	 *
	 * `auto-fit` rather than two named tracks and a breakpoint, because the fold is the same
	 * question as the minimum: a track that cannot have its minimum is not laid out, and the second
	 * item wraps underneath. No width is written here to keep in step with the page frame.
	 *
	 * The minimum is half the measure and the maximum is `1fr`. A fixed maximum would be counted at
	 * that maximum, so the second column would appear only on very wide windows; with `1fr` the
	 * count comes from the minimum and the width from what is left. That folds to one column below
	 * roughly 1,000px, the fold this pane wants: a card of two values with a button under each is
	 * not readable under about 500px.
	 *
	 * `min(100%, ...)` stops that minimum being wider than the window on a phone, which a bare
	 * minimum would overflow.
	 */
	.entity-history {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min(100%, calc(var(--page-measure) / 2)), 1fr));
		gap: var(--space-5);
		/* Each column as tall as its own content. Stretched, an empty waiting column would be as tall
		   as fifty rows of thread for nothing, and one short card would grow to fill it. */
		align-items: start;
	}

	/*
	 * The measure is on the COLUMNS and not on the grid, because `1fr` above has no maximum: on a
	 * 32-inch monitor a fraction of the window is a reading line half a metre long.
	 *
	 * And `min-inline-size: 0` because a grid item's automatic minimum size is the widest thing in
	 * it, so one long line in one event would hold the whole column open and the page would scroll
	 * sideways: the same line the tab strip next door carries, for the same reason.
	 */
	.thread,
	.beside {
		min-inline-size: 0;
		max-inline-size: var(--page-measure);
	}
</style>
