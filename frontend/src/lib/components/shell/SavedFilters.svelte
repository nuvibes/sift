<script lang="ts">
	/* WHY NOT BITS-UI: there is no widget here. It is a bordered region holding a wrapping row of
	   `SavedFilterButton`, and one editing state with two buttons. Everything with behaviour in it
	   (the menus, the tooltips, the field) is the library one level down. */
	/* NOT ON THE GALLERY: this reads the one list of kept filters and writes to it, so a second live
	   copy would be a second set of controls acting on the same rows. Everything it is MADE of is on
	   the gallery: the pill, the row menu, the field, the buttons. */

	/*
	 * The kept filters, at the foot of the panel they belong to.
	 *
	 * Under the columns, inside a line that says they are a set: choosing among kept filters and
	 * choosing filters are the same act, so they belong in the same panel rather than behind a
	 * trigger of their own.
	 *
	 * Editing one means editing a draft, and the screen never moves. The facet columns above are
	 * the editor, and they already understand excluded values, any-of against all-of and free text.
	 * `savedSearches.editing` carries a draft: the panel reads and writes the draft while an edit
	 * is open and the address otherwise (one seam, `Narrowing`, in `FilterBar`), and nothing
	 * navigates, so the bar's chips keep describing the screen and filters chosen by hand are never
	 * cleared by opening an editor. Save writes the draft under the name; Cancel drops it. There is
	 * nothing to remember or restore, which is why `onapply` is one callback: it is for pressing a
	 * kept filter and nothing else.
	 */
	import {
		Button,
		Chip,
		EditMarks,
		Empty,
		SectionHeading,
		TextInput
	} from '$lib/components/common';
	import { ASSET_WALL, savedSearches, type SavedSearch } from '$lib/search/saved-searches.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import FilterChip from './FilterChip.svelte';
	import SavedFilterButton from './SavedFilterButton.svelte';
	import { partsOf, written } from '$lib/search/query-parts';
	import { ChipOrder } from './filter-bar.svelte';

	interface Props {
		/**
		 * Which wall these are the kept filters of.
		 *
		 * A kept filter is spelled in its wall's own vocabulary (the query language on a wall of
		 * files, that noun's facets on a wall of people), so it is offered only where it means
		 * something, and anything kept on a wall is offered back there. Handed in rather than
		 * worked out here: the bar already knows what it is drawing.
		 */
		kind?: string;
		/**
		 * The parameter names that are FILTERS on this wall, for the chips under "Editing".
		 *
		 * The same list the bar draws its own chips from. Without it the draft would be read in the
		 * file language on every wall, so a People filter would show none of its own dimensions.
		 */
		fields?: readonly string[];
		/**
		 * What is filtering the screen NOW, as a query string.
		 *
		 * One writer needs it: "update from current filters" keeps what is on screen under an existing
		 * name. The EDIT does not: it has a draft of its own, and that is the point.
		 */
		current: string;
		/** Put this query on whatever is being filtered. Pressing a kept filter, and nothing else. */
		onapply: (query: string) => void;
		/**
		 * Whether Edit is offered.
		 *
		 * Off where the caller has no columns to edit WITH: the theater's picker draws this list to
		 * choose a cell's source, not to change one. A menu row that fails is worse than a missing
		 * one, so it is simply not drawn there.
		 */
		editable?: boolean;
		/**
		 * The three things a chip can do, for the chips under "Editing".
		 *
		 * They are the bar's own three verbs pointed at the DRAFT rather than at the address, which is
		 * why they are threaded through rather than rebuilt here: the bar owns how a filter is written
		 * down and this file has no business learning it.
		 *
		 * A chip here draws ONE value, as on the bar, so refusing it and taking it off take the
		 * dimension and that one value; any-or-all belongs to the whole parameter and takes its raw
		 * value, the pair a query string holds.
		 *
		 * Absent, the chips only describe.
		 */
		onflip?: (field: string, value: string) => void;
		ondrop?: (field: string, value: string) => void;
		onswitchmatch?: (field: string, value: string) => void;
	}

	let {
		current,
		onapply,
		kind = ASSET_WALL,
		fields = undefined,
		editable = false,
		onflip,
		ondrop,
		onswitchmatch
	}: Props = $props();

	/* This wall's own, and nobody else's. The store holds one account's whole list because it is one
	   small list; which of them belong here is this line. */
	const keptHere = $derived(savedSearches.on(kind));

	/* Asked for when this exists, which is when the panel holding it is open. A list most people
	   never look at should not cost a request on every page load. */
	$effect(() => {
		void savedSearches.ensure();
	});

	/** Which one is having its NAME changed, and to what. Null when none is. */
	let renaming = $state<string | null>(null);
	let draft = $state('');

	/*
	 * Which write is in flight, so the screen says one is.
	 *
	 * Keeping a filter goes to the server and back, and can take several seconds; a panel unchanged
	 * all that time reads as a press that did nothing, and gets pressed again.
	 *
	 * Two states rather than one flag, because they are two subjects. The edit is a form whose Save
	 * is a button, so it wears `Button.busy` like every other write. "Update from current filters"
	 * is a menu row, gone the instant it is pressed, so the pill it was opened from carries it, by
	 * id, as a list of rows carries a per-row busy elsewhere.
	 *
	 * They are also the re-entry guard: the button is disabled while it turns, but the menu row is
	 * not (see `KeptPill`), so a second Update is refused here rather than sent.
	 */
	let saving = $state(false);
	let updating = $state<string | null>(null);

	/* The open edit, when it is one of THIS wall's. The draft survives the panel closing and the
	   screen changing, so on any other wall it is somebody else's filter and there is nothing here
	   that could honestly edit it: the columns above are spelled in this wall's vocabulary. */
	const editing = $derived(savedSearches.editing?.kind === kind ? savedSearches.editing : null);

	/**
	 * What the edit is holding, as chips: its own draft, never the screen, so the chips describe
	 * the filter without the filter taking the screen over. See `savedSearches.editing`.
	 */
	const holding = $derived(
		fields ? partsOf(editing?.draft ?? '', fields) : partsOf(editing?.draft ?? '')
	);

	/* One chip per value, in the order the bar's own chips keep. */
	const order = new ChipOrder();
	const chips = $derived(order.lay(holding, (part) => part));

	function apply(kept: SavedSearch) {
		onapply(kept.query);
	}

	/* Nothing navigates. The draft starts as what the filter holds and the columns take it from
	   there; the screen somebody was on is left exactly as it was. */
	function beginEdit(kept: SavedSearch) {
		savedSearches.editing = {
			id: kept.id,
			name: kept.name,
			draft: kept.query,
			kind: kept.kind ?? ASSET_WALL
		};
	}

	function stopEditing() {
		savedSearches.editing = null;
	}

	async function saveEdit() {
		const was = savedSearches.editing;
		if (!was || saving) return;
		saving = true;
		try {
			/* The filter's OWN wall, carried on the draft rather than taken from the screen: an edit
			   can only be open on the wall the filter belongs to, and reading it off the panel would
			   be a second answer that a future screen could disagree with. */
			await savedSearches.update(was.name, was.draft, was.kind);
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
			return;
		} finally {
			saving = false;
		}
		stopEditing();
	}

	async function updateFromCurrent(kept: SavedSearch) {
		if (updating !== null) return;
		updating = kept.id;
		try {
			await savedSearches.update(kept.name, current, kept.kind ?? ASSET_WALL);
			toasts.show(`"${kept.name}" has been updated`, { tone: 'success' });
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		} finally {
			updating = null;
		}
	}

	function startRename(kept: SavedSearch) {
		renaming = kept.id;
		draft = kept.name;
	}

	async function commitRename(kept: SavedSearch) {
		const wanted = draft.trim();
		renaming = null;
		if (!wanted || wanted === kept.name) return;
		try {
			await savedSearches.rename(kept.id, wanted);
		} catch {
			toasts.show("That couldn't be renamed", { tone: 'error' });
		}
	}

	async function remove(kept: SavedSearch) {
		try {
			await savedSearches.remove(kept.id);
		} catch {
			toasts.show("That couldn't be deleted", { tone: 'error' });
		}
	}
</script>

<section class="saved" aria-label="Saved filters">
	<SectionHeading band>Saved filters</SectionHeading>

	{#if keptHere.length === 0}
		<!-- Names the control by its own tooltip, never by where it sits. -->
		<Empty scope="block"
			>No saved filters yet. Select some filters, then press the Add to saved filters icon on the
			filters bar.</Empty
		>
	{:else}
		<ul>
			{#each keptHere as kept (kept.id)}
				<li>
					{#if renaming === kept.id}
						<!--
							The two answers are inside the box: a tick and a cross inside the
							field's own edge. A bare box that commits on blur gives no way to change
							your mind and no sign it took, and a Cancel and a Save beside it would
							make one renamed pill twice as wide as its neighbours and push them onto
							another line. A mark in the end of a field is a shape the search boxes
							already use for their cross.

							`data-unfinished` stays: a half-typed name is genuinely destroyed if the
							panel falls shut under the pointer.
						-->
						<div class="renaming">
							<!-- The cross and the tick inside the box are `EditMarks`, the shape a
							     file's record uses to fill one field in, so the two cannot drift. -->
							<EditMarks
								oncancel={() => (renaming = null)}
								onkeep={() => void commitRename(kept)}
								cancelLabel="Keep the name {kept.name}"
								keepLabel="Rename it to what is typed"
							>
								<!-- svelte-ignore a11y_autofocus: the menu row that opened this exists to type into -->
								<TextInput
									bind:value={draft}
									maxlength={120}
									autofocus
									aria-label="Rename {kept.name}"
									onkeydown={(event) => {
										if (event.key === 'Enter') void commitRename(kept);
										if (event.key === 'Escape') renaming = null;
									}}
								/>
							</EditMarks>
						</div>
					{:else}
						<SavedFilterButton
							{kept}
							busy={updating === kept.id}
							onapply={apply}
							onupdate={updateFromCurrent}
							onedit={editable ? beginEdit : undefined}
							onrename={startRename}
							onremove={remove}
						/>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}

	{#if editing}
		<!--
			The one being edited, INSIDE the box the kept ones are in, below a rule.

			Two identical outlines one under the other would read as two lists rather than as a list and
			the thing being done to it. So the outline extends: one edge, one corner, and a line across
			where the pills end. What is being edited belongs to what is kept, and the drawing says so.

			Still its own region for anybody not looking at it: the rule is a line, and a line says
			nothing to a screen reader.
		-->
		<!-- NO `data-unfinished` here, and that is deliberate. The attribute stops the panel falling
		     shut on hover. The draft lives in the store, so shutting the panel costs nothing
		     (reopening it shows the edit exactly where it was) and a panel that will not close when
		     you point away is the more annoying of the two. The rename form keeps it: a half-typed
		     name is genuinely destroyed, and `#someoneIsTyping` only holds while the caret is still in
		     the box. -->
		<div class="editing" role="group" aria-label="Editing a saved filter">
			<SectionHeading band>Editing &ldquo;{editing.name}&rdquo;</SectionHeading>

			<p class="quiet">
				The columns above are the editor while this is open. Nothing on the screen changes until you
				save.
			</p>

			<ul>
				{#each chips as entry (entry.key)}
					{@const part = entry.from}
					{@const one = entry.value}
					{@const value = written(part)}
					<!-- The bar's own chip and verbs, pointed at the draft: one per value, so one tag can be
					     kept and another refused. -->
					<li>
						<FilterChip
							field={part.field}
							values={[one]}
							of={part.values.length}
							all={part.all}
							excluded={part.excluded}
							onselect={onflip ? () => onflip(part.field, one) : undefined}
							onremove={ondrop ? () => ondrop(part.field, one) : undefined}
							onswitch={onswitchmatch ? () => onswitchmatch(part.field, value) : undefined}
						/>
					</li>
				{:else}
					<li><Chip size="sm" tone="quiet">Everything</Chip></li>
				{/each}
			</ul>

			<!-- Save turns while the write is in flight and refuses a second press; Cancel is closed
			     off with it, because dropping the draft half way through writing it leaves somebody
			     unable to say what was kept. -->
			<div class="decide">
				<Button tone="ghost" size="small" disabled={saving} onclick={stopEditing}>Cancel</Button>
				<Button size="small" icon="save" busy={saving} onclick={() => void saveEdit()}>Save</Button>
			</div>
		</div>
	{/if}
</section>

<style>
	/*
	 * A LINE ROUND IT, and the line is what makes these a set rather than more controls.
	 *
	 * The panel is already a surface and the columns above are already on it, so a second ground
	 * would be a box on a box. An edge and a corner say "these belong together" without adding a
	 * step, which is the same reasoning `BarPanel` gives for having an edge at all.
	 */
	.saved {
		display: grid;
		gap: var(--space-3);
		padding: var(--space-3);
		border: 1px solid var(--sift-line);
		border-radius: var(--radius-lg);
	}

	ul {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	/* The row being renamed takes the width it needs, so a long name is not typed into a pill. The
	   marks inside the field are `EditMarks`'s. */
	.renaming {
		min-inline-size: 18rem;
	}

	/*
	 * The one being EDITED, inside the same box, below a rule.
	 *
	 * Not a second bordered box of its own: two identical outlines stacked read as two lists rather
	 * than as a list and the thing being done to one of its rows. A rule costs one line and
	 * says the same thing. The negative margins take the section's own padding back so the rule runs
	 * the full width of the box rather than floating inside it with a gap at each end.
	 */
	.editing {
		display: grid;
		gap: var(--space-3);
		margin-inline: calc(var(--space-3) * -1);
		padding: var(--space-3) var(--space-3) 0;
		border-block-start: 1px solid var(--sift-line);
	}

	/* At the end, which is where this application puts the thing that finishes a question. */
	.decide {
		display: flex;
		justify-content: flex-end;
		gap: var(--space-2);
	}
</style>
