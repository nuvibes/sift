<script lang="ts">
	/* DRESSED BY: .ui-menu (ContextMenu owns the surface these rows are drawn on, and
	   ContextMenuItem owns the row: this file declares what the rows SAY and hands them over). */

	/*
	 * Everything a cell can be told to do, as rows for the app's one menu.
	 *
	 * `ContextMenu` owns every right-click in this application, and a second hand-built menu would
	 * be a second implementation of its most-used surface. The cases for one do not hold:
	 *
	 * - A modal menu leaves the page unable to take a pointer: that is what a modal menu is for,
	 *   since the wall behind an open menu should not take clicks.
	 * - Only the fullscreen element's own contents are painted: the shared menu takes `portalTo`,
	 *   as `Select` does, and is handed the box that fills the window, so it is drawn inside what the
	 *   browser is painting.
	 * - Motion: the arrival animation is on `.ui-menu`, so every right-click in Sift arrives the
	 *   same way.
	 *
	 * What is bought is everything a menu is mostly made of: arrow keys, typeahead, focus returning
	 * to what opened it, the flip when there is no room below, submenus that do not fire off a
	 * pointer crossing them, and a right-click on an open menu behaving.
	 */
	import ContextMenuItem from '$lib/components/common/ContextMenuItem.svelte';
	import ContextMenuGroup from '$lib/components/common/ContextMenuGroup.svelte';
	import { FileVerbs, Selection, VerbMenuItems } from '$lib/components/common';
	import { copyText } from '$lib/shell/clipboard';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { ASPECTS } from '$lib/theater/aspects';
	import type { Cell } from '$lib/theater/cell.svelte';
	import type { OpinionPatch } from '$lib/library/changes.svelte';
	import type { Wall } from '$lib/theater/wall.svelte';
	import { ACTS } from '$lib/player/acts';

	interface Props {
		wall: Wall;
		cell: Cell;
		index: number;
		/** Open the panel that sets what this cell draws from. */
		onpick: () => void;
		/** Open the field that says how long this cell holds one file. */
		ontimer: () => void;
	}

	let { wall, cell, index, onpick, ontimer }: Props = $props();

	const silent = $derived(wall.masterMuted || cell.muted);
	const held = $derived(cell.paused || wall.paused);

	/* Save is the shared `save` verb, since the browser's own menu (and its Save video as) is refused
	   on a cell; through `FileVerbs`, so the verb's refusals come with it. */
	const playing = $derived(cell.playing);

	/*
	 * Where a cell's file can be put, off the same builder every other menu in Sift grows it from:
	 * a wall runs for an hour, and what somebody wants to do with what comes up is put it on a
	 * collection, under a person, or on a tag.
	 *
	 * Through `FileVerbs` and `menu` rather than a list written here: the declaration carries the
	 * five lists (the handlers `FileVerbs` writes as `places`), and a copy here would fall behind
	 * when a sixth wall is added.
	 *
	 * `items` carries the file this menu was opened on. A cell fills its run from `GET /assets`,
	 * whose `AssetSummary` carries the heart and the stars, and the cell's `Playable` keeps them
	 * (see `cell.svelte.ts`), so the heart and the stars word themselves truthfully with nothing
	 * fabricated.
	 */
	const nothingPicked = new Selection();

	/** The one file this menu is about, as the list a verb looks its subject up in. */
	const showing = $derived(playing ? [playing] : []);

	/*
	 * Move the cell's own copy the moment a heart or a star is written, before the server answers.
	 *
	 * The same contract every wall in Sift signs (`Surroundings.setState`), and a cell needs it for
	 * the same reason a tile does: the menu closes on the press, and the next opening reads whatever
	 * the cell is holding. Without it the heart would say Favorites again over a file it had just
	 * favourited, until the run moved on.
	 *
	 * Guarded on the id because a run advances: a slow write coming back after the cell has moved to
	 * the next file must not paint its answer onto a different one.
	 */
	function remember(id: string, state: OpinionPatch): void {
		const file = cell.playing;
		if (!file || file.id !== id) return;
		file.favorite = state.favorite;
		file.rating = state.rating;
	}

	/*
	 * The name of what this cell is playing, on the clipboard: to search for it, paste it into a
	 * message, or find it on disk.
	 *
	 * Through `copyText` and never `navigator.clipboard` directly: that object does not exist on a
	 * plain-http address, which is how a self-hosted Sift is normally reached, and `$lib/shell/clipboard`
	 * owns the fallback. The same call the filename on a file's own screen makes.
	 *
	 * A toast rather than a "Copied" tooltip: selecting a menu row closes the menu, so the row that
	 * would say "Copied" is gone. Keeping this one row open after a copy would make it behave
	 * unlike its neighbours and leave a menu to dismiss; a toast is the app's rule for something
	 * that happened out of sight, which after the menu has gone is where it happened.
	 *
	 * The name is the file's own, falling back to nothing: a file with no name on disk has nothing
	 * to copy, and the row is disabled rather than copying an id nobody asked for.
	 */
	const filename = $derived(playing?.original_filename?.trim() ?? '');

	async function copyFilename(name: string): Promise<void> {
		if (await copyText(name)) toasts.show('Filename copied', { tone: 'success' });
		// The one case worth a sentence: nothing landed, and silence would read as it having worked.
		else toasts.show("That name couldn't be copied", { tone: 'error' });
	}
</script>

<!--
	Five parts, each a group, so the lines between them are the groups' own: the transport, the
	sound, what this cell plays and how, the file it is playing now, and the wall around the cell.
-->
<ContextMenuGroup>
	<ContextMenuItem
		label={held ? ACTS.play : ACTS.pause}
		icon={held ? 'play_arrow' : 'pause'}
		onselect={() => {
			// The wall's own hold wins over a cell's: pressing play while everything is stopped means
			// start, and starting one cell out of a stopped wall is not a thing to guess at.
			if (wall.paused) wall.togglePause();
			else cell.paused = !cell.paused;
		}}
	/>
	<ContextMenuItem
		label={ACTS.previous}
		icon="skip_previous"
		disabled={!cell.hasBack}
		onselect={() => void cell.back()}
	/>
	<ContextMenuItem label={ACTS.next} icon="skip_next" onselect={() => void cell.advance()} />
</ContextMenuGroup>

<ContextMenuGroup>
	<ContextMenuItem label="Hear only this" icon="hearing" onselect={() => wall.solo(index)} />
	<ContextMenuItem
		label={silent ? ACTS.unmute : ACTS.mute}
		icon={silent ? 'volume_off' : 'volume_up'}
		onselect={() => wall.toggleMute(index)}
	/>
</ContextMenuGroup>

<ContextMenuGroup>
	<!-- The menu names the FIELD it opens rather than "how it plays": it opens one field, and the
	     other settings of how a cell plays are drawer icons. -->
	<ContextMenuItem label="Move on after" icon="timer" onselect={ontimer} />
	<!--
		The shape of this one cell, as a row that opens out.

		A submenu rather than seven rows in the main list, which is the rule `ContextMenuItem` states:
		a run of near-identical rows belongs under the one thing they have in common, and the side is
		where a menu has room.
	-->
	<ContextMenuItem label="Shape" icon="aspect_ratio">
		{#each ASPECTS as choice (choice.id)}
			<ContextMenuItem
				label={choice.label}
				icon={cell.aspect === choice.id ? 'check' : undefined}
				onselect={() => (cell.aspect = choice.id)}
			/>
		{/each}
	</ContextMenuItem>
	<ContextMenuItem label="What it plays" icon="filter_alt" onselect={onpick} />
</ContextMenuGroup>

{#if playing}
	<!-- The five walls this file can go on, each opening out into its own list. See the head of the
	     script: the group is read off the declaration, and only the rows carrying a list are kept.
	     Save is read off the same declaration. See `playing` in the script. -->
	<FileVerbs items={showing} around={{ selection: nothingPicked, setState: remember }}>
		{#snippet children(verbs)}
			{@const on = [playing.id]}
			{@const all = verbs.menu(on, playing.id)}
			{@const save = all.find((one) => one.id === 'save')}
			{@const addTo = all.find((one) => one.id === 'add')}
			{@const rating = all.find((one) => one.id === 'rate')}
			<ContextMenuGroup>
				{#if addTo}
					<!-- The declared door itself, drawn by the one renderer every Add to goes through,
					     so its word, its glyph and its five lists are the ones every other door shows.
					     The heart is the sixth child (see `grid/verbs.ts`). -->
					<VerbMenuItems verbs={[addTo]} ids={on} subjectId={playing.id} />
				{/if}
				<!-- The name on the clipboard. Beside Save because the two are the same request at
				     different sizes: keep the file, or keep what it is called. See `copyFilename`. -->
				<ContextMenuItem
					label="Copy filename"
					icon="content_copy"
					disabled={filename === ''}
					onselect={() => void copyFilename(filename)}
				/>
				{#if rating}
					<!-- The stars are a row of their own rather than a child of the group, because what
					     they change is this account's opinion and not where the file is filed. -->
					<VerbMenuItems verbs={[rating]} ids={on} subjectId={playing.id} />
				{/if}
				{#if save}
					<VerbMenuItems verbs={[save]} ids={on} subjectId={playing.id} />
				{/if}
			</ContextMenuGroup>
		{/snippet}
	</FileVerbs>
{/if}

<!--
	Growing the wall, and the strip, from the menu the cell already has.

	Center stage is a layout, so the strip usually arrives with it, but a wall in any other shape
	can have one, and a strip is the only part of a wall the layout picker cannot take away once it
	is there. These are that: one more cell, one fewer, or no strip at all.
-->
<ContextMenuGroup>
	{#if wall.isPreview(index)}
		<ContextMenuItem
			label="Move to the wall"
			icon="open_in_full"
			onselect={() => wall.sendToFocus(index)}
		/>
		<ContextMenuItem
			label="Remove this preview"
			icon="close"
			onselect={() => wall.dropPreview(index)}
		/>
		<ContextMenuItem label="Close the strip" icon="view_array" onselect={() => wall.closeStrip()} />
	{:else}
		<!--
			ONE MORE CELL, which is a bigger wall until the wall is as big as it gets: the wall steps
			up to the next shape the picker offers, and a wall already at the largest of them puts the
			new cell in the strip, so the row means the same thing throughout, "one more picture".
		-->
		<ContextMenuItem label="Add a cell" icon="add" onselect={() => wall.spawn()} />
		{#if wall.centerStage}
			<ContextMenuItem
				label="Close the strip"
				icon="view_array"
				onselect={() => wall.closeStrip()}
			/>
		{/if}

		<!--
			Another one of this, beside it. Enabled even at the ceiling: a full wall puts the copy in
			the strip, so the press always works.
		-->
		<ContextMenuItem
			label="Duplicate cell"
			icon="file_copy"
			onselect={() => wall.duplicate(index)}
		/>

		<!--
			Sends a copy of this feed to the strip. It COPIES rather than moves, and `Wall.sendToStrip`
			is where that is argued: a place given up is a hole, and closing a hole means reshaping
			the wall under somebody who only asked for a preview.

			Disabled at the wall's nine rather than hidden: a row that vanishes leaves somebody looking
			for something that was there a moment ago, while a dim one says the wall is full.
		-->
		<ContextMenuItem
			label="Move to the strip"
			icon="arrow_downward"
			disabled={!wall.canPreview}
			onselect={() => wall.sendToStrip(index)}
		/>

		<ContextMenuItem
			label="Remove this feed"
			icon="close"
			disabled={!wall.canShrink}
			onselect={() => wall.remove(index)}
		/>
	{/if}
</ContextMenuGroup>
