<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/*
	 * Look a subject up in the stash-boxes, and agree field by field to what comes back.
	 *
	 * One screen for every kind of subject. A person, a site and a tag are linked the same way, and
	 * what the confirm step draws comes from the field registry, the declaration that feeds the
	 * record, the readout and the edit form, so a field added on the server appears in all four
	 * surfaces at once.
	 *
	 * Nothing is ever silently overwritten. Every field is a tick, and a field Sift already has a
	 * value for starts unticked with both values side by side. That is the whole conflict handling
	 * and deliberately not a queue: a queue is for judgements piling up while nobody watches, and
	 * the person who pressed this is here. What they do not tick is not written.
	 *
	 * One way to save, wherever the sheet is opened: the ticked field keys go to the server's take
	 * route, which writes them from what was kept with the link through the subject's own writer
	 * (the one every enrichment and every settled disagreement uses) and records what landed, so
	 * the ledger can say what was filled in. The same from a record page and from the queue of
	 * names nobody could choose for, so the entity link never leaves the page; the page re-reads
	 * its record after `onlinked`.
	 */
	import { untrack } from 'svelte';
	import { exactly, onRecord } from '$lib/shell/when';
	import { api } from '$lib/api/client';
	import {
		Avatar,
		Button,
		Checkbox,
		Empty,
		Modal,
		Pressable,
		Problem,
		TextInput,
		type CheckState
	} from '$lib/components/common';
	import RecordValue from '$lib/components/record/RecordValue.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import SectionHeading from '$lib/components/common/SectionHeading.svelte';
	import { fields, type FieldDescription, type RecordSubject } from '$lib/entity/records.svelte';
	import {
		forgetLink,
		keepPicture,
		link,
		linksOf,
		problemFrom,
		chooserPicture,
		refresh,
		search,
		type LinkSubject,
		type StashBoxLink
	} from '$lib/entity/enrich.svelte';
	import {
		StashBoxes,
		type BoxAnswer,
		type FoundRecord
	} from '$lib/settings-ui/stash-boxes.svelte';

	interface Props {
		open?: boolean;
		/** What kind of thing is being linked. Decides the query and the fields drawn. */
		subject: LinkSubject;
		/** Its id in THIS library. */
		id: string;
		/** Its name here, which is what the search starts from. */
		name: string;
		/** What Sift already holds, by field key: the left-hand side of every row. */
		values: Record<string, unknown>;
		/** Save a draft. The page's own record save, so there is only ever one of those. Left out
		 *  where there is no page: the ticked fields are then taken on the server. See above. */
		/** Told when a link was kept, so the page can redraw the box list under the record. */
		onlinked?: () => void;
	}

	let { open = $bindable(false), subject, id, name, values, onlinked }: Props = $props();

	/* Seeded from the subject's own name ONCE, and re-seeded by `begin` every time the sheet opens.
	 *
	 * `untrack` says that in the code rather than only here: without it Svelte warns that the
	 * initialiser captures the first value only, which is exactly the intent: a box somebody is
	 * typing into must not be rewritten because the page behind it re-fetched. A warning left
	 * standing because it happens to be wrong here is a warning nobody reads next time.
	 */
	let term = $state(untrack(() => name));
	let answers = $state<BoxAnswer[]>([]);
	let looking = $state(false);

	/* Which boxes have had their fuzzy half opened, by box id.
	 *
	 * Per box rather than one flag for the sheet: three services answer separately, and one of them
	 * having a long tail is no reason to expand the other two. Reset with every new search below:
	 * "show the rest" is about the answer on screen, not a standing preference. */
	let opened = $state(new Set<string>());

	/** How many entries a box shows before the rest are folded away. See the note at the list. */
	const FIRST = 3;

	/* Every box that is configured, switched on or not.
	 *
	 * Read so this sheet can say which ones were NOT asked. The search route asks the switched-on
	 * boxes and says nothing about the others, so a library with three boxes and one of them on
	 * would look exactly like a library with one box: "Asking each stash-box in turn", then a single
	 * answer, and nothing anywhere to suggest two more were sitting there with keys already in
	 * them.
	 */
	const boxes = new StashBoxes();
	boxes.follow();
	const asleep = $derived(boxes.items.filter((one) => !one.enabled));
	let asked = $state(false);
	let problem = $state<string | undefined>();

	/** The entry somebody picked, and which box it came from. Null while the chooser is up. */
	let chosen = $state<FoundRecord | null>(null);
	let ticks = $state<Record<string, boolean>>({});
	let saving = $state(false);

	/* Whether to take their PICTURE as well as their fields.
	 *
	 * Its own tick rather than one of the field rows, because it is not a field: it is not on the
	 * record, it has no value to lay beside what is here, and it lands in the picture cache rather
	 * than on the row. Off by default, like every field Sift already has an answer for, and a
	 * picture is one of those far more often than a birth date is.
	 */
	let takePicture = $state(false);

	/* What is already agreed, and the two things that can be done to one.
	 *
	 * They live here rather than on the record itself, deliberately. The record is read on four
	 * surfaces and drawn by one component per value; putting a Forget and an Ask again inside a
	 * value would put two buttons into every one of them. This sheet is where the decision to make
	 * a link was taken, so it is where the decision is taken back.
	 */
	let linked = $state<StashBoxLink[]>([]);
	let busy = $state('');

	async function reload() {
		linked = await linksOf(subject, id);
	}

	async function askAgain(boxId: string) {
		if (busy) return;
		busy = boxId;
		problem = undefined;
		try {
			await refresh(subject, id, boxId);
			await reload();
			onlinked?.();
		} catch (error) {
			problem = problemFrom(error);
		} finally {
			busy = '';
		}
	}

	async function forget(boxId: string) {
		if (busy) return;
		busy = boxId;
		problem = undefined;
		try {
			await forgetLink(subject, id, boxId);
			await reload();
			onlinked?.();
		} catch (error) {
			problem = problemFrom(error);
		} finally {
			busy = '';
		}
	}

	/* The whole thing is reset when it is opened, never when it is closed.
	 *
	 * Closing it while a search is in flight would otherwise wipe the state the answer lands in and
	 * then draw the answer into an empty screen. Opening is the moment there is nothing in flight.
	 *
	 * Driven by an EFFECT on `open` and not by the dialog's `onOpenChange`. That callback reports
	 * changes the library itself made (a press on the overlay, an Escape) and this sheet is
	 * opened by a button on the page setting the bound value, which the library never hears about.
	 * So it would not fire at all: the search box would keep the last thing typed into it, an old
	 * answer would still be on screen, and the list of what is already linked would never be
	 * fetched.
	 *
	 * `untrack` around the call so the effect depends on `open` alone. Everything `begin` touches is
	 * state this component also reads, and an effect that tracked its own writes would re-run itself
	 * for ever.
	 */
	$effect(() => {
		if (open) untrack(begin);
	});

	function begin() {
		term = name;
		answers = [];
		asked = false;
		chosen = null;
		ticks = {};
		problem = undefined;
		linked = [];
		void reload();
		// Which boxes exist, so the answer can say which of them were not asked. Read on open
		// rather than once at module scope: a box switched on in Settings while this page has been
		// sitting there should be reflected the next time somebody opens the sheet.
		void boxes.load();
		/* And ASK, straight away.
		 *
		 * The box arrives with the subject's own name already in it. Waiting for somebody to press
		 * Look up would ask them to confirm a word they did not type, on a sheet they opened by
		 * pressing a verb called Enrich: every press of that verb would be two presses.
		 *
		 * The question is the same one the sheet was opened to ask, so it asks it. Typing over the
		 * name and pressing again is still there for the case the name here is not the name the
		 * boxes file them under, which is the one case the automatic answer cannot help with. */
		void look();
	}

	async function look() {
		const wanted = term.trim();
		if (!wanted || looking) return;
		looking = true;
		problem = undefined;
		// A new question, so nothing is expanded. "Show the rest" is about the answer on screen.
		opened = new Set();
		try {
			// The row this sheet was opened on, so a record kept local is refused before its
			// name goes anywhere. See `search`.
			answers = await search(subject, wanted, id);
			asked = true;
		} catch (error) {
			problem = problemFrom(error);
		} finally {
			looking = false;
		}
	}

	/** Whether Sift already holds something for this field. Decides the tick's starting state. */
	function held(key: string): boolean {
		const one = values[key];
		if (one === null || one === undefined || one === '') return false;
		return !Array.isArray(one) || one.length > 0;
	}

	function pick(found: FoundRecord) {
		chosen = found;
		// Ticked where Sift has nothing, unticked where it has something. A field with a value is a
		// decision somebody already made, and a confirm screen that arrives pre-agreed to replacing
		// it is a confirm screen in name only.
		ticks = Object.fromEntries(offered(found).map((one) => [one.key, !held(one.key)]));
		takePicture = false;
	}

	/** The registry's fields for this subject, filtered to the ones this entry actually offers. */
	function offered(found: FoundRecord): FieldDescription[] {
		return fields
			.of(subject as RecordSubject)
			.filter((one) => one.editable && one.key in found.fields);
	}

	const rows = $derived(chosen ? offered(chosen) : []);

	/* Two lists become one, with what is already here kept first and nothing repeated.
	 *
	 * A list field is a MERGE and not a replacement: taking their aliases must not throw away the
	 * ones somebody typed. Compared without case, because `Jane Doe` and `jane doe` are one alias
	 * and keeping both is how a chooser fills up with the same name twice. */
	function merged(mine: unknown, theirs: unknown): string[] {
		const text = (one: unknown) =>
			typeof one === 'string'
				? one
				: String((one as { alias?: string; url?: string; name?: string })?.alias ?? '') ||
					String((one as { url?: string })?.url ?? '') ||
					String((one as { name?: string })?.name ?? '');
		const out: string[] = [];
		for (const one of [
			...(Array.isArray(mine) ? mine : []),
			...(Array.isArray(theirs) ? theirs : [])
		]) {
			const word = text(one).trim();
			if (word && !out.some((had) => had.toLowerCase() === word.toLowerCase())) out.push(word);
		}
		return out;
	}

	/** What a row draws on the right: their value, or the merged list a tick would produce. */
	function proposed(one: FieldDescription): unknown {
		const theirs = chosen?.fields[one.key];
		return isList(one) ? merged(values[one.key], theirs) : theirs;
	}

	function isList(one: FieldDescription): boolean {
		return one.kind === 'names' || one.kind === 'links';
	}

	async function apply() {
		if (!chosen || saving) return;
		saving = true;
		problem = undefined;
		try {
			// The link first. If this fails nothing has been written to the subject, which is the
			// order that leaves the least behind: a record quietly carrying a stash-box's values
			// with no record of which stash-box said so is the state worth never reaching.
			await link(subject, id, chosen.source_id, chosen.remote_id);
			/* The KEYS and never the values: the server takes each value from what was kept
			   with the link, so nothing this page holds can be written into the record by
			   naming it. Sent when nothing is ticked too: that is written down as a link that
			   filled nothing in, which is a different line from one Sift kept no account of. */
			await api.post(`/stash-boxes/links/${subject}/${id}/${chosen.source_id}/take`, {
				body: { keys: rows.filter((one) => ticks[one.key]).map((one) => one.key) }
			});
			/* The picture last, and never allowed to undo the rest.
			 *
			 * Everything above has been written by this point. A picture that will not come back
			 * (a dead address, a host that answers with a page) must not turn a successful record
			 * save into a failure that leaves somebody unsure what was kept. It is logged nowhere
			 * and simply does not happen; the fields are the work.
			 */
			if (takePicture && chosen.image_url) {
				try {
					await keepPicture(subject, id, chosen.source_id);
				} catch {
					problem = "The fields were saved. Their picture couldn't be downloaded.";
				}
			}
			onlinked?.();
			if (!problem) open = false;
		} catch (error) {
			problem = problemFrom(error);
		} finally {
			saving = false;
		}
	}

	/* Whether pressing the button would do anything. The picture counts: ticking only that is a
	   real intention, and a disabled button beside a ticked box reads as a broken screen. */
	const anyTicked = $derived(rows.some((one) => ticks[one.key]) || takePicture);

	/*
	 * ONE BOX FOR ALL OF THEM, above the rows it acts on.
	 *
	 * A performer this library has never heard of comes back with eight fields filled in and every
	 * one of them starts ticked, which is fine; a performer it already holds comes back with eight
	 * fields it has answers for and every one of them starts UNTICKED, which is right and is eight
	 * presses to undo. The whole point of this sheet is that nothing is taken without somebody
	 * saying so, and saying it once is still saying it: the values are all on screen, beside what
	 * would be replaced, when the box is pressed.
	 *
	 * It is DERIVED from the rows and never held, so it cannot go out of step with them: tick the
	 * last row by hand and this becomes `on` with nothing told to do so. `partly` is the shared
	 * box's own fourth state and it is exactly this case (some of them) rather than `out`,
	 * which is a refusal and is painted red.
	 *
	 * The picture is NOT one of these. It is not a field, it is not in `rows`, and it lands in a
	 * different table; a box called "take all the fields" reaching across to fetch a portrait would
	 * be the one surprise this screen is written to avoid.
	 */
	const takeAll = $derived<CheckState>(
		rows.length === 0 || !rows.every((one) => ticks[one.key])
			? rows.some((one) => ticks[one.key])
				? 'partly'
				: 'off'
			: 'on'
	);

	/* On means every row, and anything else means none of them.
	 *
	 * The shared box cycles off, on, out, off, so pressing a ticked one reports `out`, which is
	 * not `on` and clears the lot. That is the reading the field rows below already take of the
	 * same control, and it makes the third state unreachable here exactly as it is there. */
	function takeEvery(next: CheckState) {
		const wanted = next === 'on';
		ticks = Object.fromEntries(rows.map((one) => [one.key, wanted]));
	}
</script>

<Modal
	bind:open
	title="Look up in a stash-box"
	description={chosen
		? `Tick a field to take ${chosen.source_name}'s answer for it. Anything left unticked stays` +
			' exactly as it is here.'
		: "Search the stash-boxes you have switched on, and pick the entry that's really this one."}
	sheetClass="stash-look"
>
	{#snippet children()}
		{#if !chosen}
			{#if linked.length > 0}
				<!-- What has already been agreed, above the search box: it answers "have I done this
				     already", which is the first question somebody opening this sheet has. -->
				<ul class="linked" aria-label="Already linked">
					{#each linked as one (one.box_id)}
						<li>
							<span class="who">
								<span class="name">{one.box_name}</span>
								<Tooltip label={exactly(one.fetched_at)}>
									<span class="quiet"
										>{one.record.name}
										{'\u00b7'} asked {onRecord(one.fetched_at, {
											inline: true
										})}</span
									>
								</Tooltip>
							</span>
							<Button
								type="button"
								size="small"
								busy={busy === one.box_id}
								onclick={() => askAgain(one.box_id)}>Ask again</Button
							>
							<Button
								type="button"
								size="small"
								tone="ghost"
								icon="close"
								disabled={busy === one.box_id}
								onclick={() => forget(one.box_id)}>Remove</Button
							>
						</li>
					{/each}
				</ul>
			{/if}

			<form
				class="asking"
				onsubmit={(event) => {
					event.preventDefault();
					void look();
				}}
			>
				<TextInput
					value={term}
					oninput={(event) => (term = event.currentTarget.value)}
					aria-label="A name"
					autocomplete="off"
				/>
				<Button type="submit" tone="primary" busy={looking}>Look up</Button>
			</form>

			{#if looking}
				<Empty scope="block" busy>Asking each switched-on stash-box in turn.</Empty>
			{:else if asked}
				<!--
							The boxes that were NOT asked, named.

							Without this a library with three boxes and one of them switched on reads
							exactly like a library with one box: the sheet says it asked each of them,
							one answer comes back, and nothing anywhere suggests the other two are
							sitting there with keys already in them. Somebody then concludes the search
							is broken, or that the person genuinely is not on StashDB.

							Under the answers rather than over them: it is a footnote about the question,
							and the answers are what was asked for.
						-->
				<ul class="answers">
					{#each answers as answer (answer.box_id)}
						<li>
							<SectionHeading band>{answer.box_name}</SectionHeading>
							{#if answer.problem}
								<Problem message={answer.problem} />
							{:else if answer.records.length === 0}
								<Empty scope="block">Nothing by that name.</Empty>
							{:else}
								<!--
											Three from each box, and the rest folded away.

											A stash-box's search completes a word somebody is typing, so it
											matches on ANY of them: two words come back as ten entries of
											which one is the person and nine share a first name or a surname.
											The ones matching EVERY word come first and are what the three are
											taken from; where none does, the box's own order stands.

											Three rather than "all the close ones", because a common name
											matches every word ten times over and a chooser is for choosing
											between a few. Folded rather than dropped, because the fuzziness
											is sometimes doing its job: somebody filed under a spelling
											nobody here would type comes back only that way, and dropping it
											would make them unreachable through Sift while the service itself
											finds them.
										-->
								{@const close = answer.records.filter((one) => one.every_word)}
								{@const best = (close.length > 0 ? close : answer.records).slice(0, FIRST)}
								{@const showing = opened.has(answer.box_id)}
								{@const more = answer.records.length - best.length}
								<ul class="found">
									{#each showing ? answer.records : best as found (found.remote_id)}
										<!-- The pack's logo first where Sift ships one for this site, the box's own
										     picture behind it. See `chooserPicture`: a mark already on this disk beats a
										     trip off the machine per row of the list. -->
										{@const drawn = chooserPicture(found)}
										<li>
											<!-- The app's own pressable surface, not a bare button: a row in a list takes the
											     `wash` feedback rather than a lift, because scaling one row shoves the rest of
											     the list around. -->
											<Pressable feedback="wash" class="candidate" onclick={() => pick(found)}>
												<!--
															A row of this file's own, INSIDE the pressable rather than the
															pressable itself.

															The obvious version puts `display: flex` on `.candidate` and it
															does not work: Svelte scopes the shared component's own rule to
															`.pressable.svelte-xxxx`, which is two classes, and a `:global`
															rule naming one class loses the specificity contest silently.
															The result would be a picture, a name and a count stacked in a
															column: invisible for as long as the portrait was drawing at
															the full width of the sheet and hiding all three.

															An element declared here is styled by a scoped rule with nothing
															to argue with, and it does not depend on what the pressable
															happens to call itself.
														-->
												<span class="row">
													<Avatar
														src={drawn.src}
														instead={drawn.instead}
														name={found.name}
														decorative
													/>
													<span class="who">
														<span class="name">{found.name}</span>
														{#if found.disambiguation}
															<span class="quiet">{found.disambiguation}</span>
														{/if}
													</span>
													{#if found.file_count !== null}
														<span class="quiet">{counted(found.file_count)} files</span>
													{/if}
												</span>
											</Pressable>
										</li>
									{/each}
								</ul>
								{#if more > 0 && !showing}
									<Button
										type="button"
										tone="ghost"
										size="small"
										onclick={() => (opened = new Set([...opened, answer.box_id]))}
									>
										Show {counted(more)} more {more === 1 ? 'answer' : 'answers'} from this box
									</Button>
								{/if}
							{/if}
						</li>
					{/each}
				</ul>
				{#if asleep.length > 0}
					<p class="quiet skipped">
						{asleep.map((one) => one.name).join(', ')}
						{asleep.length === 1 ? 'was' : 'were'} not asked &mdash; switched off under Settings, Stash-boxes.
					</p>
				{/if}
			{/if}
		{:else}
			<p class="picked">
				Keeping <strong>{chosen.name}</strong> from <strong>{chosen.source_name}</strong>.
			</p>

			<!--
						Their picture, offered where it already is.

						The chooser has just drawn it, so "use that one" is the obvious next sentence,
						and without this the only picture an entity could have would be a still out
						of a file already in the library.

						Above the fields rather than among them, because it is not one. It has no
						value to lay against what is here, and it lands in the picture cache rather
						than on the record's row, which is also what makes it unable to overwrite a
						cover somebody chose. Two different fields, in two different tables.
					-->
			{#if chosen.image_url}
				<div class="picture">
					<Checkbox
						state={takePicture ? 'on' : 'off'}
						onchange={(next) => (takePicture = next === 'on')}
						label="Also use their picture from {chosen.source_name}"
					/>
					<Avatar src={chosen.image_url} name={chosen.name} decorative />
					<!--
								Said in words as well as shown.

								A checkbox and a portrait and nothing else, with the sentence naming what
								it does only in the box's accessible label, would be a bare tick beside
								a picture on screen, which reads as part of the field list under it
								rather than as an offer. What a control does belongs
								where somebody can read it.
							-->
					<span class="offer">Also use their picture from {chosen.source_name}</span>
				</div>
			{/if}

			{#if rows.length === 0}
				<Empty scope="block">That entry carries nothing Sift has a field for.</Empty>
			{:else}
				<!--
					Which column is which, said once, and the RIGHT one says what ticking does.

					Headings like "Here now" and "FansDB says" would describe two values and neither
					say what the tick is for, and a tick on the left, beside the column holding what
					Sift already has, reads straight down as a list of boxes next to a column of
					dashes: "keep the dashes". It is the opposite: a tick TAKES the value on the right.

					So the box is in the column it acts on, and its heading says the verb.

					`aria-hidden` because each row still names its own two values through the
					checkbox's label, and a screen reader reading a floating pair of column headings
					mid-list gains nothing.
				-->
				<!--
					Above the headings rather than inside them, because it is not a column caption: it
					acts on every row under it and the headings say what the columns are. Its own
					sentence is drawn beside it for the reason the picture offer's is: a bare tick
					with its words only in an accessible label is a control nobody can read.
				-->
				<div class="take-all">
					<Checkbox
						state={takeAll}
						onchange={takeEvery}
						label="Take all from {chosen.source_name}"
					/>
					<span class="offer">Take all from {chosen.source_name}</span>
				</div>

				<div class="heads" aria-hidden="true">
					<span class="head-name">Field</span>
					<span>Here now</span>
					<span class="head-take">Take from {chosen.source_name}</span>
				</div>
				<ul class="rows">
					{#each rows as one (one.key)}
						<li class:conflict={held(one.key)}>
							<!--
								The field's NAME, drawn.

								Carried only inside the checkbox's label, where nothing but a screen reader
								reaches it, every row on this sheet would read "Here" against the box's
								name, one after another, with no way to tell a height from a hair colour.
							-->
							<span class="what">{one.label}</span>

							<div class="mine">
								<RecordValue kind={one.kind} value={values[one.key]} />
							</div>

							<!--
								Their value and the tick that takes it, in one cell.

								Together rather than in two columns, because they are one decision: this is
								the value, and this is whether to have it. Apart, the tick would belong
								to whichever column it was nearer, and could look like a vote for the blanks
								on the other side.
							-->
							<div class="theirs">
								<!--
									The shared box, driven from a plain yes/no.

									It carries a third state (`out`, meaning "deliberately refused") for
									the filter lists it was written for. There is no third answer to "keep
									this field", so the state is DERIVED from a boolean and only `on` is read
									back: pressing a ticked row reports `out`, which is not `on`, so it
									becomes false and redraws as `off`. The cycle is a toggle from the
									outside and the third state is unreachable, which is what it should be
									here.
								-->
								<Checkbox
									state={ticks[one.key] ? 'on' : 'off'}
									onchange={(next) => (ticks[one.key] = next === 'on')}
									label={held(one.key)
										? `Replace ${one.label} with what ${chosen.source_name} says`
										: `Fill in ${one.label} from ${chosen.source_name}`}
								/>
								<div class="value">
									<RecordValue kind={one.kind} value={proposed(one)} />
								</div>
							</div>
						</li>
					{/each}
				</ul>
			{/if}
		{/if}
	{/snippet}

	<!-- Out of the scroll. What to press next stays put while the record above it moves, which on
	     the confirm step is the difference between a list you read and a list you get lost in. -->
	{#snippet footer()}
		<Problem message={problem} />

		<div class="buttons">
			{#if chosen}
				<Button type="button" onclick={() => (chosen = null)} disabled={saving}>Back</Button>
				<Button type="button" tone="primary" busy={saving} disabled={!anyTicked} onclick={apply}>
					Take what is ticked
				</Button>
			{:else}
				<Button type="button" onclick={() => (open = false)}>Close</Button>
			{/if}
		</div>
	{/snippet}
</Modal>

<style>
	/*
	 * Wider than an ordinary sheet, because the confirm step lays two values side by side, and two
	 * columns in 420 pixels are two columns of one word each. The height is not this file's
	 * business: every sheet is capped and scrolls its own body through the shared component.
	 */
	:global(.stash-look) {
		/* The width only. `.sheet` clamps it to the window. See app.css. */
		--sheet-inline: 46rem;
	}

	/* What is already agreed, above the search box. */
	.linked {
		list-style: none;
		margin: var(--space-4) 0 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}

	.linked li {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		padding: var(--space-2);
		border: 1px solid var(--sift-line);
		border-radius: var(--radius-md);
	}

	.linked .who {
		display: flex;
		flex-direction: column;
		flex: 1;
		min-inline-size: 0;
	}

	.asking {
		display: flex;
		gap: var(--space-2);
		margin-block: var(--space-4) var(--space-3);
	}

	/* `:global`, because the box is `TextInput`'s own element, compiled in that file's scope. */
	.asking :global(.text-input) {
		flex: 1;
		min-inline-size: 0;
	}

	/* The footnote about what was not asked, set off from the last answer above it. */
	/* The picture offer, set apart from the field rows under it: it is a different kind of thing
	   and reads as a first row of the list otherwise. */
	.picture {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		margin-block-end: var(--space-3);
		padding-block-end: var(--space-3);
		border-block-end: 1px solid var(--sift-line);
	}

	/* Sized here for the reason the candidate row's is: `Avatar` has no width of its own and takes
	   whatever it is given, which as a flex child with nothing said is all of it. */
	.picture :global(.avatar) {
		flex: none;
		inline-size: 5.5rem;
		border-radius: var(--radius-sm);
	}

	.offer {
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	/* The box that ticks every row, on its own line above them. Laid out like the picture offer
	   because it is the same kind of thing (a tick and the sentence that says what it does) and
	   two rows a person reads the same way should not be built two ways. */
	.take-all {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		margin-block-end: var(--space-2);
	}

	.skipped {
		margin-block-start: var(--space-3);
		padding-block-start: var(--space-3);
		border-block-start: 1px solid var(--sift-line);
	}

	.answers,
	.found,
	.rows {
		list-style: none;
		margin: 0;
		padding: 0;
	}

	.answers > li + li {
		margin-block-start: var(--space-4);
	}

	/* The box's name, then its answers: the band draws no margin, the list item owns the gap. */
	.answers > li {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.found > li + li {
		margin-block-start: var(--space-1);
	}

	/* `:global` INSIDE a scoped parent. `:global` because the element wearing this class is the
	   shared pressable's rather than this file's, so a plain rule aimed at it matches nothing at
	   all; the scoped `.found` in front of it because the component's own rule is two classes and a
	   bare one-class `:global` loses to it silently, which is why the row's layout is on an
	   element declared here. */
	.found :global(.candidate) {
		inline-size: 100%;
		padding: var(--space-2);
		text-align: start;
	}

	/* The row itself. See the note where it is written for why the layout is here and not on
	   `.candidate`. */
	.row {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		inline-size: 100%;
	}

	/* The candidate's portrait, at the size of a row rather than the size of the sheet.
	 *
	 * `Avatar` is `display: block` with no width of its own: it takes whatever its container
	 * gives it and derives its height from an aspect ratio. As a flex child with nothing said about
	 * it that would come out as the full width of the sheet and about 450 pixels tall, so ONE
	 * candidate would fill the chooser and the rest would be below the fold, like a picture viewer.
	 *
	 * `flex: none` as well as a width, or the row's own `min-inline-size: 0` lets it be squeezed by
	 * a long name beside it. `:global` for the reason above: the element is the component's.
	 */
	/* Big enough to tell two people apart, which is the entire job of this row.
	 *
	 * Not the 2.75rem of an avatar beside a name in a list, because this is not that. Three
	 * entries come back for a common name and the way to know which is the right one is to LOOK at
	 * them; at forty-four pixels a face is a smudge and the count beside it does all the work.
	 * Portrait, because a stash-box's picture of a person is. */
	.row :global(.avatar) {
		flex: none;
		inline-size: 5.5rem;
		border-radius: var(--radius-sm);
	}

	.who {
		display: flex;
		flex-direction: column;
		flex: 1;
		min-inline-size: 0;
	}

	.name {
		font: var(--text-body);
		color: var(--sift-ink);
	}

	.picked {
		margin: var(--space-4) 0 var(--space-3);
		font: var(--text-body);
		color: var(--sift-ink-2);
	}

	.rows > li {
		padding: var(--space-3) 0;
		border-block-start: 1px solid var(--sift-line);
	}

	/* A row where both sides have something. Marked, because it is the only kind of row where
	   ticking the box throws something away, and it is the one worth reading twice. */
	.rows > li.conflict .what {
		color: var(--sift-accent-text);
	}

	/* One grid for the headings and for every row, declared once through a custom property so the
	   captions cannot drift out of line with the columns they name, which two separate `auto-fit`
	   declarations would quietly do. */
	.heads,
	.rows > li {
		display: grid;
		grid-template-columns: minmax(7rem, 1fr) minmax(8rem, 1.2fr) minmax(10rem, 1.4fr);
		gap: var(--space-3);
		align-items: start;
	}

	.heads {
		padding-block-end: var(--space-2);
		font: var(--text-micro);
		color: var(--sift-ink-3);
	}

	/* The column that says what pressing a box does. Marked, because it is the only heading here
	   that describes an ACTION rather than a value, and the confusion it prevents is not knowing
	   which way the tick points. */
	.head-take {
		color: var(--sift-ink-2);
	}

	.head-name {
		color: var(--sift-ink-3);
	}

	/* The field's name. The thing that differs row to row, so it is the thing that leads one. */
	.what {
		display: block;
		font: var(--text-label);
		color: var(--sift-ink-2);
	}

	/* What Sift holds. Quieter than the column beside it: this is the thing being left alone unless
	   somebody says otherwise. */
	.mine {
		color: var(--sift-ink-3);
	}

	/* Their value and the tick that takes it, on one line, in one cell. */
	.theirs {
		display: flex;
		align-items: start;
		gap: var(--space-2);
	}

	.theirs .value {
		min-inline-size: 0;
		flex: 1;
	}

	.buttons {
		margin-block-start: var(--space-5);
	}
</style>
