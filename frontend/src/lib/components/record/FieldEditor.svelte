<script lang="ts">
	/*
	 * How ONE field of a record is edited. The only definition of it.
	 *
	 * ## Why this is not inside the form
	 *
	 * A record is edited on two shapes of page and they are genuinely different shapes: an entity
	 * page edits a column of fields under a heading, and a file's record edits a grid of short facts
	 * in place, without the page moving. Two layouts is a decision somebody made; two copies of "a
	 * date is a date control, a list is boxes with an adder, a nationality is chosen and never
	 * typed" is the thing that always comes apart: one of them gains a kind, the other keeps
	 * stringifying it, and both look right on their own screen.
	 *
	 * So the two layouts hold the geometry and this holds the rule. A kind added to the registry is
	 * added here once and appears on both.
	 *
	 * ## It never draws its own label
	 *
	 * The label is a `<label for>` in the form and a `<dt>` in the grid, and those are not the same
	 * element. Whoever draws the label passes the id it points at.
	 */
	import {
		Button,
		DateField,
		MenuButton,
		NumberInput,
		PickMenu,
		Select,
		SuggestInput,
		Switch,
		TagChip,
		TextArea,
		TextInput,
		Tooltip
	} from '$lib/components/common';
	import { appearance } from '$lib/theme/appearance.svelte';
	import { COUNTRIES, COUNTRY_CODES } from '$lib/people/countries';
	import { heightFromParts, heightParts } from '$lib/shell/measure';
	import { PICK_PAGE } from '$lib/search/frequent.svelte';
	import { linkMarks, pickRow } from '$lib/entity/entity-picture';
	import Icon from '$lib/components/Icon.svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import type { PickChoice, PickPage } from '$lib/components/common/verbs';
	import type { FieldDescription } from '$lib/entity/records.svelte';

	/** Marks that failed to load, so the next candidate (or the chain link) is tried instead. */
	const unmarked = new SvelteSet<string>();
	import { tags as tagStore } from '$lib/entity/tags.svelte';

	interface Props {
		field: FieldDescription;
		/** What is in the draft for this field. Never the stored value: the caller holds the draft. */
		value: unknown;
		/** The whole new value for this field. A list arrives as a list, always. */
		onchange: (next: unknown) => void;
		/** The id the label points at, so the control the label names is the one that takes focus. */
		id?: string;
		describedBy?: string;
		/** What the record being edited is called: never offered as the value of a field that names
		 *  something (a Site is not part of itself). */
		self?: string;
	}

	let { field, value, onchange, id, describedBy, self }: Props = $props();

	/** What is being typed into the adder of a list field. Local, because it is not part of the
	 *  record: it is a half-typed entry, and it is discarded the moment it is taken. */
	let typing = $state('');

	/** A list field is edited as boxes with an adder; everything else takes one control. The
	 *  registry's `LIST_KINDS` is the same pair. What the adder's empty box says is the field's
	 *  own `entry`, declared beside the field, so a list of tattoos never asks for "another
	 *  name". */
	const isList = $derived(field.kind === 'names' || field.kind === 'links');

	/** Whether the ORDER of this list is stored, so each entry can be moved up and down: a song's
	 *  artists, in the order the recording credits them. The registry says so (`ordered`). */
	const ordered = $derived(field.ordered === true);

	/** A list entry as text, whichever shape it arrived in: a bare name, or a link with a url. */
	function asText(one: unknown): string {
		if (typeof one === 'string') return one;
		const held = one as { url?: string; alias?: string; name?: string };
		return held.url ?? held.alias ?? held.name ?? String(one);
	}

	const list = $derived(Array.isArray(value) ? (value as unknown[]).map(asText) : []);
	const chips = $derived(Array.isArray(value) ? (value as { id: string; name: string }[]) : []);

	/* One page of the vocabulary, filtered by what is typed, with what is already in the draft taken
	   out of it, and counted into `more`, so the line under the list still says how many tags exist
	   rather than how many this page happened to keep. The server's own page, alphabetical, never the
	   store's cached wall: that is one page of an order chosen for a different screen. */
	async function askTags(typed: string): Promise<PickPage> {
		const asked = await tagStore.choices(typed, PICK_PAGE);
		const on = new Set(chips.map((one) => one.id));
		const rows = asked.items.filter((tag) => !on.has(tag.id));
		const dropped = asked.items.length - rows.length;
		return {
			choices: rows.map((tag) => pickRow('tag', tag)),
			more: Math.max(0, asked.total - asked.items.length + dropped)
		};
	}

	/* Making one is the one thing here that DOES write before Save, and it has to: a tag has to
	   exist before a draft can name it. The tag is created; putting it on this thing is still the
	   form's business. */
	async function makeTag(named: string): Promise<PickChoice> {
		const made = await tagStore.create(named);
		return { id: made.id, name: made.name };
	}

	function add() {
		const wanted = typing.trim();
		if (!wanted) return;
		typing = '';
		if (list.some((one) => one.toLowerCase() === wanted.toLowerCase())) return;
		onchange([...list, wanted]);
	}

	/* Change one entry in place.
	 *
	 * Not trimmed or checked for a duplicate as it is typed: halfway through editing "Jane" into
	 * "Janet" the text is briefly "Jane" again, and refusing it there would be refusing somebody
	 * mid-word. An entry emptied entirely is dropped when the record is saved rather than stored as
	 * a blank name.
	 */
	function edit(at: number, text: string) {
		onchange(list.map((held, index) => (index === at ? text : held)));
	}

	/* One entry one place up (`-1`) or down (`1`), swapped with its neighbour. Nothing at either end:
	   the press there is drawn refused. */
	function move(at: number, by: -1 | 1) {
		const to = at + by;
		if (to < 0 || to >= list.length) return;
		const next = [...list];
		[next[at], next[to]] = [next[to], next[at]];
		onchange(next);
	}

	/* A nationality is CHOSEN, never typed.
	 *
	 * The value stored is a two-letter code, and a box somebody types a code into is a box that ends
	 * up holding `USA`, `U.S.` and `America` for one country: none of which any importer or filter
	 * would ever match. The list is the same one the record reads the name out of.
	 *
	 * An empty first option is how the field is CLEARED. `Select` swaps the empty string for its own
	 * stand-in internally, because the primitive underneath reads `''` as nothing chosen and draws a
	 * blank control, so it is safe to write here and must stay written here.
	 */
	const countries = [
		{ value: '', label: 'Not set' },
		...COUNTRY_CODES.map((code) => ({ value: code, label: COUNTRIES[code] }))
	];

	/*
	 * A length is typed in the system this account reads in.
	 *
	 * `height_cm` is the one field of the one measured kind a record carries. Under imperial the
	 * field is two boxes, feet and inches, and what is saved is still whole centimetres: the
	 * column, every filter and every stash-box are metric, and the choice is about reading and
	 * typing, not storage. `$lib/shell/measure` holds both halves of the arithmetic, so the pair typed
	 * here and the phrase the record draws cannot disagree.
	 *
	 * The draft holds whatever the last writer put there (the number the record arrived with, or
	 * the text a metric box sent), so it is read through one rule. Anything that is not a height
	 * above zero is nothing: zero is what the server treats as no height, so an untouched empty
	 * field reads 0 ft 0 in and saves as nothing rather than a measurement of none.
	 */
	const centimetres = $derived.by(() => {
		const written = typeof value === 'number' ? String(value) : String(value ?? '').trim();
		const read = Number(written);
		return written !== '' && Number.isFinite(read) && read > 0 ? read : null;
	});

	const parts = $derived(centimetres === null ? { feet: 0, inches: 0 } : heightParts(centimetres));

	/* Both boxes write the whole height, because a height is one field: the box that was not touched
	   hands back what it is already showing. An empty pair is the field CLEARED: the same empty
	   string the centimetre box sends when somebody clears it, which the server files as nothing. */
	function heightTyped(feet: number, inches: number): void {
		const held = heightFromParts(feet, inches);
		onchange(held > 0 ? held : '');
	}

	/** What a box for this kind should ask a phone's keyboard for. */
	const mode = $derived(
		field.kind === 'link'
			? ('url' as const)
			: field.kind === 'year' || field.kind === 'length'
				? ('numeric' as const)
				: undefined
	);
</script>

{#if isList}
	<!--
		The whole list lives inside the control, entries and all.

		Not a nicety: the field wrapper is where the app's text-box look is declared, scoped to that
		control. Entries rendered as a sibling would come out as browser-default white boxes in the
		middle of a dark form. Anything that is part of a field belongs in the field.
	-->
	<div class="adding">
		{#if field.suggests}
			<!-- A field that NAMES something the library already knows about completes from what is
			     there. A site's other names and a person's other names are the two places a second
			     spelling of an existing thing is most easily typed in, which is the exact drift these
			     lists exist to undo. Which vocabulary comes from the registry. -->
			<SuggestInput
				{id}
				{describedBy}
				suggests={field.suggests}
				value={typing}
				placeholder={field.entry ?? undefined}
				oninput={(typed) => (typing = typed)}
				onsubmit={(typed) => {
					typing = typed;
					add();
				}}
			/>
		{:else}
			<TextInput
				{id}
				{describedBy}
				value={typing}
				oninput={(event) => (typing = event.currentTarget.value)}
				onkeydown={(event) => {
					if (event.key !== 'Enter') return;
					// Enter adds to the list; it must not submit the record, which would save while
					// somebody was still filling one field in.
					event.preventDefault();
					add();
				}}
				placeholder={field.entry ?? undefined}
				autocomplete="off"
			/>
		{/if}
		<Button type="button" icon="add" onclick={add}>Add</Button>
	</div>

	{#if list.length > 0}
		<!--
			Every entry is a BOX, not a chip.

			A chip can only be added and taken away: a name typed with a letter missing would have to
			be deleted and retyped, and a link with a typo in it read off the screen first. A record is a
			thing you correct, and correcting is the ordinary case rather than the rare one.

			Keyed by position, not by value. Keyed by the text, editing a character destroys the row
			and builds a new one, which takes the caret with it, so the box would lose focus on
			every keystroke.
		-->
		<ul class="entries">
			{#each list as entry, at (at)}
				{@const mark =
					field.kind === 'links' ? linkMarks(entry).find((one) => !unmarked.has(one)) : undefined}
				<li>
					{#if field.kind === 'links'}
						{#if mark}
							<img
								class="mark"
								src={mark}
								alt=""
								width="16"
								height="16"
								onerror={() => unmarked.add(mark)}
							/>
						{:else}
							<Icon name="link" size={16} />
						{/if}
					{/if}
					<TextInput
						value={entry}
						aria-label="{field.label}, {at + 1}"
						autocomplete="off"
						oninput={(event) => edit(at, event.currentTarget.value)}
					/>
					{#if ordered}
						<!-- A list whose order is stored: the rail's own two words and glyphs for one step
						     up and one step down, beside the Remove at the row's end, at its size. -->
						<Tooltip label="Move up">
							<Button
								icon="arrow_upward"
								size="small"
								tone="ghost"
								type="button"
								aria-label={`Move ${entry} up`}
								disabled={at === 0}
								onclick={() => move(at, -1)}
							/>
						</Tooltip>
						<Tooltip label="Move down">
							<Button
								icon="arrow_downward"
								size="small"
								tone="ghost"
								type="button"
								aria-label={`Move ${entry} down`}
								disabled={at === list.length - 1}
								onclick={() => move(at, 1)}
							/>
						</Tooltip>
					{/if}
					<Tooltip label="Remove {entry}">
						<Button
							icon="close"
							size="small"
							tone="ghost"
							type="button"
							aria-label="Remove {entry}"
							onclick={() => onchange(list.filter((_, index) => index !== at))}
						/>
					</Tooltip>
				</li>
			{/each}
		</ul>
	{/if}
{:else if field.kind === 'tags'}
	<!--
		The same picker every other surface uses: a tag is shared vocabulary, and typing a
		near-match into a free text box is how a library ends up with two tags nobody can tell
		apart. The shape is an entity header's (chips, then a button opening straight onto the
		list), using `PickMenu` drawn `inline` so the button opens onto the rows rather than onto a
		menu holding one row that must be opened again.

		It writes into the draft and nothing else. A record is saved by its form, so a pick adds a
		chip and a cross takes one off, and neither touches the server until Save.
	-->
	<div class="tags">
		{#each chips as chip (chip.id)}
			<TagChip
				name={chip.name}
				onremove={() => onchange(chips.filter((one) => one.id !== chip.id))}
			/>
		{/each}
		<MenuButton label="Add a tag to {field.label}" words="Tag">
			<PickMenu
				label="Tag"
				icon="shoppingmode"
				kind="tag"
				plural="tags"
				inline
				ask={askTags}
				onpick={(choice) => {
					if (chips.some((one) => one.id === choice.id)) return;
					onchange([...chips, { id: choice.id, name: choice.name }]);
				}}
				oncreate={makeTag}
			/>
		</MenuButton>
	</div>
{:else if field.kind === 'date'}
	<DateField
		{id}
		{describedBy}
		label={field.label}
		value={String(value ?? '')}
		onchange={(day: string) => onchange(day)}
	/>
{:else if field.kind === 'country'}
	<Select
		{id}
		{describedBy}
		options={countries}
		value={String(value ?? '')}
		onValueChange={(chosen: string) => onchange(chosen)}
		placeholder="Not set"
	/>
{:else if field.kind === 'flag'}
	<!--
		Yes or no, and the same switch the whole application uses for one.

		A `Select` of two options was the other shape and is worse here: a flag has no "not set", so a
		list with two entries offers a chooser for a question that is already answered. The switch says
		the state and takes one press to change it.

		Written back as a real boolean rather than as the 0 or 1 the column keeps, because the draft
		is what the form sends and the server reads either: a screen that passed the stored form
		back would be the storage leaking through the edit.
	-->
	<Switch
		{id}
		{describedBy}
		checked={value === true || value === 1 || value === '1'}
		onCheckedChange={(on: boolean) => onchange(on)}
	/>
{:else if field.kind === 'paragraph'}
	<TextArea
		{id}
		{describedBy}
		rows={4}
		value={String(value ?? '')}
		oninput={(event) => onchange(event.currentTarget.value)}
	/>
{:else if field.kind === 'length' && appearance.units === 'imperial'}
	<!--
		Two boxes, each with its unit inside rather than a word beside it: the field this app draws
		for a number with a unit, so a height is typed in the same control as every other measured
		setting.

		The label the form drew points at the feet box, filled in first; each box names itself as
		well, because "Height" alone on the second would announce two different fields with one
		name.
	-->
	<div class="parts">
		<NumberInput
			{id}
			{describedBy}
			label="{field.label}, feet"
			unit="ft"
			min={0}
			width={2}
			value={parts.feet}
			onchange={(feet: number) => heightTyped(feet, parts.inches)}
		/>
		<NumberInput
			{describedBy}
			label="{field.label}, inches"
			unit="in"
			min={0}
			max={11}
			width={2}
			value={parts.inches}
			onchange={(inches: number) => heightTyped(parts.feet, inches)}
		/>
	</div>
{:else if field.suggests}
	<!-- A single box that names something: the network a site is part of. It completes from the
	     sites that exist and never refuses one that does not: a network Sift has not heard of is how
	     the second site on it comes to have one to complete from. -->
	<SuggestInput
		{id}
		{describedBy}
		suggests={field.suggests}
		value={String(value ?? '')}
		oninput={(typed) => onchange(typed)}
		leaveOut={self ? [self] : []}
	/>
{:else}
	<!--
		`type="text"` for an address as well, deliberately. `type="url"` adds the browser's own
		validation, which refuses anything without a scheme and says so in the site's wording, in
		the site's bubble, in a place the page cannot style or explain, and this box is also how
		an address is CLEARED, which that validation treats as another thing to complain about.
	-->
	<TextInput
		{id}
		{describedBy}
		type="text"
		inputmode={mode}
		placeholder={field.kind === 'link' ? 'https://\u2026' : undefined}
		value={String(value ?? '')}
		oninput={(event) => onchange(event.currentTarget.value)}
		autocomplete="off"
	/>
{/if}

<style>
	.adding {
		display: flex;
		gap: var(--space-2);
		align-items: center;
	}

	/* `:global`, because the box is `TextInput`'s own element, compiled in that file's scope. */
	.adding :global(.text-input) {
		flex: 1 1 auto;
		min-inline-size: 0;
	}

	/* One per row, full width, so an entry lines up with the box that adds them and with every other
	   field. Wrapped chips would put a long link on a line of its own anyway and leave the short
	   ones in a ragged row. */
	.entries {
		list-style: none;
		margin: var(--space-2) 0 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.entries li {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	.entries :global(.text-input) {
		flex: 1 1 auto;
		min-inline-size: 0;
	}

	/* The two halves of one measurement, side by side on one line. They are one field, so they sit
	   together rather than wrapping: feet and inches on separate rows would read as two questions. */
	.parts {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	/* The chips already on this thing, and the button that adds one, on the same wrapping line:
	   the same arrangement an entity's header draws them in. */
	.tags {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
	}

	/* The site's mark before a link being edited: the text's height, never larger. */
	.mark {
		/* Sized by its width and height attributes, the text's height; only kept in shape here. */
		object-fit: contain;
		flex: none;
	}
</style>
