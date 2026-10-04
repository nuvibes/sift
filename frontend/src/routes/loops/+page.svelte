<script lang="ts">
	/*
	 * Loops: every stretch somebody marked, drawn by the same grid Browse and Favorites are.
	 *
	 * Not a copy of that grid: the grid itself, pointed at another list, so every decision it has
	 * made (a pager that is always drawn, room for the top row's hover lift, playback under the
	 * cursor) holds here too. A copy would lose them.
	 *
	 * What makes a wall of MOMENTS possible without a second grid is one field: a row may carry
	 * `asset_id`, meaning "the file this row is about is not this row". So a loop keeps an identity
	 * of its own (two marks on one video are two tiles) while the picture, the clip under the
	 * cursor, the heart, the verbs and what opening it opens all belong to the video.
	 *
	 * Nothing here needs a rule about who may see what. A loop reaches an account only through the
	 * file it points at, so a loop of something concealed is not a row at all: a property of the
	 * server's join rather than of a check written on this screen.
	 */
	import { untrack } from 'svelte';
	import AssetGrid from '$lib/components/AssetGrid.svelte';
	import WallControls from '$lib/components/entity/WallControls.svelte';
	import {
		Button,
		ContextMenuGroup,
		ContextMenuItem,
		Field,
		TextInput,
		PickDialog,
		tagConfirmLabel
	} from '$lib/components/common';
	import { clipTheStretch } from '$lib/edit/edit.svelte';
	import { libraryChanges } from '$lib/library/changes.svelte';
	import { LOOP_SOURCE, type GridItem } from '$lib/grid/grid.svelte';
	import { api } from '$lib/api/client';
	import { forgetLabel, forgetLoops } from '$lib/player/loops';
	import { session } from '$lib/shell/session.svelte';
	import { tags as tagStore, type Tag } from '$lib/entity/tags.svelte';
	import { pickRow } from '$lib/entity/entity-picture';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { FIELDS } from '$lib/search/search.svelte';
	import { page } from '$app/state';
	import type { components } from '$lib/api/schema';
	import { LOOP_WORDS, WallWords, wordsIn } from '$lib/components/shell/wall-words';

	/*
	 * Whether the bar is filtering this wall, which decides what an empty one says.
	 *
	 * "No loops yet: mark a stretch in the player" is the answer for a library with no marks, and a
	 * false one under a filter that simply matched none of them: it tells somebody who has fifty to
	 * go and make one. Read off the same names the grid reads as filters, so the two cannot disagree
	 * about whether the wall is filtered.
	 */
	const FILTERING: ReadonlySet<string> = new Set<string>(['q', ...FIELDS]);
	const narrowed = $derived([...page.url.searchParams.keys()].some((name) => FILTERING.has(name)));

	/*
	 * The search box: the marks whose OWN name holds the words, asked of `/loops` as `called`.
	 *
	 * The box is the wall's and not the bar's. The bar filters by the FILES a mark is cut from, and
	 * a mark's name is not a fact about its file, so the words go to the rows' route only and the
	 * bar keeps counting files (`filesQuery`).
	 *
	 * Kept in the address like every wall's words (see `WallWords`), under `called` because `q`
	 * here is the query language the grid reads, so Back and a link keep them.
	 */
	const CALLED = LOOP_WORDS;
	let term = $state('');
	const called = $derived(wordsIn(page.url, CALLED));
	const words = new WallWords(CALLED);
	/* The words the wall asks by: what was last typed and settled, or what the address brought;
	   the address is written too, so Back and a link carry the same words. */
	let settled = $state(wordsIn(page.url, CALLED));
	const asking = $derived<Record<string, string>>(settled ? { called: settled } : {});

	/* Words arriving any other way (Back, a link) are put in the box; the box's own write is not
	   handed back to somebody still typing. */
	$effect(() => {
		const arrived = called;
		untrack(() => {
			settled = arrived;
			if (words.echoed(arrived)) return;
			term = arrived;
		});
	});

	/*
	 * What this list can and cannot do, said out loud rather than discovered.
	 *
	 * An anchor: `/loops` resolves `from` against this same scoped, ordered, filtered list, a tag's
	 * reach included, so this wall keeps its place on the way back from a mark exactly as every
	 * other wall does.
	 *
	 * A funnel: a mark is a piece of a file, so a filter on the FILE is exactly what filtering a
	 * wall of marks means, and `/loops` reads the language with the engine the library does. Saved
	 * filters, chips and the panel's columns all work here, the columns counting the files that
	 * have a mark (`LOOP_SOURCE.files`).
	 *
	 * Six orders, which are the six every wall in Sift shares. `Longest first` is missing because
	 * it is a fact about a video rather than about a mark of it; `Biggest first` is the mark's own
	 * length, which is this wall's size in the way an item count is another wall's.
	 */

	/*
	 * The verbs a mark does not offer.
	 *
	 * Every verb in Sift acts on a FILE, which is right here for most of them: tagging, hearting
	 * or sharing from a mark's tile means the video, and that is what somebody pressing it means. The
	 * four left out are the ones where it would not be: a menu row on a wall of marks that reads as
	 * removing the mark and removes the video instead is the worst kind of wrong, and moving,
	 * compressing or re-encoding a video from a tile of one moment of it is a decision nobody is
	 * making on this screen. Forgetting the mark is offered below, where it belongs.
	 */
	const NOT_HERE = ['delete', 'move', 'compress', 'edit', 'tag'] as const;

	/*
	 * Tagging is left out of the shared verbs and offered below instead, and that is the one place
	 * this wall deliberately disagrees with every other.
	 *
	 * The shared Tag verb puts a tag on the FILE, which on every other wall is what somebody means.
	 * Here the row is a MOMENT, and a twenty-second stretch is one thing while the forty minutes it
	 * sits in is many. So "Tag" on a mark's tile tagging the whole video is the exact fault the
	 * feature exists to avoid. This screen calls the endpoints for tagging the mark.
	 *
	 * Both are offered, named so the difference is on the row rather than in somebody's head.
	 */

	/*
	 * Naming a mark.
	 *
	 * A form above the wall rather than a dialog, which is where the Collections and Photo Sets
	 * walls put the same thing: choosing the row from its own menu fills the box, and Save writes
	 * it. A mark starts with no name at all, so the line under its tile is empty until the box
	 * gives it one.
	 */
	let naming = $state<{ id: string; was: string } | null>(null);
	let named = $state('');
	let busy = $state(false);

	function rename(id: string, was: string) {
		naming = { id, was };
		named = was;
	}

	/*
	 * Tagging the mark.
	 *
	 * The same sheet the Tag verb opens everywhere else (`PickDialog`, which the Tag,
	 * Add-to-collection and Assign-person verbs all open), and a mark DRAWS its tags on its own
	 * tile, so the write has somewhere to be read. A write nothing draws looks like a press that
	 * did nothing.
	 *
	 * Admin-only, like every other write to shared vocabulary: a tag changes what everybody's
	 * searches return, whoever put the mark there. Making the mark is not, which is why the two are
	 * asked differently.
	 */
	let tagging = $state<{
		id: string;
		label: string;
		on: readonly { id: string; name: string }[];
	} | null>(null);

	/* The shared sheet offers what the tag store holds, and the store is loaded by whoever needs it.
	   The sheet does not load it itself, so without this every mark's sheet would say "Nothing
	   matched" over a library full of tags. */
	/* `loaded` is the dependency; `loading` is checked inside `untrack` and is not one.
	 *
	 * Both tracked, this loops the moment the server refuses: the request fails, `loading` goes
	 * back to false, `loaded` never goes true, and the effect (which was watching `loading`)
	 * runs again and asks again, as fast as the machine can go. Watching `loaded` alone cannot:
	 * on success it goes true, this runs once more and returns; on failure nothing it depends on
	 * moved. `loading` still guards, so two screens mounted together do not both fetch. */
	$effect(() => {
		const stale = !tagStore.loaded;
		untrack(() => {
			if (stale && !tagStore.loading) void tagStore.load();
		});
	});

	/*
	 * Deleting loops, as a VERB rather than a menu row.
	 *
	 * A menu row is drawn per TILE and would delete exactly the one it was opened on. The shared
	 * file Remove is hidden on this wall on purpose (it removes the FILE), so a selection of
	 * thirty loops needs a verb of its own. Destructive, so both surfaces draw it last and alone. `extraVerbs` is the missing half of `hideVerbs`: the
	 * wall hands over a verb, and the bar and the menu both draw it from the one definition.
	 *
	 * The wording and the request are `$lib/player/loops`, so they can be tested; what is left here is the
	 * part that really is about this screen.
	 */
	function forgetVerbs(ids: string[], grid: { forget: (id: string) => void }) {
		return [
			{
				id: 'forget',
				label: forgetLabel(ids.length),
				icon: 'delete' as const,
				destructive: true,
				run: () => void forgetLoops(ids, grid.forget)
			}
		];
	}

	/* Read off the ROW, not fetched. The wall already carries every mark's tags (see `GridItem`),
	   so opening this sheet costs nothing and cannot show a different answer from the tile. */
	function tagTheMark(item: GridItem) {
		tagging = {
			id: item.id,
			label: item.original_filename ?? 'this loop',
			on: item.tags ?? []
		};
	}

	/* Everything not already on this mark, which is what the shared sheet expects: it offers a list
	   to tick, and offering a tag the mark already carries is a row that does nothing. Each row wears
	   the picture the tag's card wears (`pickRow`). */
	const offerable = $derived(
		tagStore.items
			.filter((tag) => !(tagging?.on ?? []).some((on) => on.id === tag.id))
			.map((tag) => pickRow('tag', tag))
	);

	/* One request per tag, because that is the shape the endpoint has: it takes one tag and says
	   whether it goes on or comes off. Sequential rather than parallel: this is a handful of tags,
	   and the server answers with the whole set each time, so the last answer is the one kept. */
	/* Cleared when the sheet closes, whichever way it was closed: the button, Escape or a click
	   beside it. A one-way `open` would leave this set after the first close and the row would never
	   open the sheet again. Named here rather than written inline so the markup holds no prose. */
	function closeTagging(next: boolean) {
		if (!next) tagging = null;
	}

	async function putOn(mark: { id: string }, chosen: { id: string }[]) {
		try {
			for (const tag of chosen) {
				await api.post<Tag[]>(`/loops/${mark.id}/tags`, { body: { tag_id: tag.id, add: true } });
			}
			// The tile draws the mark's tags, so the wall re-reads rather than being patched: the
			// same signal a rename sends.
			libraryChanges.changed();
		} catch {
			toasts.show("Those tags couldn't be added", { tone: 'error' });
		}
	}

	/* Turning an older mark into the clip it should have been.
	 *
	 * This is offered on exactly the marks that are not already a clip: `whole` is false only for
	 * a stretch of something longer, a mark that was never cut as a clip of its own.
	 * Offering it on every mark would make it a second path to something the press in the player
	 * already did.
	 *
	 * A wall where some rows are clips and some are marks behaves two ways for one gesture (an
	 * older mark opens its whole video with two handles on the bar), and nobody using it knows
	 * which kind they are about to press.
	 *
	 * ## It is the same call the player makes
	 *
	 * `clipTheStretch` with `asLoop`, which is the whole of Save as Loop. The old mark is not
	 * removed here: the server retires it when the clip's own row lands, so an encode that fails
	 * leaves the mark exactly where it was rather than losing it to a file that never arrived.
	 *
	 * `Save to device` is a different thing: it exports a copy OFF the machine rather than making
	 * one in the library.
	 */
	async function cutTheMark(item: GridItem) {
		const asset = item.asset_id;
		const from = item.start_ms ?? 0;
		const to = item.end_ms ?? 0;
		if (!asset) return;
		const cut = await clipTheStretch(asset, from, to - from, { asLoop: true });
		toasts.show(
			cut.made ? 'Saving it as a clip. This mark goes when the file lands.' : cut.because,
			{ tone: cut.made ? 'success' : 'error' }
		);
	}

	async function saveName(event: SubmitEvent) {
		event.preventDefault();
		const wanted = naming;
		if (!wanted || busy) return;
		busy = true;
		try {
			await api.put(`/loops/${wanted.id}`, { body: { name: named.trim() || null } });
			naming = null;
			named = '';
			// The row on screen is the server's answer to a question this screen no longer holds, so
			// the wall re-reads rather than being patched: the same signal a share or a hide sends.
			libraryChanges.changed();
		} catch {
			toasts.show("That couldn't be renamed", { tone: 'error' });
		} finally {
			busy = false;
		}
	}
</script>

<AssetGrid
	source={LOOP_SOURCE}
	hideVerbs={NOT_HERE}
	extraVerbs={forgetVerbs}
	menuExtraGrouped
	query={asking}
	filesQuery={{}}
	words={CALLED}
	icon="all_inclusive"
	title="Loops"
	empty={settled
		? `No loop or the file it's cut from has a name with "${settled}" in it.`
		: narrowed
			? 'No loops are cut from files that match these filters.'
			: 'No loops yet. Mark the two ends of a stretch in the player, then save it.'}
	pinnable
>
	{#snippet tools()}
		<!-- The box every wall wears, with no Add: a loop is cut in the player from a playing
		     video, so there is nothing an Add could open. -->
		<WallControls
			noun="loop"
			plural="loops"
			bind:term
			onsettled={(typed) => {
				settled = typed.trim();
				words.write(page.url, typed);
			}}
		/>
		{#if naming}
			{@const wanted = naming}
			<form onsubmit={saveName}>
				<Field label="What this loop is called" hideLabel>
					{#snippet control({ id, describedBy })}
						<TextInput
							{id}
							bind:value={named}
							maxlength={120}
							placeholder={wanted.was || 'Name this loop'}
							autocomplete="off"
							{describedBy}
						/>
					{/snippet}
				</Field>
				<Button type="submit" tone="primary" size="small" icon="save" disabled={busy}>Save</Button>
				<Button size="small" onclick={() => (naming = null)}>Cancel</Button>
			</form>
		{/if}
	{/snippet}

	<!-- Says what a loop IS (a clip of its own), does not pretend the older marks are the
	     same thing, and says where the way out of being one is. A sentence describing behaviour
	     that was replaced would be the screen itself telling somebody the opposite of what just
	     happened. -->

	<!-- The one thing that acts on the MARK rather than on the video. `forget` takes the row off the
	     screen without a refetch, exactly as deleting a file does; the video keeps every byte, which
	     is what the wording has to say or somebody will read it as the other thing. -->
	<!-- The loop's own rows, one part of the menu each, in the menu's order: file it, keep a copy,
	     change it. Three kinds of act, so three groups rather than one run of mixed rows. -->
	{#snippet menuExtra(item, grid)}
		{#if session.isAdmin}
			<!-- The MARK, not the video. Named so the row says which, because the shared Tag verb on
			     every other wall means the file and somebody has learned that word here too. -->
			<ContextMenuGroup>
				<ContextMenuItem
					label="Tag this loop"
					icon="shoppingmode"
					onselect={() => tagTheMark(item)}
				/>
			</ContextMenuGroup>
		{/if}
		{#if item.whole === false}
			<!-- Only on a mark of a stretch. A row that already IS its file would be cutting a clip
			     out of a clip, which is a second identical file and a second identical row. -->
			<ContextMenuGroup>
				<ContextMenuItem
					label="Save as a clip"
					icon="content_cut"
					onselect={() => void cutTheMark(item)}
				/>
			</ContextMenuGroup>
		{/if}
		<ContextMenuGroup>
			<ContextMenuItem
				label="Rename this loop"
				icon="edit_square"
				onselect={() => rename(item.id, item.name ?? '')}
			/>
		</ContextMenuGroup>
		<!-- Deleting a loop is not here: it is a verb, so the bar and this menu draw it from one
		     definition, below these rows, and a selection can be deleted in one press. See
		     `forgetVerbs`. -->
	{/snippet}
</AssetGrid>

<!--
	The tags on one mark, in the sheet every other Tag verb opens.

	Named "Tag this loop" on the menu row and titled the same here, because on every other wall the
	word means the FILE and somebody has learned it. What it is NOT is a differently-shaped box: the
	sheet, the filtering, what Enter does and how a new tag gets made are the shared component's, so
	there is one answer to each of those rather than a second one living here.
-->
{#if tagging}
	{@const mark = tagging}
	<PickDialog
		bind:open={() => tagging !== null, closeTagging}
		title="Tag this loop"
		subject={mark.on.length
			? `The moment, not the whole video. Cut from ${mark.label}. Already on it: ` +
				`${mark.on.map((one) => one.name).join(', ')}.`
			: `The moment, not the whole video. Cut from ${mark.label}. Nothing on it yet.`}
		choices={offerable}
		placeholder="Tag name"
		createLabel="Create"
		kind="tag"
		confirmLabel={tagConfirmLabel}
		onpick={(chosen) => void putOn(mark, chosen)}
		oncreate={(name) => tagStore.create(name)}
	/>
{/if}

<style>
	/* The rename box, and nothing else. Everything about the wall belongs to the grid. */
	form {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
	}
</style>
