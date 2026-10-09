<script lang="ts" module>
	import { api } from '$lib/api/client';
	import { isFinished } from '$lib/jobs/queue.svelte';
	import type { JobsPage } from '$lib/jobs/family';
	import { SvelteSet } from 'svelte/reactivity';

	/** How often the queue is asked whether the re-match is still to run. */
	const WATCH_EVERY = 2000;

	/*
	 * The People a Yes in this tab was about, while the re-match it asked for is queued or running.
	 *
	 * A Yes teaches Sift her face, and the re-match then sweeps the rest of the library against it,
	 * so her numbers move for a while after the press. Held outside any one screen so her card and
	 * her page draw the same mark, and cleared once the queue holds no re-match.
	 */
	class Rematching {
		readonly people = new SvelteSet<string>();
		#watching = false;

		/** Mark her until the re-match a Yes about her asked for has run. */
		after(personId: string): void {
			this.people.add(personId);
			if (!this.#watching) void this.#watch();
		}

		async #watch(): Promise<void> {
			this.#watching = true;
			try {
				while (this.people.size > 0) {
					await new Promise((done) => setTimeout(done, WATCH_EVERY));
					if (this.people.size > 0 && !(await stillMatching())) this.people.clear();
				}
			} finally {
				this.#watching = false;
			}
		}
	}

	/* The agreeing a Yes hands to a task, then the re-match that task asks for. A queue that cannot
	   be read is not matching: a mark that never goes is worse than none. */
	async function stillMatching(): Promise<boolean> {
		try {
			for (const type of ['face_agree', 'face_rematch']) {
				const page = await api.get<JobsPage>('/jobs', { query: { type, limit: 5 } });
				if (page.jobs.some((one) => !isFinished(one.state))) return true;
			}
			return false;
		} catch {
			return false;
		}
	}

	export const rematching = new Rematching();

	/** What a Yes to her proposals says: the agreeing is a task, and this is what it was handed. */
	export function agreeingWith(faces: number): string {
		return faces === 1 ? 'Agreeing with 1 face' : `Agreeing with ${faces.toLocaleString()} faces`;
	}

	/** The working mark's words: "Sift is matching the rest of the library against Ada's face". */
	export function matchingSaid(name: string | null | undefined): string {
		const first = name?.trim().split(/\s+/)[0];
		return `Sift is matching the rest of the library against ${first ? `${first}'s` : 'their'} face`;
	}
</script>

<script lang="ts">
	/*
	 * What Sift knows of one person's face, in three numbers: the faces you confirmed, the faces
	 * Sift recognized on its own, and the faces still waiting for an answer.
	 *
	 * They are one reading, drawn together in that order: what teaches Sift (only a confirmed face
	 * does), what Sift has done with it, and what is left to answer. Apart they mislead: "2,400
	 * faces waiting" alone reads as a backlog, and "12 confirmed" beside it shows what it is.
	 *
	 * Three short lines, with the sentences in tooltips. The block sits under the cover in a column
	 * 200 pixels wide, where a sentence under each number would push the tab strip far down the
	 * page. Nothing needed to act is hidden: "300 recognized by Sift" is the whole fact, and the
	 * sentence explains what kind of face that is.
	 *
	 * The three phrases are the same on every screen that reads these states: confirmed,
	 * recognized by Sift, needs your input. What matters to somebody arriving is whether Sift is
	 * asking them anything, and only the third is.
	 *
	 * The one of the three that asks something of the reader is a row with its own Open on the
	 * right, and an optional line under it saying where those faces came from.
	 *
	 * Counts and ways in rather than the faces themselves: Organize's review page for one person
	 * answers the same question with paging and the same three decisions, and two ways to agree to
	 * a face would be two places a fix has to land. So this page says what is there and hands over
	 * to the screens whose job it is.
	 *
	 * It asks for one row, once: the server counts all three over the whole of what this viewer may
	 * see and sends them with any page (see `AppearancePage`), so the smallest page is enough.
	 *
	 * Under the three, a fourth line counts the files a folder's name filed under them whose face
	 * waits in an unnamed group. Those faces carry no name, so none of the three reaches them, and
	 * the line opens the Files wall on exactly those files.
	 *
	 * A zero is absent rather than drawn, as in the bar beside this one: a link to an empty screen
	 * is a dead end, and nothing at all here usually means recognition was never switched on.
	 *
	 * The file's name is narrower than what it draws; the people page and the design gallery import
	 * it by that name.
	 */
	import type { components } from '$lib/api/schema';
	import { identifiedForPerson } from '$lib/people/faces.svelte';
	import { primedOr, readingAbout } from '$lib/entity/subject.svelte';
	import { Spinner, Tooltip } from '$lib/components/common';

	interface Props {
		personId: string;
		/**
		 * Whose faces these are, said in the tooltips ("Faces you confirmed to be Ada Byron" rather than
		 * "are them"). Absent, the pronoun stands in.
		 */
		name?: string;
		/** Bump to re-read, for whatever answers some of these elsewhere on the page. */
		refresh?: number;
		/** One line under the waiting row, saying where those faces came from. Absent draws none. */
		help?: string;
	}

	let { personId, name, refresh = 0, help }: Props = $props();

	/* The words the three tooltips say about this person: their whole name where it is known, and
	   their first name where the sentence is about what Sift learns ("what Ada looks like"). */
	const whom = $derived(name?.trim() || 'them');
	const first = $derived(name?.trim().split(/\s+/)[0] || 'they');
	/* The same name as the object of a sentence ("as Ada"); "them" without one. */
	const asWhom = $derived(name?.trim() ? first : 'them');
	const learnsTip = $derived(
		name?.trim()
			? `Faces you confirmed to be ${whom}. These teach Sift what ${first} looks like.`
			: 'Faces you confirmed to be them. These teach Sift what they look like.'
	);
	const waitingTip = $derived(`Faces Sift thinks may be ${whom}, awaiting your input.`);

	/** The smallest page there is. The total comes with any page; the rows do not get drawn. */
	const ONE = { limit: 1, offset: 0 };

	/* The three numbers, named off the server's own shape rather than written out again. They are
	   exactly the three an identified card carries about one person: the wall of cards and this
	   strip are the same reading, gathered for many people there and asked for one here. */
	type Counts = Pick<components['schemas']['IdentifiedCard'], 'confirmed' | 'matched' | 'waiting'> &
		Pick<components['schemas']['AppearancePage'], 'unnamed_from_folder'>;

	const NOTHING: Counts = { confirmed: 0, matched: 0, waiting: 0, unnamed_from_folder: 0 };

	/* The rule that keeps these on screen while they are re-read is shared. See `readingAbout`,
	   which is where the whole of the reasoning is. */
	const counts = readingAbout<Counts>(
		() => personId,
		async (id) => {
			// Unfiltered, because the three counts are of the whole of what this viewer may see and
			// come back whatever was asked for. `total` is the filtering's own number and is not one
			// of the three: reading it here is how this would quietly become a fourth count.
			const answer = await primedOr('identified', id, () => identifiedForPerson(id, ONE));
			return {
				confirmed: answer.confirmed,
				matched: answer.matched,
				waiting: answer.waiting,
				unnamed_from_folder: answer.unnamed_from_folder ?? 0
			};
		},
		NOTHING,
		() => refresh
	);

	const found = $derived(counts.value);

	/*
	 * Where the two ways in go.
	 *
	 * Held as names at the top rather than written into the markup, because both are addresses this
	 * component does not own: this person's review screen opened on the faces Sift attached on its
	 * own, and the same screen opened on the ones waiting. Both go to THIS person: the wall of
	 * everybody Sift has attached somebody to does not bring one person's card into view, so a
	 * count on a person's page that opened the wall would send the reader looking for them among
	 * hundreds. Written inline they would be two places to edit when a queue is renamed, and the
	 * one that drifts is the one nobody is looking at.
	 */
	const matches = $derived(`/organize/known-people/${encodeURIComponent(personId)}?show=matched`);
	const toCheck = $derived(`/organize/known-people/${encodeURIComponent(personId)}?show=suggested`);
	/* The files a folder's name filed under them whose face waits in an unnamed group: none of the
	   three above reaches those faces, so without this line they are found only by hunting. It opens
	   the Files wall on exactly those files, the filter the server counted them by. */
	const unnamed = $derived(`/browse?${new URLSearchParams({ unnamed_face: personId })}`);
	const unnamedTip = $derived(
		`Files filed under ${whom} from a folder, with a face nobody has named yet.`
	);

	const anything = $derived(
		found.confirmed > 0 || found.matched > 0 || found.waiting > 0 || found.unnamed_from_folder > 0
	);
</script>

{#if anything}
	<div class="counts">
		{#if rematching.people.has(personId)}
			<p class="one working">
				<Tooltip label={matchingSaid(name)} placement="bottom">
					<Spinner size={12} label={matchingSaid(name)} />
				</Tooltip>
			</p>
		{/if}
		{#if found.confirmed > 0}
			<!-- The one of the three with nowhere to go, so it is words rather than a link:
			     agreeing is done on the review screens, and a confirmed face has already been
			     answered. Its sentence is on hover only, where the other two also have it on focus:
			     there is no control here to Tab to, and one invented to carry a tooltip would be a
			     button that does nothing. -->
			<p class="one">
				<Tooltip label={learnsTip} placement="bottom">
					<span class="number">You confirmed {found.confirmed.toLocaleString()} as {asWhom}</span>
				</Tooltip>
			</p>
		{/if}
		{#if found.matched > 0}
			<p class="one">
				<!-- A link, not a button: this goes somewhere rather than doing something, so
				     middle-click, open in a new tab and copy link all work, and none of them do on
				     a button. The act it names is on the card at the other end.

				     The sentence names where to go, not the press: that card leads with whichever
				     act has more faces behind it, so the words of a control can be inside a menu
				     there, and a line that names a control by its words goes wrong the day the
				     control is reworded. -->
				<Tooltip
					label="Faces Sift named without asking. Agree to each one from its card."
					placement="bottom"
				>
					<a class="number" href={matches}
						>Sift recognized {found.matched.toLocaleString()} as {asWhom}</a
					>
				</Tooltip>
			</p>
		{/if}
		{#if found.waiting > 0}
			<!-- The one number that asks something of the reader, so it is a row with its action on
			     the right; the words themselves are the link, as the recognized line's are. -->
			<div class="asks">
				<div class="row">
					<!-- The words' own box, so the row can give them the whole line on a phone: the
					     tooltip's wrapper is the row's item, and a rule on the span inside it reaches
					     nothing the row lays out. -->
					<span class="words">
						<Tooltip label={waitingTip} placement="bottom">
							<!-- The singular is a sentence of its own, not the plural with a 1 in front of it:
							     this is the state a person is one press from empty in. -->
							<!-- The words are the way in, as "Sift recognized N as X" above is: no
							     button beside them. -->
							<a class="number" href={toCheck}>
								{found.waiting === 1
									? '1 awaiting your input'
									: `${found.waiting.toLocaleString()} awaiting your input`}
							</a>
						</Tooltip>
					</span>
				</div>
				{#if help}
					<p class="help">{help}</p>
				{/if}
			</div>
		{/if}
		{#if found.unnamed_from_folder > 0}
			<p class="one">
				<Tooltip label={unnamedTip} placement="bottom">
					<a class="number" href={unnamed}>
						{found.unnamed_from_folder === 1
							? '1 file from the folder with a face still unnamed'
							: `${found.unnamed_from_folder.toLocaleString()} files from the folder with a face still unnamed`}
					</a>
				</Tooltip>
			</p>
		{/if}
	</div>
{/if}

<style>
	/* Three readings stacked, one line each. The column they sit in is the cover's width (see the
	   person page's `.recognition`) so nothing here sets a measure of its own, and the gap is the
	   small one because three short lines are one block rather than three paragraphs. */
	.counts {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}

	.one {
		display: flex;
		margin: 0;
		min-inline-size: 0;
	}

	/* The mark while a re-match about her runs: quieter than the numbers under it. */
	.working {
		color: var(--sift-ink-3);
	}

	/* The number and what it counts, on one line, and it is the loudest thing in the block because
	   it is the answer. Tabular figures so three stacked numbers line up rather than wandering. */
	.number {
		font: var(--text-body);
		font-variant-numeric: tabular-nums;
	}

	/* Colour is said of the SPAN alone, never of `.number`. A class inside a component outranks the
	   bare `a` rule in `app.css`, so a colour written here would take the link colour off the two
	   that are links and leave them looking like the one that is not. */
	span.number {
		color: var(--sift-ink);
	}

	/* The row, then the line saying what opening it does, close under it. */
	.asks {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		min-inline-size: 0;
	}

	/* The words on the left and the Open on the right; in a narrow column the words wrap. The
	   words' box is a row of its own so the tooltip inside it lays out as it does anywhere. */
	.words {
		display: flex;
		min-inline-size: 0;
	}

	/* The words on the left and the Open on the right; in a narrow column the words wrap. */
	.row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-2);
	}

	/* On a phone the column is the cover's 112px, and "6 need your input" beside Open would break
	   into three lines of one or two words. There the words take the line and Open stands under
	   them at the end, where a row's action goes. */
	@media (max-width: 767px) {
		.row {
			flex-wrap: wrap;
			justify-content: flex-end;
		}

		/* The words' box, not the span inside the tooltip: the row lays out its own children, and
		   with the rest of the line empty the row's end-packing would slide the words off the
		   column's start edge by the width they fell short of it. */
		.words {
			flex-basis: 100%;
		}
	}

	.help {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}
</style>
