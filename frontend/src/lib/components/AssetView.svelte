<script lang="ts">
	/* One file, drawn; frameless, so the panel over a wall and a page of its own share it. */
	import { bridge } from '$lib/bridge';
	import { abLoop } from '$lib/player/loop.svelte';
	import { onMount, untrack } from 'svelte';
	import Copyable from '$lib/components/record/Copyable.svelte';
	import { offerViewer } from '$lib/remote/offer.svelte';
	import { judge, tally } from '$lib/library/judgement.svelte';
	import { api, ApiError, isMissing } from '$lib/api/client';
	import AssetDetailControls from '$lib/components/AssetDetailControls.svelte';
	import { thumbUrl } from '$lib/entity/art';
	import { runNowVerb, type Verb } from '$lib/grid/verbs';
	import { loadRunNowGroups, runNow, runNowGroups } from '$lib/jobs/run-now.svelte';
	import FileActions from '$lib/components/FileActions.svelte';
	import FileRecordPanel from '$lib/components/FileRecordPanel.svelte';
	import FileBandRows from '$lib/components/FileBandRows.svelte';
	import { FileBand } from '$lib/components/file-band.svelte';
	import { FileHistory } from '$lib/components/file-history.svelte';
	import {
		Button,
		Drawer,
		Empty,
		Separator,
		FileVerbs,
		MenuButton,
		Selection,
		SettingLink,
		SharingMark,
		SplitButton,
		Tooltip,
		VerbMenuItems
	} from '$lib/components/common';
	import PlayerBar from '$lib/components/player/PlayerBar.svelte';
	import StageNotice from '$lib/components/player/StageNotice.svelte';
	import { noticeLabel, noticeWords } from '$lib/player/facts';
	import type { PlaybackPlan } from '$lib/player/playback';
	import { newSitting } from '$lib/player/sitting.svelte';
	import { bellsRung, panelPlace, takeRecord } from '$lib/player/asset-view';
	import { run } from '$lib/player/run.svelte';
	import { dwell } from '$lib/player/dwell.svelte';
	import MediaStage from '$lib/components/player/MediaStage.svelte';
	import { swipeBetween } from '$lib/components/player/swipe';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import { finger } from '$lib/components/player/finger.svelte';
	import Player from '$lib/components/player/Player.svelte';
	import StillView from '$lib/components/player/StillView.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { fields, SaveRefused } from '$lib/entity/records.svelte';
	import FacesInThis from '$lib/components/FacesInThis.svelte';
	import EnrichmentMarks from '$lib/components/entity/EnrichmentMarks.svelte';
	import LooksLikeThis from '$lib/components/LooksLikeThis.svelte';
	import SameMusic from '$lib/components/SameMusic.svelte';
	import {
		HISTORY_SETTLE_MS,
		jobChanges,
		libraryChanges,
		rereadOnHistoryChange,
		whenChanged
	} from '$lib/library/changes.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { saveToDevice } from '$lib/capture/copy-out';
	import type { components } from '$lib/api/schema';
	import { matches } from '$lib/shell/shortcuts';
	import { reveal } from '$lib/shell/motion.svelte';
	import {
		popoutExpanded,
		recallInterfaceState,
		rememberPopoutExpanded
	} from '$lib/shell/interface-state.svelte';

	/** How the player is playing this file, handed up so the mark in the corner can say why. */
	let plan = $state<PlaybackPlan | null>(null);

	export type AssetDetail = components['schemas']['AssetDetail'];

	interface Props {
		id: string;
		/** Told what came back, for a frame that wants to name itself after it. */
		onloaded?: (asset: AssetDetail) => void;
		/** Move to the next or previous item in whatever this was opened from, when there is one. */
		onnext?: () => void;
		onprevious?: () => void;
		/** Put this view away, however it is being shown: a stroke down the picture on a phone. */
		onclose?: () => void;
		/** Where a clip goes when it plays to its end and "Play through" is on. Skips stills. */
		onplayedthrough?: () => void;
		/** Whether the run brought this file up rather than a press: only then does a picture rest. */
		reachedByRun?: boolean;
		/** A clip's first frame is playing. */
		onstarted?: () => void;
		/** Open another file, one this list may not hold; `runs` says whether it plays. */
		onopen?: (id: string, runs: boolean) => void;
		/** This file was deleted from the menu below; stepping on or closing is the frame's call. */
		ongone?: (id: string) => void;
		/** A moment to open at, in milliseconds, for a link to one (a pile of faces, a search). */
		startAt?: number | null;
		/** Where the stretch ends, for something opened from a saved loop: it repeats that range. */
		playUntil?: number | null;
	}

	let {
		id,
		onloaded,
		onnext,
		onprevious,
		onclose,
		onplayedthrough,
		reachedByRun = false,
		onstarted,
		onopen,
		ongone,
		startAt = null,
		playUntil = null
	}: Props = $props();

	/* A moment asked for by pressing a face: an object, so the same face twice is a new request. */
	let seekTo = $state<{ ms: number } | null>(null);
	$effect(() => {
		void id;
		seekTo = startAt === null ? null : { ms: startAt };
	});

	/* A saved loop arms the A-B pair, in seconds; cleared for a file with none, or it would carry on. */
	$effect(() => {
		const from = startAt;
		const to = playUntil;
		const clip = id;
		untrack(() => {
			if (from === null || to === null) {
				if (abLoop.owns(clip)) abLoop.clear();
				return;
			}
			abLoop.arm(clip, from / 1000, to / 1000);
		});
	});

	/* The arrows step through the run, not while typing or with a modifier; Shift is the step. */
	function stepping(event: KeyboardEvent): boolean {
		if (event.ctrlKey || event.metaKey || event.altKey) return false;
		const element = event.target as HTMLElement | null;
		if (!element?.tagName) return true;
		if (element.isContentEditable) return false;
		return !['INPUT', 'TEXTAREA', 'SELECT'].includes(element.tagName);
	}

	/* Shift and an arrow steps; a bare arrow too, but only where there is nothing to seek. */
	function onKeydown(event: KeyboardEvent) {
		/* Ctrl-S saves what is on screen, of any kind: this view covers all three kinds and already
		   holds the file, where the corner panel stands down while it is up. */
		if (matches(event, 'player.save')) {
			if (!asset) return;
			// Or the shell saves its own HTML into the downloads folder instead.
			event.preventDefault();
			void saveToDevice(asset);
			return;
		}
		const forward = matches(event, 'view.next');
		if (!forward && !matches(event, 'view.previous')) return;
		if (!stepping(event)) return;
		// About what is on SCREEN: a bare arrow belongs to the player where there is one.
		if (!event.shiftKey && asset?.media_type === 'video') return;
		const go = forward ? onnext : onprevious;
		if (!go) return;
		event.preventDefault();
		go();
	}

	let asset = $state<AssetDetail | null>(null);

	/* Moves only when the file does: a re-read record is a new object (`AssetView.flash.test.ts`). */
	const shownId = $derived(asset === null ? '' : asset.id);

	/* Offered to the phone whatever it shows (`offerViewer`), with a favourite and the O counter. */
	let shownClip = $state<ReturnType<typeof Player> | null>(null);
	let shownStill = $state<ReturnType<typeof StillView> | null>(null);
	const offeredMarks = judge(() => ({
		id: asset?.id ?? '',
		favorite: asset?.favorite ?? false,
		rating: asset?.rating ?? null
	}));
	const offeredCount = tally(() => ({ id: asset?.id ?? '', o_count: asset?.o_count ?? 0 }));
	onMount(() =>
		offerViewer(
			() => (asset?.media_type === 'video' ? shownClip?.remote() : shownStill?.remote()) ?? null,
			() =>
				asset === null || asset.concealed
					? {}
					: {
							'player.favorite': ({ key, value }) => {
								if (key !== null) return false;
								offeredMarks.setFavorite(value === null ? !offeredMarks.favorite : value === 1);
								return true;
							},
							'player.count': ({ key }) => {
								if (key !== null) return false;
								offeredCount.more();
								return true;
							}
						},
			// And where the two stand, so the phone's heart and O counter read what the desk's do.
			() =>
				asset === null || asset.concealed
					? {}
					: { favorite: offeredMarks.favorite, count: offeredCount.count }
		)
	);

	/* What the file belongs to, under its picture. See `FileBand`. */
	const band = new FileBand(() => id);

	let failed = $state(false);

	/* The name on disk, else the one it came in under: all there is when no copy can be read. */
	const label = $derived(asset ? (asset.filename ?? asset.original_filename ?? null) : null);

	/* The record form's id, unique per view, so Save can be a submit button standing outside it. */
	const viewId = $props.id();
	const RECORD_FORM = `record-${viewId}`;

	let editingRecord = $state(false);

	/** Which field somebody pressed Add on, so the form opens on that row. */
	let addingField = $state<string | null>(null);

	/** The panes, in the order they are drawn. The first is what every opening starts on. */
	const RECORD_TABS = ['about', 'media', 'history'] as const;
	type RecordTab = (typeof RECORD_TABS)[number];

	/* Which pane is showing: this view's own, kept across files inside one opening, not remembered. */
	let recordTab = $state<RecordTab>(RECORD_TABS[0]);

	/** Open a pane, and read the history when its pane is first shown. */
	function showPane(next: RecordTab) {
		recordTab = next;
		if (next === 'history') void thread.load(id, () => id);
	}

	function choosePane(next: string) {
		showPane(next as RecordTab);
	}

	/* What has happened to this file, held here so it survives the pane being folded. */
	const thread = new FileHistory();
	/* Every bell that may move the thread goes through its one settle (`FileHistory.soon`). */
	rereadOnHistoryChange(() => thread.soon(() => id), 0);

	/** Turn the record into boxes, on the pane that has them, optionally on one field. */
	function editRecord(field: string | null = null) {
		addingField = field;
		// Not remembered: opening the form is not somebody choosing what to read.
		showPane('about');
		editingRecord = true;
	}

	function closeRecordForm() {
		editingRecord = false;
		addingField = null;
	}

	const recordValues = $derived.by(() => {
		const file = asset;
		if (!file) return {};

		/* From the registry by key, never a hand-written list; `dimensions` is the one composed row. */
		const held = file as unknown as Record<string, unknown>;
		const out: Record<string, unknown> = {
			dimensions: file.width && file.height ? [file.width, file.height] : null
		};
		for (const one of fields.of('asset')) {
			if (one.key === 'dimensions') continue;
			out[one.key] = held[one.key] ?? null;
		}
		return out;
	});

	/** A box's contents as they are stored: the text, trimmed, or nothing at all when it is empty. */
	function written(value: unknown): string | null {
		const text = typeof value === 'string' ? value.trim() : '';
		return text === '' ? null : text;
	}

	/* Sends only the drawn, editable fields in the draft; a throw keeps `RecordGrid`'s form up. */
	async function saveRecord(draft: Record<string, unknown>) {
		const file = asset;
		if (!file) return;

		const body: Record<string, unknown> = {};
		for (const one of fields.drawn('asset')) {
			if (!one.editable || one.key === 'filename') continue;
			// One field's own tick sends that field alone; the route writes only what it is sent.
			if (!(one.key in draft)) continue;
			body[one.key] =
				one.kind === 'links' || one.kind === 'names'
					? ((draft[one.key] as unknown[]) ?? [])
					: written(draft[one.key]);
		}
		await api.put(`/assets/${file.id}`, { body });

		/* The filename goes through the rename route, the one whose refusals are written once, and
		   only when it changed. AFTER the other fields, so a refused rename never throws away what
		   could be written; the refusal is the server's own sentence (`SaveRefused`). */
		const wanted = String(draft.filename ?? '').trim();
		if (wanted && wanted !== (file.filename ?? '')) {
			try {
				await api.post(`/assets/${file.id}/rename`, { body: { name: wanted } });
			} catch (failure) {
				await load();
				throw new SaveRefused(
					failure instanceof ApiError && failure.detail
						? failure.detail
						: "That file couldn't be renamed."
				);
			}
		}

		editingRecord = false;
		await load();
	}

	/* One fold for everything under the file's row, remembered per account and open until read. */
	let expanded = $state(popoutExpanded());
	onMount(() => {
		void recallInterfaceState().then(() => {
			expanded = popoutExpanded();
		});
	});

	/** The id the control points at, unique per view. */
	const FOLD = `fold-${viewId}`;

	function toggleFold() {
		expanded = !expanded;
		rememberPopoutExpanded(expanded);
	}

	/* On a phone the stroke steps, so Previous and Next stand down; the sections are a sheet. */
	const stepButtons = $derived(!(phoneWidth.yes && finger.yes));
	let detailsOpen = $state(false);
	/* The sheet's sections are drawn once asked for, and kept while it slides away. */
	let detailsDrawn = $state(false);

	function openDetails() {
		detailsDrawn = true;
		detailsOpen = true;
	}

	/* Stepping to another file with the sheet shut forgets the last file's sections. */
	$effect(() => {
		void id;
		untrack(() => {
			if (!detailsOpen) detailsDrawn = false;
		});
	});

	/* Escape shuts the sheet and nothing under it: heard before the frame's own Escape. */
	function shutDetailsFirst(event: KeyboardEvent) {
		if (event.key !== 'Escape' || !detailsOpen) return;
		event.preventDefault();
		detailsOpen = false;
	}

	/** Run task on this file: Importing's passes for one file, the same declared verb every
	 *  surface draws (`$lib/jobs/run-now`). */
	$effect(() => {
		if (session.isAdmin) loadRunNowGroups();
	});
	const running = $derived(runNowVerb(runNowGroups(), (ids, run) => void runNow(ids, run)));

	/** The rows only this screen offers, which hang off whether THIS file can be organized: Rename
	 *  and Undo move, beside the shared verbs in the menu's groups. */
	function ownVerbs(
		file: { media_type: string },
		canOrganize: boolean,
		rename: () => void,
		undo: (() => void) | null
	): Verb[] {
		const verbs: Verb[] = [];
		if (canOrganize) {
			verbs.push({
				id: 'rename',
				label: 'Rename',
				icon: 'edit_square',
				group: 'change',
				run: rename
			});
		}
		if (undo) {
			verbs.push({
				id: 'undo-move',
				label: 'Undo move',
				icon: 'settings_backup_restore',
				group: 'change',
				run: undo
			});
		}
		if (!session.isAdmin) return verbs;
		if (running) verbs.push(running);
		return verbs;
	}

	/* The host wants a selection, and this screen shows one file: an empty one costs nothing. */
	const selection = new Selection();

	/* Follows the id rather than being rebuilt, which would drop out of fullscreen. */
	$effect(() => {
		void id;
		untrack(() => void load());
	});

	/* Re-read in place, never the whole `load`, which blanks; only the queue's chatter settles. */
	let following: ReturnType<typeof setTimeout> | undefined;

	/* The band only where the jobs bell owed it: the library bell reads it on the bell itself. */
	let bandOwed = false;
	function followNow() {
		clearTimeout(following);
		// Not again for a record asked for after the last bell, which already says what it moved.
		if (bellsRung() !== askedAt) void rereadRecord();
		if (bandOwed) void band.load(id);
		bandOwed = false;
	}
	/* The record once a bell has settled: its re-read restarts the sitting's dwell. */
	function followSoon() {
		clearTimeout(following);
		following = setTimeout(followNow, HISTORY_SETTLE_MS);
	}
	whenChanged(libraryChanges, () => (void band.load(id), followSoon()));
	// The thread has its own settle, shared with every other bell it hears.
	whenChanged(jobChanges, () => (thread.soon(() => id), (bandOwed = true), followSoon()));
	$effect(() => () => {
		clearTimeout(following);
		thread.stop();
		band.stop();
	});

	/* The record read again for the same file, put on screen only when the answer moved. */
	let askedAt = -1;
	async function rereadRecord() {
		const wanted = id;
		if (asset === null || asset.id !== wanted) return;
		askedAt = bellsRung();
		try {
			const loaded = await api.get<AssetDetail>(`/assets/${wanted}`);
			if (wanted !== id || asset === null || asset.id !== wanted) return;
			if (JSON.stringify(loaded) === JSON.stringify(asset)) return;
			asset = loaded;
			onloaded?.(loaded);
		} catch (error) {
			// Gone since it was drawn: said as a fresh load says it. Anything else keeps the record.
			if (isMissing(error) && wanted === id) failed = true;
		}
	}

	/* A still's own time on screen (`$lib/player/sitting`): panel and full size can both be up. */
	const sitting = newSitting();

	/* Where a sitting here happens: the panel, over the screen it was opened from (`panelPlace`). */
	const place = $derived(panelPlace(id));

	/* `pagehide` covers leaving the page; the unmount covers moving within the app. */
	onMount(() => {
		const onLeave = () => sitting.end();
		window.addEventListener('pagehide', onLeave);
		return () => {
			window.removeEventListener('pagehide', onLeave);
			sitting.end();
		};
	});

	async function load() {
		const wanted = id;
		failed = false;
		// The history goes with the file: read at once where its pane is open, else after the picture.
		thread.reset();
		if (recordTab === 'history') void thread.load(id, () => id);
		const held = takeRecord(wanted);
		askedAt = bellsRung();
		band.step(() => {
			void thread.load(wanted, () => id);
			// A record found while the last file played is read again only if a bell rang since.
			if (held?.stale) void rereadRecord();
		});
		try {
			const loaded = held?.record ?? (await api.get<AssetDetail>(`/assets/${wanted}`));
			// A slower request for a clip already moved on from must not overwrite a newer one.
			if (wanted !== id) return;
			asset = loaded;
			onloaded?.(loaded);
			/* A still times its own dwell and reports it when left, so a picture counts as viewed. */
			if (loaded.media_type !== 'video') sitting.start(wanted, panelPlace(wanted));
		} catch {
			// A 404 covers both "gone" and "not yours", and says the same thing either way.
			if (wanted === id) failed = true;
		}
	}

	const HIDDEN = 'This one is hidden';
</script>

<!-- The stage stands outside the branch on media type: it is the element held fullscreen. -->
<svelte:window onkeydown={onKeydown} onkeydowncapture={shutDetailsFirst} />

{#if failed}
	<p class="note">Not found.</p>
{:else if !asset}
	<p class="note">Loading&hellip;</p>
{:else}
	{@const file = asset}
	<!-- The end of a hidden file's row, where a clip's bar keeps it, every press dimmed. -->
	{#snippet hiddenTrailing()}
		<Separator vertical />
		{#if !phoneWidth.yes}
			<Tooltip label={HIDDEN}>
				<Button tone="ghost" icon="cadence" aria-label={HIDDEN} disabled />
			</Tooltip>
		{/if}
		<Tooltip label={HIDDEN}>
			<Button tone="ghost" icon="picture_in_picture" aria-label={HIDDEN} disabled />
		</Tooltip>
		<Tooltip label={HIDDEN}>
			<Button tone="ghost" icon="fullscreen" aria-label={HIDDEN} disabled />
		</Tooltip>
	{/snippet}
	{#snippet stageNotice()}
		<!-- `StageNotice`, in `noticeWords`' one rule, as a Theater cell and the corner panel say it. -->
		<StageNotice label={noticeLabel(plan, asset?.playback_repair)}>
			{noticeWords(plan, asset?.playback_repair)}
			<!-- Only here, since a wall has nowhere to send somebody; the link goes to the row itself. -->
			{#if asset?.playback_repair === 'off' && (!plan || plan.route === 'direct')}
				<SettingLink section="performance" setting="performance.repair_playback">
					Turn it on
				</SettingLink>
			{/if}
		</StageNotice>
	{/snippet}

	<!-- `asset` arms the drag out of the window; neither it nor the notice for a file this account
	     may not see, which must not be dragged past the vault. The stroke steps through the run on a
	     phone. -->
	<div
		class="swipe"
		class:fills={phoneWidth.yes}
		{@attach swipeBetween(() => ({
			live: phoneWidth.yes,
			next: onnext,
			previous: onprevious,
			close: onclose
		}))}
	>
		<MediaStage
			notice={!asset.concealed && noticeWords(plan, asset.playback_repair)
				? stageNotice
				: undefined}
			asset={asset.concealed ? undefined : { id: asset.id, filename: label ?? asset.id }}
		>
			{#snippet media()}
				<!-- Asked again: a snippet body is its own scope, so the branch above does not narrow it. -->
				{#if !asset}
					<span></span>
				{:else if asset.concealed}
					<!-- A hidden file keeps the dialog's shape and the whole bar, every control dimmed but the
					     steps through the run. -->
					<div class="concealed">
						<Empty scope="block" icon="visibility_off"
							>This one is hidden. It takes the PIN to see.</Empty
						>
					</div>
					<PlayerBar
						position={0}
						duration={0}
						onseek={() => {}}
						seekable={false}
						sound={false}
						soundWhy={HIDDEN}
						playing={false}
						playable={false}
						playWhy={HIDDEN}
						timed={false}
						onplay={() => {}}
						onback={onprevious}
						onforward={onnext}
						shuffle={{ on: run.shuffle, onpress: () => {}, why: HIDDEN }}
						repeat={{ mode: dwell.mode, onpress: () => {}, why: HIDDEN }}
						keyboard="picture"
						muted={true}
						volume={0}
						onmute={() => {}}
						onvolume={() => {}}
						trayWhy={HIDDEN}
						trailing={hiddenTrailing}
					/>
				{:else if asset.media_type === 'video'}
					<!-- The way onwards is handed in: an asset opened by id knows nothing of the grid. `draggable`
					     only where the stage listens for it. -->
					<Player
						bind:this={shownClip}
						id={shownId}
						onprevious={stepButtons ? onprevious : undefined}
						onnext={stepButtons ? onnext : undefined}
						draggable={bridge.canNativeDrag()}
						poster={thumbUrl(asset)}
						sprite={asset.sprite}
						art={asset.art}
						favorite={asset.favorite}
						rating={asset.rating}
						file={{
							width: asset.width,
							height: asset.height,
							container: asset.container,
							size_bytes: asset.size_bytes,
							vcodec: asset.vcodec,
							acodec: asset.acodec,
							fps: asset.fps,
							bit_depth: asset.bit_depth
						}}
						{onopen}
						{onplayedthrough}
						onstarted={() => (band.pictured(), onstarted?.())}
						{seekTo}
						{place}
						onplan={(next) => (plan = next)}
					/>
				{:else}
					<!-- A photograph or a GIF: no browser demuxes GIF through the media stack, so a `<video>`
					     cannot play one. `file` is the detail itself, so the codecs reach the panel. -->
					<StillView
						bind:this={shownStill}
						id={shownId}
						mediaType={asset.media_type}
						onprevious={stepButtons ? onprevious : undefined}
						onnext={stepButtons ? onnext : undefined}
						durationMs={asset.duration_ms}
						file={asset}
						mayNotDraw={asset.browser_may_not_draw}
						onpicturesize={() => band.pictured()}
						{onopen}
						{onplayedthrough}
						{reachedByRun}
					/>
				{/if}
			{/snippet}
		</MediaStage>
	</div>

	{#if !asset.concealed}
		<!-- The verbs from the one host every surface uses, around everything under the stage. -->
		<FileVerbs
			items={[file]}
			around={{
				selection,
				refresh: () => void load(),
				forget: (gone) => {
					if (gone === file.id) ongone?.(gone);
				}
			}}
		>
			{#snippet children(verbs)}
				{@const on = [file.id]}
				{@const save = verbs.named('save', on, file.id)}
				{@const share = verbs.named('share', on, file.id)}
				{@const hide = verbs.named('hide', on, file.id)}
				{@const move = verbs.named('move', on, file.id)}
				<!-- Who else can see this file, beside Share: one sets it, the other reads it back. -->
				{@const reach = verbs.named('visibility', on, file.id)}
				<!-- Add to, off the same builder the right-click menu grows it from (`bar-and-menu.test.ts`). -->
				{@const all = verbs.menu(on, file.id)}
				{@const addTo = all.find((one) => one.id === 'add')}
				<!-- Where a file can be put, off the declaration, the tag row taken once. -->
				{@const places = addTo?.children ?? []}
				{@const loose = all.find((one) => one.id === 'tag')}
				{@const puttable = [
					...places,
					...(loose && !places.some((one) => one.id === loose.id) ? [loose] : [])
				]}
				{@const listed = [save, share, reach, hide, move].filter((one) => one !== undefined)}
				<!-- Edit, Create GIF and Compress, off the one table the tile's menu reads. -->
				{@const changing = all.filter((one) => ['edit', 'gif', 'compress'].includes(one.id))}
				<!-- Auto-enrich, Enrich and Don't enrich: the rows the tile's menu grows from the same
				     declaration, so the file's own menu offers what its tile does. -->
				{@const enriching = all.filter((one) => one.group === 'enrich')}
				<!-- Delete, last and below its own line; nothing for a guest. -->
				{@const destroys = [verbs.named('delete', on, file.id)].filter((one) => one !== undefined)}
				<div class="acts" class:fills={phoneWidth.yes}>
					<!-- The file's name names what the controls act on; not a heading. On a phone the name is on
					     the strip above the picture and this row is the file's bar. -->
					{#if !phoneWidth.yes}
						<div class="named">
							<!-- The name is the control: pressing it copies it (`Copyable`), and it is the one thing
							     in the row that gives ground (`shrinks`). -->
							<div class="name">
								<Copyable text={label ?? file.id} what="That name" class="filename" shrinks />
							</div>
							<!-- Which passes wrote to this file, beside its name, by the marks every page wears. -->
							<EnrichmentMarks sources={file.enriched_by ?? []} />
							<!-- Who else can see this, as the mark every tile draws; pressing it opens the sharing
							     panel. Plain text where there is no sharing verb. -->
							<SharingMark
								file
								shared={file.shared}
								restricted={file.restricted}
								shared_here={file.shared_here}
								restricted_here={file.restricted_here}
								hidden={file.hidden}
								onopen={share ? () => share.run?.(on) : undefined}
								onhidden={share ? () => share.run?.(on) : undefined}
							/>
						</div>
						<!-- Save to device is a menu row (and Ctrl-S); everything else is behind one door. Expand,
						     centred on the row by `.named` and `.ends` sharing its free space, is a disclosure. -->
						<Button
							tone="ghost"
							size="small"
							icon={expanded ? 'expand_less' : 'expand_more'}
							aria-expanded={expanded}
							aria-controls={FOLD}
							onclick={toggleFold}
						>
							{expanded ? 'Collapse' : 'Expand'}
						</Button>
					{/if}
					<div class="ends">
						<!-- The marks, then the door that puts this file somewhere, then the three dots. -->
						<AssetDetailControls
							id={shownId}
							favorite={file.favorite}
							rating={file.rating}
							oCount={file.o_count}
						/>
						<FileActions
							id={shownId}
							held={band.settled !== shownId}
							filename={label}
							onrenamed={() => void load()}
						>
							{#snippet children({ canOrganize, rename, undo })}
								<!-- One control, two doors: what somebody came to press, and the rarer verbs. A phone's
								     bar gives each its own square, with Share and Show info between. -->
								{#if phoneWidth.yes}
									{#if share}
										<Tooltip label={share.label}>
											<Button
												tone="ghost"
												icon={share.icon}
												aria-label={share.label}
												onclick={() => share.run?.(on)}
											/>
										</Tooltip>
									{/if}
									<MenuButton label="Add this file to">
										{#snippet trigger({ props })}
											<Tooltip label="Add to">
												<Button {...props} tone="ghost" icon="add" aria-label="Add to" />
											</Tooltip>
										{/snippet}
										<VerbMenuItems verbs={puttable} ids={on} subjectId={file.id} />
									</MenuButton>
									<Tooltip label="Show info">
										<Button
											tone="ghost"
											icon="keyboard_double_arrow_up"
											aria-label="Show info"
											aria-expanded={detailsOpen}
											onclick={openDetails}
										/>
									</Tooltip>
									<MenuButton label="More for this file">
										{#snippet trigger({ props })}
											<Tooltip label="More for this file">
												<Button
													{...props}
													tone="ghost"
													icon="more_vert"
													aria-label="More for this file"
												/>
											</Tooltip>
										{/snippet}
										<VerbMenuItems
											verbs={[
												...listed,
												...enriching,
												...changing,
												...ownVerbs(file, canOrganize, rename, undo),
												...destroys
											]}
											ids={on}
											subjectId={file.id}
										/>
									</MenuButton>
								{:else}
									<SplitButton
										icon="add"
										trailingIcon="more_vert"
										trailingLabel="Options for this file"
										leadMenuLabel="Add this file to"
									>
										Add to
										{#snippet leadMenu()}
											<VerbMenuItems verbs={puttable} ids={on} subjectId={file.id} />
										{/snippet}
										{#snippet menu()}
											<!-- One list, so the menu's groups hold the shared verbs and this
										     screen's own alike, and Delete is last below its own line. -->
											<VerbMenuItems
												verbs={[
													...listed,
													...enriching,
													...changing,
													...ownVerbs(file, canOrganize, rename, undo),
													...destroys
												]}
												ids={on}
												subjectId={file.id}
											/>
										{/snippet}
									</SplitButton>
								{/if}
							{/snippet}
						</FileActions>
					</div>
				</div>

				<!-- Everything the library knows about the file, behind the one control above, opening as
				     one block (`reveal`): under the picture on a desktop, in the Info sheet on a phone. -->
				{#snippet sections()}
					<!-- Full-width sections, each folding under its own heading: what it is filed under,
					     who is in it, what looks like it, and the record. -->
					<div class="under">
						<!-- These follow the picture (`band.settled`). Faces FIRST, the one time-linked row
						     (`AssetView.under.test.ts`); what looks like it SECOND; the same song THIRD. -->
						{#if band.settled}
							<FileBandRows {band} fileId={band.settled} />
							<FacesInThis
								id={band.settled}
								seekable={file.media_type === 'video'}
								onseek={(moment) => (seekTo = moment)}
							/>
							<LooksLikeThis id={band.settled} cannotCompare={file.fingerprint_verdict ?? null} />
							<SameMusic id={band.settled} name={file.title ?? label} />
						{/if}
						<FileRecordPanel
							{file}
							shownId={band.settled || shownId}
							onlySite={band.onlySite}
							bind:tab={recordTab}
							editing={editingRecord}
							adding={addingField}
							formId={RECORD_FORM}
							values={recordValues}
							{thread}
							current={() => id}
							onchoose={choosePane}
							onedit={(field) => editRecord(field)}
							onclose={closeRecordForm}
							onsave={saveRecord}
							onreload={load}
						/>

						<!-- Derivation is said in the History tab, each line linking the other file. -->
					</div>
				{/snippet}
				{#if expanded && !phoneWidth.yes}
					<div class="fold" id={FOLD} transition:reveal>
						{@render sections()}
					</div>
				{/if}
				{#if phoneWidth.yes}
					<Drawer side="bottom" label="Info" bind:open={detailsOpen}>
						{#if detailsDrawn}
							{@render sections()}
						{/if}
					</Drawer>
				{/if}

				<!-- Edit, Create GIF and Compress open the dialogs `FileVerbs` hosts for every surface,
				     outside the fold, so a press on the fold never tears an open editor down. -->
			{/snippet}
		</FileVerbs>
	{/if}
{/if}

<style>
	/* What can be done with it, under the stage; the name takes the room between (`.named`). */
	.acts {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: var(--space-2);
		margin-block-start: var(--space-3);
	}

	/* The name and its marks: `flex: 1 1 0` and no minimum keep a long name off a second row. */
	.named {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		flex: 1 1 0;
		min-inline-size: 0;
	}

	/* Only the name gives ground; the marks are fixed-size glyphs. `.name` wins on specificity. */
	.named > :global(*) {
		flex: none;
	}

	.named > .name {
		display: flex;
		align-items: center;
		flex: 1 1 0;
		min-inline-size: 0;
	}

	/* One line with an ellipsis, at a control's ink. `:global`: the class is handed to `Pressable`. */
	.named :global(.filename) {
		display: block;
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font: var(--text-body);
		color: var(--sift-ink-2);
		text-align: start;
	}

	/* The trailing end as ONE box, half the row with `.named`: Expand on the centre line. */
	.ends {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		flex: 1 1 0;
		justify-content: flex-end;
	}

	/* The box the stroke is heard on; nothing on a desktop, where the stage is the dialog's child. */
	.swipe {
		display: contents;
	}

	/* On a phone it holds the picture, and a sideways stroke reaches the page, not the browser. */
	.swipe.fills {
		display: block;
		touch-action: pan-y pinch-zoom;
	}

	/* The player's controls are a finger's width on a phone, like everything else on the screen. */
	.swipe.fills :global(.player-bar button) {
		min-inline-size: var(--touch-target);
		min-block-size: var(--touch-target);
	}

	/* A phone's bar: one line, a finger's width a control, the marks' group opened into the row. */
	.acts.fills {
		flex-wrap: nowrap;
		block-size: var(--topbar-height);
		margin-block-start: 0;
		padding-inline: var(--space-2);
	}

	.acts.fills .ends {
		justify-content: space-between;
	}

	.acts.fills .ends > :global(.detail-controls) {
		display: contents;
	}

	.acts.fills :global(button) {
		min-inline-size: var(--touch-target);
		min-block-size: var(--touch-target);
		justify-content: center;
	}

	/* Everything the Expand control opens, as ONE block the disclosure moves. */
	.fold {
		display: flex;
		flex-direction: column;
	}

	/* Everything under the picture, one thing per row, evenly apart. */
	.under {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
		margin-block-start: var(--space-4);
	}

	/* A file this account may not see, centred in the stage box, so the dialog keeps its size. */
	.concealed {
		display: grid;
		place-items: center;
		block-size: 100%;
	}

	.note {
		margin: 0;
		padding: var(--space-8);
		text-align: center;
		font: var(--text-body);
		color: var(--sift-ink-3);
	}
</style>
