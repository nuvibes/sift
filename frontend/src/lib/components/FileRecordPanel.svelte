<script lang="ts">
	/*
	 * A file's record under its picture: the heading outside the panel, at the edge the strips above
	 * put theirs, then the panel holding three panes behind a strip: what a person wrote (About), what
	 * the file is (Media), and what has happened to it (History). The split is the registry's own
	 * (`group` on the field). Folding the section closes the form with it.
	 */
	/* NOT ON THE GALLERY: a piece of `AssetView`, exempt as the dynamic host. */
	import Copyable from '$lib/components/record/Copyable.svelte';
	import RecordGrid from '$lib/components/record/RecordGrid.svelte';
	import MusicSource from '$lib/components/record/MusicSource.svelte';
	import Disagreements from '$lib/components/record/Disagreements.svelte';
	import PathText from '$lib/components/PathText.svelte';
	import {
		Button,
		Empty,
		Fold,
		HistoryList,
		Note,
		Panel,
		Problem,
		Scroller,
		Tabs,
		type TabChoice
	} from '$lib/components/common';
	import { session } from '$lib/shell/session.svelte';
	import type { components } from '$lib/api/schema';
	import {
		appliesToKind,
		fields,
		filledIn,
		inGroup,
		type FieldGroup
	} from '$lib/entity/records.svelte';
	import { waitingText } from '$lib/entity/reconcile.svelte';
	import type { FileHistory } from './file-history.svelte';
	import { counted } from '$lib/entity/entity-counts';

	interface Props {
		file: components['schemas']['AssetDetail'];
		/** The file showing, as a value that moves only when the file does. */
		shownId: string;
		/** The one Site a song's name could have come from, or null. */
		onlySite: string | null;
		/** Which pane is showing. */
		tab: string;
		/** Whether the record is a form. */
		editing: boolean;
		/** The field somebody pressed Add on, which the form opens on. */
		adding: string | null;
		/** The form's id, so Save can stand outside it. */
		formId: string;
		values: Record<string, unknown>;
		thread: FileHistory;
		/** Which file the view was asked for, which a thread's answer must still be about. */
		current: () => string;
		onchoose: (tab: string) => void;
		onedit: (field: string | null) => void;
		onclose: () => void;
		onsave: (draft: Record<string, unknown>) => Promise<void>;
		/** Read the whole file again. */
		onreload: () => Promise<void>;
	}

	let {
		file,
		shownId,
		onlySite,
		tab = $bindable(),
		editing,
		adding,
		formId,
		values,
		thread,
		current,
		onchoose,
		onedit,
		onclose,
		onsave,
		onreload
	}: Props = $props();

	/* How many things are behind each tab, by the registry's own `filledIn`, and `appliesToKind` as in
	   the grid, so a tab never counts a row it does not draw. */
	function filledCount(group: FieldGroup): number {
		return fields
			.drawn('asset')
			.filter(
				(one) =>
					inGroup(one, group) && appliesToKind(one, file.media_type) && filledIn(values[one.key])
			).length;
	}

	const mediaCount = $derived(filledCount('media'));

	/* Media and History are closed off while the record is a form, which lives in About. */
	const tabs = $derived<TabChoice[]>([
		{ id: 'about', label: 'About', count: filledCount('record') },
		{ id: 'media', label: 'Media', count: mediaCount, disabled: editing },
		{
			id: 'history',
			label: 'History',
			// No number until the thread has landed (read once the picture is up), then its total: the
			// whole history, never the length of the page drawn, and never a number taken back.
			count: thread.total ?? undefined,
			/* The mark where a stash-box disagrees, naming the box: the panel that settles it is in this tab. */
			attention:
				(file.disagreements ?? 0) > 0
					? waitingText(file.disagreements ?? 0, file.disagreement_boxes ?? [])
					: undefined,
			disabled: editing,
			/* The one pane whose length is not its content's: capped, and it scrolls (`Tabs`). */
			scrolls: true
		}
	]);
</script>

<Fold
	section
	weight={450}
	summary="File info"
	remember="sift.file.fold.record"
	ontoggle={(open) => {
		if (!open) onclose();
	}}
>
	<!-- Edit at the start of the heading's row; Cancel and Save at its end while the form is open,
	     reaching the form through `form=`, the way back first and the act last. -->
	{#snippet leading()}
		{#if session.isAdmin && !editing}
			<Button icon="edit" tone="ghost" size="small" onclick={() => onedit(null)}>Edit</Button>
		{/if}
	{/snippet}
	{#snippet actions()}
		{#if session.isAdmin && editing}
			<Button type="button" tone="ghost" size="small" onclick={onclose}>Cancel</Button>
			<Button type="submit" form={formId} tone="primary" size="small" icon="save">Save</Button>
		{/if}
	{/snippet}
	<Panel inset="md" corner="lg">
		<div class="record">
			<!-- One grid in both modes, so the panel does not jump: a value becomes a box where it stood,
			     the file's name among them. -->
			<div class="panes">
				<Tabs
					size="panel"
					look="segmented"
					{tabs}
					bind:value={tab}
					onchange={onchoose}
					label="What this file's record says"
				>
					{#snippet pane(which)}
						{#if which === 'about'}
							<Scroller>
								{#if file.where}
									<div class="location">
										<span class="location-label">Location</span>
										<p class="location-path">
											<Copyable text={file.where} what="That location">
												<PathText path={file.where} />
											</Copyable>
										</p>
									</div>
								{/if}
								<!-- Every field, empty ones included: an empty row draws "Add", which opens the
								     form on that row. -->
								<RecordGrid
									subject="asset"
									{formId}
									{values}
									{editing}
									group="record"
									onadd={session.isAdmin ? (key) => onedit(key) : undefined}
									focusField={adding}
									label="File info"
									{onsave}
									oncancel={onclose}
								>
									<!-- Where the song's name came from, under the Music it explains. -->
									{#snippet under(one)}
										{#if one.key === 'music'}
											<MusicSource
												source={file.music_source}
												from={file.music_from}
												site={onlySite}
												onundo={file.music_undo
													? () => void thread.undo(file.music_undo!, onreload)
													: undefined}
												undoing={thread.undoing !== null && thread.undoing === file.music_undo?.id}
											/>
										{/if}
									{/snippet}
								</RecordGrid>
							</Scroller>
						{:else if which === 'media'}
							<Scroller>
								<!-- Only what the file answers (`appliesToKind` drops a still's frame rate), and
								     read-only: nothing measured is an opinion. -->
								{#if mediaCount === 0}
									<Empty scope="block" icon="info"
										>Nothing has been measured off this file yet.</Empty
									>
								{:else}
									<RecordGrid
										subject="asset"
										{values}
										group="media"
										mediaType={file.media_type ?? null}
										onlyFilled
										label="What this file is"
									/>
								{/if}
								<!-- A standing verdict, not a failure: why this file can never be compared. -->
								{#if file.fingerprint_verdict}
									<Note tone="caution">
										Sift couldn't read this file's frames, so it has no fingerprint left and can't
										be compared again &mdash;
										{file.fingerprint_verdict}
									</Note>
								{/if}
							</Scroller>
						{:else}
							<Scroller>
								<!-- Oldest first, newest at the bottom, through the one row every history uses.
								     A stash-box disagreeing with this file sits at the top of its thread, as on
								     every entity's History tab, an admin's to settle. -->
								{#if session.isAdmin}
									<Disagreements
										subject="asset"
										localId={shownId}
										onwritten={() => void onreload()}
									/>
								{/if}
								{#if thread.failed}
									<Problem message="That history couldn't be read." />
								{:else if thread.events === null}
									<Empty scope="block" busy>Reading what happened to this file</Empty>
								{:else}
									{#if thread.mayGoFurther}
										<!-- A word at the thread's cut end. -->
										<Button
											tone="link"
											busy={thread.widening}
											onclick={() => void thread.showEarlier(current)}
										>
											Show {counted(thread.nextEarlier)} earlier
										</Button>
									{/if}
									<HistoryList
										events={thread.events}
										undoing={thread.undoing}
										onundo={(event) => {
											if (event.undo) void thread.undo(event.undo, onreload);
										}}
										emptyText="Nothing has been recorded about this file yet."
									/>
								{/if}
							</Scroller>
						{/if}
					{/snippet}
				</Tabs>
			</div>
		</div>
	</Panel>
</Fold>

<style>
	/* The panel's one item as a row: a track the panes are bounded by, as `Tabs` does inside. */
	.record {
		display: grid;
		grid-template-rows: minmax(0, 1fr);
		min-block-size: 0;
	}

	/* A grid of one row, so a `Scroller` has a track to resolve its height against. */
	.panes {
		display: grid;
		grid-template-rows: minmax(0, 1fr);
		min-block-size: 0;
	}

	/* The file's location over the record: the path wraps anywhere, and pressing it copies it. */
	.location {
		display: grid;
		align-items: center;
		gap: var(--space-1) var(--space-3);
		margin-bottom: var(--space-3);
	}

	.location-label {
		grid-column: 1 / -1;
		font: var(--text-label);
		color: var(--sift-ink-3);
	}

	.location-path {
		margin: 0;
		font: var(--text-body-sm);
		overflow-wrap: anywhere;
	}
</style>
