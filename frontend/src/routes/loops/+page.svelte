<script lang="ts">
	/* Loops: every stretch somebody marked, drawn by the same grid Browse and Favorites are. */
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

	/* Whether the bar is filtering this wall, which decides what an empty one says. */
	const FILTERING: ReadonlySet<string> = new Set<string>(['q', ...FIELDS]);
	const narrowed = $derived([...page.url.searchParams.keys()].some((name) => FILTERING.has(name)));

	/* The search box: the marks whose OWN name holds the words, asked of `/loops` as `called`. */
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

	/* What this list can and cannot do, said out loud rather than discovered. */

	/* The verbs a mark does not offer. */
	const NOT_HERE = ['delete', 'move', 'compress', 'edit', 'tag'] as const;

	/* Tagging is left out of the shared verbs and offered below instead, and that is the one
	 * place this wall deliberately disagrees with every other. */

	/* Naming a mark. A form above the wall rather than a dialog, which is where the Collections
	 * and Photo Sets walls put the same thing: choosing the row from its own menu fills the box,
	 * and Save writes it. */
	let naming = $state<{ id: string; was: string } | null>(null);
	let named = $state('');
	let busy = $state(false);

	function rename(id: string, was: string) {
		naming = { id, was };
		named = was;
	}

	/* Tagging the mark. */
	let tagging = $state<{
		id: string;
		label: string;
		on: readonly { id: string; name: string }[];
	} | null>(null);

	/* The shared sheet offers what the tag store holds, and the store is loaded by whoever needs
	   it. */
	/* `loaded` is the dependency; `loading` is checked inside `untrack` and is not one. */
	$effect(() => {
		const stale = !tagStore.loaded;
		untrack(() => {
			if (stale && !tagStore.loading) void tagStore.load();
		});
	});

	/* Deleting loops, as a VERB rather than a menu row. */
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

	/* Everything not already on this mark, which is what the shared sheet expects: it offers a
	   list to tick, and offering a tag the mark already carries is a row that does nothing. */
	const offerable = $derived(
		tagStore.items
			.filter((tag) => !(tagging?.on ?? []).some((on) => on.id === tag.id))
			.map((tag) => pickRow('tag', tag))
	);

	/* One request per tag, because that is the shape the endpoint has: it takes one tag and says
	   whether it goes on or comes off. */
	/* Cleared when the sheet closes, whichever way it was closed: the button, Escape or a click
	   beside it. */
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

	/* Turning an older mark into the clip it should have been. */
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

	<!--
		Says what a loop IS (a clip of its own), does not pretend the older marks are the same thing,
		and says where the way out of being one is.
	-->

	<!-- The one thing that acts on the MARK rather than on the video. -->
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

<!-- The tags on one mark, in the sheet every other Tag verb opens. -->
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
