<script lang="ts">
	/*
	 * One thing's history, as a panel inside a tab: the asking, the two empty states and the Undo,
	 * around `HistoryList`. Read once per showing; an answer for a subject left behind is dropped.
	 * NOT ON THE GALLERY: it fetches on mount, so an entry would draw whatever the running library
	 * held; HistoryList, HistoryRow, Empty and Problem are there.
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
		/** What kind of thing this is the history of; decides the address and the words. */
		subject: 'person' | 'site' | 'tag' | 'collection' | 'photo_set' | 'song';
		id: string;
		/** What to call them while the answer is on its way. */
		name?: string;
		/** What is waiting on this thing, in a column beside the thread (under it when narrow). */
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
		/* A site records no moment itself, so its empty thread is a real one. */
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

	/* null is not asked yet; an empty list is asked with nothing recorded. */
	let events = $state<HistoryEvent[] | null>(null);
	let failed = $state(false);
	/** The event whose undo is in flight, so only its row spins. */
	let undoing = $state<string | null>(null);

	/** Which thing an answer belongs to. Compared after the await, never before the ask. */
	function whose(subject: Props['subject'], id: string): string {
		return `${subject}:${id}`;
	}

	/** `quietly` keeps the thread up during a re-read after a decision. */
	async function read(quietly = false) {
		const wanted = whose(subject, id);
		// Cleared before asking, or the last subject's events sit under this one's name.
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
			// Only a failure for the subject on screen is shown.
			if (whose(subject, id) === wanted) failed = true;
		}
	}

	// Once per subject.
	$effect(() => {
		if (id) void read();
	});

	/* Read again quietly whenever a write may have moved what it records, here or elsewhere. */
	rereadOnHistoryChange(() => {
		if (id) void read(true);
	});

	/*
	 * Take one event back through whichever door recorded it; a row with no door draws no button.
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
	<!--
	What is waiting, in its own column outside the three states: a failed read hides nothing.
	-->

	{#if waiting}
		<div class="beside">{@render waiting()}</div>
	{/if}
</div>

<style>
	/* Two columns folding to one below about 1,000px: auto-fit with half the measure as the
	   minimum, and min(100%, ...) for a phone. */
	.entity-history {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min(100%, calc(var(--page-measure) / 2)), 1fr));
		gap: var(--space-5);
		/* Each column as tall as its own content. */
		align-items: start;
	}

	/*
	 * The measure on the columns, since 1fr has no maximum; min 0 so a long line cannot widen it.
	 */
	.thread,
	.beside {
		min-inline-size: 0;
		max-inline-size: var(--page-measure);
	}
</style>
