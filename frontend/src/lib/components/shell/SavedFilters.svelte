<script lang="ts">
	/* WHY NOT BITS-UI: there is no widget here. It is a bordered region holding a wrapping row of
	   `SavedFilterButton` and one editing state; the behaviour is the library's. */
	/* NOT ON THE GALLERY: this reads the one list of kept filters and writes to it, so a second live
	   copy would act on the same rows. */

	/*
	 * The kept filters, at the foot of the filter panel. Editing one edits a draft that the facet
	 * columns read and write (`Narrowing` in `FilterBar`), so the screen never moves.
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
		/** Which wall these belong to: a kept filter is spelled in its wall's own vocabulary. */
		kind?: string;
		/** This wall's filter parameter names, so a draft reads in the wall's language. */
		fields?: readonly string[];
		/** What filters the screen now, for "update from current filters". */
		current: string;
		/** Put this query on whatever is being filtered; only pressing a kept filter does. */
		onapply: (query: string) => void;
		/** Whether the Edit row is offered; off where there are no columns to edit with. */
		editable?: boolean;
		/** The bar's own chip verbs, pointed at the draft; absent, the chips only describe. */
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

	// The store holds the account's whole list; this wall's share is this line.
	const keptHere = $derived(savedSearches.on(kind));

	// Asked for only when the panel is open, not on every page load.
	$effect(() => {
		void savedSearches.ensure();
	});

	/** Which one is having its NAME changed, and to what. Null when none is. */
	let renaming = $state<string | null>(null);
	let draft = $state('');

	// Which write is in flight, shown so a slow save is not pressed again; also the re-entry guard.
	let saving = $state(false);
	let updating = $state<string | null>(null);

	// Only this wall's draft: the columns above are spelled in this wall's vocabulary.
	const editing = $derived(savedSearches.editing?.kind === kind ? savedSearches.editing : null);

	/** The draft as chips, never the screen's filters. */
	const holding = $derived(
		fields ? partsOf(editing?.draft ?? '', fields) : partsOf(editing?.draft ?? '')
	);

	/* One chip per value, in the order the bar's own chips keep. */
	const order = new ChipOrder();
	const chips = $derived(order.lay(holding, (part) => part));

	function apply(kept: SavedSearch) {
		onapply(kept.query);
	}

	// Nothing navigates; the columns take the draft from here.
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
			// The filter's own wall, carried on the draft, not read off the screen.
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
						<!-- A tick and a cross inside the field keep the pill's width. -->
						<div class="renaming">
							<!-- `EditMarks`, as a file's record uses, so the two cannot drift. -->
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
		<!-- The edit sits inside the kept filters' box, below a rule, as its own region. -->
		<!-- No `data-unfinished`: the draft is in the store, so the panel may shut on hover. -->
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
					<!-- One chip per value, so one tag can be kept and another refused. -->
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

			<!-- Cancel is closed off during the write, which would leave what is kept unknown. -->
			<div class="decide">
				<Button tone="ghost" size="small" disabled={saving} onclick={stopEditing}>Cancel</Button>
				<Button size="small" icon="save" busy={saving} onclick={() => void saveEdit()}>Save</Button>
			</div>
		</div>
	{/if}
</section>

<style>
	/* A line round it makes these a set without a second ground on the panel's surface. */
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

	/* A long name gets room rather than being typed into a pill. */
	.renaming {
		min-inline-size: 18rem;
	}

	/* A rule, not a second box; negative margins run it the full width. */
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
