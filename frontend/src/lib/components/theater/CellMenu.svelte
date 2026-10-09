<script lang="ts">
	/* DRESSED BY: .ui-menu (ContextMenu owns the surface these rows are drawn on, and
	   ContextMenuItem owns the row: this file declares what the rows SAY and hands them over). */

	/*
	 * Everything a cell can be told to do, as rows for the app's one `ContextMenu`, portalled into
	 * the fullscreen box so it is painted there.
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
		onpick: () => void;
		ontimer: () => void;
	}

	let { wall, cell, index, onpick, ontimer }: Props = $props();

	const silent = $derived(wall.masterMuted || cell.muted);
	const held = $derived(cell.paused || wall.paused);

	/* Save is the shared verb, through `FileVerbs`: the browser's own menu is refused on a cell. */
	const playing = $derived(cell.playing);

	/* Where a cell's file can be put, from the same declaration every menu grows it from. */
	const nothingPicked = new Selection();

	const showing = $derived(playing ? [playing] : []);

	/*
	 * The cell's copy moves on write, guarded on the id since the run advances
	 * (`Surroundings.setState`).
	 */
	function remember(id: string, state: OpinionPatch): void {
		const file = cell.playing;
		if (!file || file.id !== id) return;
		file.favorite = state.favorite;
		file.rating = state.rating;
	}

	/* The file's name, through `copyText`, with a toast since the menu closes on the press. */
	const filename = $derived(playing?.original_filename?.trim() ?? '');

	async function copyFilename(name: string): Promise<void> {
		if (await copyText(name)) toasts.show('Filename copied', { tone: 'success' });
		// Nothing landed, and silence would read as it having worked.
		else toasts.show("That name couldn't be copied", { tone: 'error' });
	}
</script>

<!-- Five groups: transport, sound, what the cell plays, the file, the wall. -->
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
	<ContextMenuItem label="Move on after" icon="timer" onselect={ontimer} />
	<!-- A submenu: a run of near-identical rows belongs under the one thing they share. -->
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
	<FileVerbs items={showing} around={{ selection: nothingPicked, setState: remember }}>
		{#snippet children(verbs)}
			{@const on = [playing.id]}
			{@const all = verbs.menu(on, playing.id)}
			{@const save = all.find((one) => one.id === 'save')}
			{@const addTo = all.find((one) => one.id === 'add')}
			{@const rating = all.find((one) => one.id === 'rate')}
			<ContextMenuGroup>
				{#if addTo}
					<!-- The declared door, drawn by the one renderer every Add to uses. -->
					<VerbMenuItems verbs={[addTo]} ids={on} subjectId={playing.id} />
				{/if}
				<ContextMenuItem
					label="Copy filename"
					icon="content_copy"
					disabled={filename === ''}
					onselect={() => void copyFilename(filename)}
				/>
				{#if rating}
					<VerbMenuItems verbs={[rating]} ids={on} subjectId={playing.id} />
				{/if}
				{#if save}
					<VerbMenuItems verbs={[save]} ids={on} subjectId={playing.id} />
				{/if}
			</ContextMenuGroup>
		{/snippet}
	</FileVerbs>
{/if}

<!-- Growing the wall and the strip from the cell's own menu. -->
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
		<!-- One more cell: the next shape up, or the strip at the largest. -->
		<ContextMenuItem label="Add a cell" icon="add" onselect={() => wall.spawn()} />
		{#if wall.centerStage}
			<ContextMenuItem
				label="Close the strip"
				icon="view_array"
				onselect={() => wall.closeStrip()}
			/>
		{/if}

		<ContextMenuItem
			label="Duplicate cell"
			icon="file_copy"
			onselect={() => wall.duplicate(index)}
		/>

		<!-- A copy to the strip (`Wall.sendToStrip`); dimmed at nine rather than hidden. -->
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
