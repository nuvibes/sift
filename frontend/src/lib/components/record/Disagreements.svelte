<script lang="ts">
	/*
	 * Where a stash-box disagrees with this record, on the record itself.
	 *
	 * ## Why it is here and not on a queue
	 *
	 * A queue on the Organize board would be a list of unrelated fields from unrelated records. A
	 * queue is for
	 * judgements a threshold cannot settle, and this is not one of those: it is one record's field
	 * with two answers, and what settles it is looking at the record. Asked away from the person,
	 * "1990 or 1991" is a coin toss; asked on their page, with their name, their other dates and
	 * their files in front of you, it is a question somebody can actually answer.
	 *
	 * There is a second reason, and it is the one that decides the shape rather than the placement:
	 * a FILE whose two sources disagree has nowhere to surface on a queue of fields on a PERSON.
	 * Putting the rows on the record they belong to is what can grow a fourth kind of record later
	 * without needing a new queue for it.
	 *
	 * ## Why it draws nothing at all when there is nothing
	 *
	 * A record with no disagreements is the ordinary case: most records are never linked to a box
	 * at all. A line reading "nothing disagrees" at the top of every person's history would be a
	 * sentence about a feature rather than a fact about the person. So this occupies no space until
	 * it has something to say.
	 *
	 * The panel underneath is the same one the Stash-boxes pane draws, given a subject and an id.
	 * Two copies of "keeping yours writes nothing" is two places for that to stop being true.
	 */
	import ReconcilePanel from '$lib/components/organize/ReconcilePanel.svelte';
	import { waitingText } from '$lib/entity/reconcile.svelte';

	interface Props {
		/** Which kind of record this is, in the server's own word: `person`, `site`, `tag`, `asset`. */
		subject: string;
		/** This record's id. */
		localId: string;
		/**
		 * How many are waiting, passed on as the panel finds them.
		 *
		 * The page uses it for the mark beside the History tab. The strip's own request answers that
		 * number too, and this one OVERWRITES it: it is the fresher of the two while somebody is
		 * looking at this tab, so settling the last row takes the mark down rather than leaving it
		 * standing over an empty panel until the whole strip is asked again. The boxes come with it.
		 */
		onchange?: (waiting: number, boxes: string[]) => void;
		/**
		 * A press wrote a box's value onto this record, or an undo put the old one back. The page
		 * holds its own copy of the record (the name in the header is one of the fields a box can
		 * disagree about) so it reads it again rather than showing the value that was replaced.
		 */
		onwritten?: () => void;
	}

	let { subject, localId, onchange, onwritten }: Props = $props();

	/* How many this record has, as the panel found them. Held here rather than asked separately,
	   because a second reader is a second count free to disagree with the list under it. */
	let waiting = $state(0);
	/* Which boxes disagree, by name, so the line names them ("One field FansDB disagrees with"). */
	let boxes = $state<string[]>([]);
</script>

<div class="disagrees" class:none={waiting === 0}>
	{#if waiting > 0}
		<p class="how-many">
			<!-- The same sentence the mark on the tab says, from the one place it is written. -->
			{waitingText(waiting, boxes)}
		</p>
	{/if}
	<ReconcilePanel
		{subject}
		{localId}
		onchange={(found, named) => {
			waiting = found;
			boxes = named;
			onchange?.(found, named);
		}}
		{onwritten}
	/>
</div>

<style>
	.disagrees {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	/*
	 * Nothing to say, so nothing on screen, not even the gap the flex column above would give it.
	 *
	 * `display: contents` rather than `none`, because the panel inside is still mounted and still
	 * the thing doing the asking: hidden outright it would keep answering into a box nobody can
	 * see, which is fine, but `contents` says plainly that this element is not a box at all while
	 * it has nothing in it. The band around it then spaces its real children as if this were
	 * absent.
	 */
	.none {
		display: contents;
	}

	/* The quiet weight every other counting line in the app uses ("N faces Sift thinks are them",
	   for one), because it is the same kind of fact: something about
	   this record that is waiting on a person. */
	.how-many {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}
</style>
