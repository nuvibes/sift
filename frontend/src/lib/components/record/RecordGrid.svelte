<script lang="ts">
	/*
	 * A file's record, edited where it is read.
	 *
	 * The entity pages edit a record as a column of fields under a heading, which suits a page that
	 * is a column. A file's record is a grid of short facts under the picture, and swapping it for
	 * a tall column on Edit would grow the panel, move everything below, and move the value being
	 * corrected away from where somebody was looking.
	 *
	 * Nothing moves. The grid is the same in both modes: the same columns, order and cell for each
	 * field. A cell's value swaps for a control occupying the same box, and every cell reserves a
	 * control's height whether drawing one or not, so the page under it is identical before and
	 * after the press. The Save and Cancel row is the only addition, at the end, below everything.
	 *
	 * The two layouts share their editor. Two shapes is a decision, but two copies of "a date is a
	 * date control, a list is boxes with an adder, a nationality is chosen and never typed" would
	 * come apart, so both draw `FieldEditor`, and a kind added to the registry appears on both.
	 */
	import { tick, untrack, type Snippet } from 'svelte';
	import { Button, EditMarks, Problem } from '$lib/components/common';
	import FieldEditor from '$lib/components/record/FieldEditor.svelte';
	import RecordValue from '$lib/components/record/RecordValue.svelte';
	import {
		appliesToKind,
		fields,
		filledIn,
		inGroup,
		linkFor,
		saveProblem,
		type FieldDescription,
		type FieldGroup,
		type RecordSubject
	} from '$lib/entity/records.svelte';

	interface Props {
		subject: RecordSubject;
		/** The stored values, by field key. Copied into a draft while editing; never written to. */
		values: Record<string, unknown>;
		/** Whether the cells are boxes. The page owns this, because the button that sets it is the
		 *  page's. See the note about the button never moving. */
		editing?: boolean;
		/** Save the draft: every field from the whole form, only the one field from its own tick.
		 *  Anything that throws leaves the boxes up with what was typed still in them. */
		onsave?: (draft: Record<string, unknown>) => Promise<void>;
		oncancel?: () => void;
		/** Names the record for a screen reader: whose record this is. */
		label: string;
		/**
		 * The form's own id, so Save and Cancel can be drawn OUTSIDE it.
		 *
		 * `<button form="...">` is how a submit button reaches a form it is not inside, and it is
		 * what lets the page put Save exactly where Edit was rather than at the far end of the
		 * record. The alternative was lifting the whole draft into the page, which would put the
		 * record's state in two places.
		 */
		formId?: string;
		/**
		 * Draw only the fields that have something in them, while the record is being READ.
		 *
		 * ## Why this is not the rule everywhere
		 *
		 * `RecordView` draws every field with a dash against the empty ones, and the reasoning there
		 * is good: a record that hides what it has not got changes shape as somebody fills it in, and
		 * there is nowhere to see what COULD be filled in.
		 *
		 * A file's record is the case that reasoning does not cover. It is a grid of thirty-odd
		 * machine facts standing in a capped panel beside the lookalikes, and most files answer a
		 * handful of them, so the panel would be mostly dashes, with the two or three facts worth
		 * reading somewhere among them. The answer to "what could be filled in" is Edit, which is one
		 * press away and is exactly where that question is being asked.
		 *
		 * So it is only ever true while the cells are values. In EDIT mode every field is drawn
		 * whatever it holds, because a field somebody cannot see is a field they cannot fill.
		 *
		 * The price is named rather than hidden: with this on, pressing Edit grows the record by
		 * however many fields were empty, which is the one thing the "nothing moves" note above was
		 * written to prevent. It is paid knowingly (the alternative is a form that can only edit
		 * what is already filled in, which is a form that cannot be used to fill anything in) and
		 * the caller lifts its own height cap for the same reason at the same moment.
		 */
		onlyFilled?: boolean;
		/**
		 * Which HALF of the record to draw: what somebody wrote, or what the file measures.
		 *
		 * Absent draws both, which is every record but a file's: a person has one half and asking
		 * a page to name it would be asking it to know about a split that does not apply to it.
		 *
		 * The split itself is the REGISTRY's (`group` on the field) and never a list here. A list in
		 * this file is a list somebody has to remember to add to, and the field they forget lands in
		 * whichever half the list happened to put it, silently.
		 */
		group?: FieldGroup;
		/**
		 * What to do when an empty field is pressed, where an empty field should invite one.
		 *
		 * Given, a field with nothing in it draws "Add" in READ mode instead of a dash, and pressing
		 * it hands back the key: the caller turns the record into boxes and says which one to
		 * focus. That is the whole point of drawing the empty ones at all: what can be filled in is
		 * visible without pressing Edit first and then hunting for the row.
		 *
		 * Absent, an empty field draws its dash. `onlyFilled` and this one are
		 * opposite answers to the same question and the caller picks one: hiding a blank row is
		 * right where the field is a machine fact nothing will ever fill.
		 */
		onadd?: (key: string) => void;
		/**
		 * Which field's box to put the cursor in when the cells become boxes.
		 *
		 * Set by the caller at the same moment it sets `editing`, so the press that opened the form
		 * lands on the row it was made from. Read once per opening. See the effect below.
		 */
		focusField?: string | null;
		/**
		 * What KIND of file this record belongs to, where it belongs to a file at all.
		 *
		 * A still's measured half carries facts that are true of the stream ffprobe read and false
		 * of the photograph. See `appliesToKind`, which owns that rule. Absent here means "not a
		 * file", which is every other record, and every field applies.
		 */
		mediaType?: string | null;
		/**
		 * A line a caller draws UNDER one field's value, while the record is read.
		 *
		 * Called for every field and drawing whatever the caller decides for that one: usually
		 * nothing. It exists for a fact ABOUT a value that the value cannot carry: where a file's
		 * song name came from (`MusicSource`) sits directly under the Music it explains rather than
		 * somewhere below the record, where it would read as a note about the whole file.
		 *
		 * Not drawn in edit mode: the cell is a box then, and a sentence about the stored value under
		 * a box holding a different one would be describing something that is no longer on screen.
		 */
		under?: Snippet<[FieldDescription]>;
	}

	let {
		subject,
		values,
		editing = false,
		onsave,
		oncancel,
		label,
		formId,
		onlyFilled = false,
		group,
		onadd,
		focusField = null,
		mediaType = null,
		under
	}: Props = $props();

	const uid = $props.id();

	/*
	 * `filledIn` is the registry module's, not this file's: the panel above counts the same thing
	 * (the number on a tab is how many of that half's fields are filled), and a second definition
	 * of "filled" would be a tab saying three over a pane showing two.
	 */

	const all = $derived(
		fields
			.drawn(subject)
			.filter((one) => group === undefined || inGroup(one, group))
			.filter((one) => appliesToKind(one, mediaType))
			.filter((one) => !onlyFilled || editing || filledIn(values[one.key]))
	);

	/* Short facts first, then the wide ones, and this ordering is what actually keeps the page
	 * still, rather than the reserved cell heights alone.
	 *
	 * A one-line value and a one-line box can be made the same height, and are. A PARAGRAPH cannot:
	 * a dash is one line and the box that edits it is four, so a cell holding one grows by three
	 * lines on the press and would carry every row under it down the page. The same is true of a list,
	 * whose editor is an adder row plus a box per entry.
	 *
	 * So they go last, across the full width, below everything that is a short fact. Whatever they
	 * do to their own height they do to the bottom of the record, where the only thing under them
	 * is the Save row, and nothing somebody was looking at moves.
	 *
	 * It reads better too, which is the usual sign: a description and a list of addresses are not
	 * the same kind of thing as a frame rate and do not belong in a column beside one.
	 */
	const WIDE = ['paragraph', 'names', 'links', 'tags', 'accounts', 'sources'];
	const shown = $derived(all.filter((one) => !WIDE.includes(one.kind)));

	/* The wide half, with the PARAGRAPH last.
	 *
	 * They sit on the same grid as the facts above, one per column, so the record stays one shape
	 * rather than a grid with a stack bolted underneath. Which column each lands in follows from
	 * the order, and the order is: everything else, then prose.
	 *
	 * That is not arbitrary. A paragraph is the one thing here with no bound on its height, so it
	 * goes in the LAST cell: the bottom of the record, with nothing beside it that its growing
	 * can push and nothing under it but the Save row.
	 */
	const wide = $derived(
		all
			.filter((one) => WIDE.includes(one.kind))
			.toSorted((a, b) => Number(a.kind === 'paragraph') - Number(b.kind === 'paragraph'))
	);

	let busy = $state(false);
	let problem = $state<string | undefined>();

	/* The draft, seeded when editing STARTS and never re-seeded while it is open.
	 *
	 * The page re-fetches while this is up (a library change, a tag written elsewhere) and a
	 * draft that followed those would throw away whatever somebody had half typed. It is seeded off
	 * `editing` turning true rather than off `values` changing, which is the same rule the form
	 * follows and the reason `untrack` is here: capturing the first value only is the intent.
	 */
	let draft = $state<Record<string, unknown>>({});
	let open = false;
	$effect(() => {
		if (editing && !open) {
			open = true;
			draft = untrack(() => ({ ...values }));
			problem = undefined;
		} else if (!editing) {
			open = false;
		}
	});

	/*
	 * The box the press asked for, focused once the cells have become boxes.
	 *
	 * The press that opens the form is on a particular ROW ("Add" against Details) so the cursor
	 * belongs in that row's box. Without it somebody presses Add on the third field and the form
	 * opens with nothing focused, which is the same number of presses as pressing Edit and worse,
	 * because they were promised a field.
	 *
	 * Keyed on `editing` rather than on `focusField`, so it happens once per opening: the caller
	 * leaves the key set while the form is up, and re-focusing on every keystroke would take the
	 * cursor back from wherever somebody moved it to.
	 */
	let focused = false;
	$effect(() => {
		if (!editing) {
			focused = false;
			return;
		}
		if (focused) return;
		focused = true;
		const key = untrack(() => focusField);
		if (!key) return;
		const box = document.getElementById(`${uid}-${key}-box`);
		// Not every field has one: a machine fact is drawn as a value in both modes, and a press on
		// a row that cannot be typed into leaves the form open with the cursor where it was.
		if (box instanceof HTMLElement) box.focus();
	});

	/* The draft as it should be STORED, rather than as it was typed.
	 *
	 * Every list entry is a box, so an entry can be emptied, and an empty one is not a value
	 * somebody wanted, it is a row they were finished with. Blanks are dropped and the rest trimmed.
	 */
	function cleaned(held: Record<string, unknown>): Record<string, unknown> {
		const out: Record<string, unknown> = { ...held };
		for (const one of all) {
			if (one.kind !== 'names' && one.kind !== 'links') continue;
			// A field that is not being saved stays absent, so a one-field save sends one field.
			if (!(one.key in out)) continue;
			// Both halves of the record, since `shown` is only the short ones.
			const list = Array.isArray(out[one.key]) ? (out[one.key] as unknown[]) : [];
			out[one.key] = list.map((entry) => String(entry ?? '').trim()).filter(Boolean);
		}
		return out;
	}

	/*
	 * ONE FIELD, edited where it stands: what "Add" opens on an empty row.
	 *
	 * Opening the whole record as boxes for Add against one field would be a form of thirty fields
	 * for somebody who asked to fill in one. So it opens that field's box alone, with
	 * a cross and a tick inside it (`EditMarks`), and the tick saves that one field and nothing else:
	 * sending the rest of the record as it was loaded would write back values another window may
	 * have changed since. The whole form is still Edit's, on the heading.
	 *
	 * Only where the grid can save (`onsave`); a grid that cannot hands the press to `onadd`.
	 */
	let alone = $state<string | null>(null);
	let aloneValue = $state<unknown>(null);

	async function openAlone(key: string) {
		alone = key;
		aloneValue = values[key] ?? null;
		problem = undefined;
		await tick();
		const box = document.getElementById(`${uid}-${key}-box`);
		if (box instanceof HTMLElement) box.focus();
	}

	function closeAlone() {
		alone = null;
		problem = undefined;
	}

	async function keepAlone() {
		if (busy || !onsave || alone === null) return;
		busy = true;
		problem = undefined;
		try {
			await onsave(cleaned({ [alone]: $state.snapshot(aloneValue) }));
			alone = null;
		} catch (failure) {
			problem = saveProblem(failure);
		} finally {
			busy = false;
		}
	}

	/* Enter keeps and Escape leaves it, as in the saved filters' rename. Not Enter in a paragraph,
	   where it is a new line, nor in a list, where it is the adder's own. */
	function aloneKeys(event: KeyboardEvent, one: FieldDescription) {
		if (event.key === 'Escape') {
			event.preventDefault();
			event.stopPropagation();
			closeAlone();
		} else if (event.key === 'Enter' && !WIDE.includes(one.kind)) {
			event.preventDefault();
			void keepAlone();
		}
	}

	async function save(event: SubmitEvent) {
		event.preventDefault();
		// Only the whole form submits. One field open alone keeps through its own tick.
		if (busy || !onsave || !editing) return;
		busy = true;
		problem = undefined;
		try {
			await onsave(cleaned($state.snapshot(draft)));
		} catch (failure) {
			// One flat sentence for anything that went wrong, the caller's own words where it
			// wrapped the failure to say it was written for the reader, and a route's refusal in
			// words (a 422 carrying a sentence) as itself. See `saveProblem`. A file's
			// Filename renames it on DISK, and the organize verb refuses that for reasons that are
			// about what was typed: those sentences have to arrive as themselves.
			problem = saveProblem(failure);
		} finally {
			busy = false;
		}
	}
</script>

<!--
	ONE CELL'S CONTENTS, written once and rendered from both halves of the grid.

	The short facts and the wide ones are two `dl`s on purpose (see the note above `WIDE`) but
	what goes IN a cell is the same question in both, so it is answered once. With the empty-field
	prompt it is three branches, and three branches kept in step by hand in two places is the drift
	this file's own header warns about.
-->
{#snippet cell(one: FieldDescription)}
	{#if editing && one.editable}
		<FieldEditor
			field={one}
			id="{uid}-{one.key}-box"
			value={draft[one.key]}
			onchange={(next) => (draft[one.key] = next)}
		/>
	{:else if !editing && alone === one.key}
		<!-- svelte-ignore a11y_no_static_element_interactions: the keys are the box's own, caught on the way out -->
		<div class="alone" onkeydown={(event) => aloneKeys(event, one)}>
			<EditMarks
				wide={WIDE.includes(one.kind)}
				{busy}
				oncancel={closeAlone}
				onkeep={() => void keepAlone()}
				cancelLabel="Keep what was in {one.label}"
				keepLabel="Save {one.label}"
			>
				<FieldEditor
					field={one}
					id="{uid}-{one.key}-box"
					value={aloneValue}
					onchange={(next) => (aloneValue = next)}
				/>
			</EditMarks>
		</div>
	{:else if onadd && one.editable && !filledIn(values[one.key])}
		<!--
			AN INVITATION WHERE THE VALUE WOULD BE, for a record whose empty rows are drawn.

			A dash says "the field exists and nobody has filled it", which is right on a record read
			as reference and wrong on the half somebody came to write: the point of drawing the empty
			rows at all is that what can be filled in is visible without pressing Edit first. Pressing
			one opens the form on that row.

			`link` rather than an accent word: a pressable word inside prose is
			this app's `link` tone, which is underlined before anybody points at it: the accent
			would have meant a hand-dressed button in a cell, and a colour carrying the whole message.

			The word alone on screen and the field's name in the announcement, so a screen reader
			hears "Add Details" rather than the fifth "Add" in a grid of them.
		-->
		<Button
			tone="link"
			aria-label="Add {one.label}"
			onclick={() => (onsave ? void openAlone(one.key) : onadd(one.key))}>Add</Button
		>
	{:else}
		<!-- A field the server says is not editable is drawn as it reads, in both modes. A box
		     around a file's size invites an edit nothing can honour. -->
		<RecordValue
			kind={one.kind}
			value={editing ? draft[one.key] : values[one.key]}
			link={linkFor(one, editing ? draft : values)}
			copyable={!editing}
		/>
	{/if}
{/snippet}

{#if all.length > 0}
	<form id={formId} onsubmit={save} aria-label={editing ? `Editing ${label}` : label}>
		<dl class="record">
			{#each shown as one (one.key)}
				<div class="fact">
					<dt id="{uid}-{one.key}">
						<label for={editing && one.editable ? `${uid}-${one.key}-box` : undefined}>
							{one.label}
						</label>
					</dt>
					<!--
						One `dd` in both modes, holding either the value or the control.

						Not two branches drawing two elements: the grid has to be the same grid, and
						the fastest way to make a cell move is to give it a different box depending on
						what is in it.
					-->
					<dd>
						{@render cell(one)}{#if under && !editing}{@render under(one)}{/if}
					</dd>
				</div>
			{/each}
		</dl>

		{#if wide.length > 0}
			<!-- The wide half: a description, a list of addresses, a row of chips. Each takes the
			     whole width and they sit under the short facts, so growing changes only what is
			     below them. Same order and same markup in both modes. -->
			<dl class="wide">
				{#each wide as one (one.key)}
					<div class="fact">
						<dt id="{uid}-{one.key}">
							<label for={editing && one.editable ? `${uid}-${one.key}-box` : undefined}>
								{one.label}
							</label>
						</dt>
						<dd>
							{@render cell(one)}{#if under && !editing}{@render under(one)}{/if}
						</dd>
					</div>
				{/each}
			</dl>
		{/if}

		<!-- Only the message. Save and Cancel are drawn by the page, in the place the Edit button
		     was, and reach this form through `form=`. A second pair at the bottom would be two ways
		     to do one thing, and the far one is the one nobody presses. -->
		{#if editing || alone !== null}<Problem message={problem} />{/if}
	</form>
{/if}

<style>
	/*
	 * Columns that fill whatever width there is, each wide enough for a name or a link to sit on one
	 * line. `auto-fill` rather than `auto-fit`: with two facts and a wide window `auto-fit` stretches
	 * them across the whole thing, which puts a two-word value in a column the width of a wall.
	 *
	 * The same declaration the read-only record carries, deliberately identical: the grid must not
	 * change when the cells become boxes, and two nearly-equal grid declarations is how it comes to.
	 */
	.record {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr));
		gap: var(--space-4) var(--space-6);
		margin: 0;
	}

	.fact {
		min-inline-size: 0;
	}

	/* The label, quiet and small. It is the thing you read past to get to the value, not the thing
	   you read, so it is the dimmer of the two, which is the opposite of a settings pane. */
	dt {
		font: var(--text-label);
		color: var(--sift-ink-3);
		margin-block-end: var(--space-1);
	}

	dt label {
		font: inherit;
		color: inherit;
	}

	/*
	 * The height of a control, reserved whether one is drawn or not.
	 *
	 * This is what keeps the page still. A value is one line of text and a box is taller than one
	 * line, so a grid of twelve facts would grow by twelve differences the moment Edit was pressed
	 * and everything below it would move. Reserving the taller of the two in BOTH modes costs a little air
	 * around each value and buys a record that does not jump under the hand.
	 *
	 * `align-content: center` so a short value sits in the middle of the box it will become rather
	 * than at the top of it: otherwise the text itself moves down when the box appears, which is
	 * the same fault at a smaller size.
	 */
	dd {
		margin: 0;
		min-inline-size: 0;
		min-block-size: 2.25rem;
		display: grid;
		align-content: center;
	}

	/* The "Add" prompt sits where the value would start, under its label. A grid item is
	   stretched across the cell by default, and a stretched button centres its word, which would
	   put every "Add" in the middle of its column, nowhere near the label it answers. Only the link:
	   a box in edit mode still fills the cell. */
	dd > :global(.link) {
		justify-self: start;
	}

	/* A value that copies itself starts at its label's edge: the press's inset is pulled back, so
	   the words line up with the label above them and the ground spills a step to the left.
	   Moved by an offset rather than a negative margin: the press sits in its tooltip's
	   shrink-to-fit wrapper, which sizes itself to the press's margin box, so a negative margin
	   would make every value a step narrower than its own words and break "png" into "pn" and "g". */
	dd :global(.copyable) {
		justify-self: start;
		position: relative;
		inset-inline-start: calc(-1 * var(--space-1));
	}

	/* One field open on its own fills its cell, as a box in the whole form does. */
	.alone {
		min-inline-size: 0;
	}

	/* The SAME grid as the facts above, so the record reads as one thing.
	 *
	 * A full-width stack would put a narrow text box under a wide one and leave the right half of
	 * the record empty below the last fact. Declared identically to `.record` on purpose:
	 * two nearly-equal grid declarations is how a layout comes to be almost aligned.
	 */
	.wide {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr));
		gap: var(--space-4) var(--space-6);
		margin: var(--space-4) 0 0;
		/* Bottom-aligned, so a tall paragraph and a short list of links sit on the same baseline
		   instead of the shorter one floating in the middle of the taller one's row. */
		align-items: end;
	}

	/* The wide half's cells hold whatever they hold. No reserved height here, deliberately: there
	   is nothing under them but the Save row, so their growing costs nobody their place. */
	.wide dd {
		min-block-size: 0;
		display: block;
	}

	/* Every control in a cell fills it. A textarea sizes itself from `cols` otherwise, which
	   would make the two lopsided: a box the width of the browser's idea of forty characters
	   sitting beside one that had been told to fill its column. */
	.wide dd :global(textarea),
	.wide dd :global(input) {
		inline-size: 100%;
		box-sizing: border-box;
	}
</style>
