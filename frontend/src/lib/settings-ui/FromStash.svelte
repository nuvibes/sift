<script lang="ts">
	/* Migrate from Stash: name Stash's database file, read what is in it, and bring it into this
	 * library as a task. */
	import type { components } from '$lib/api/schema';
	import { onMount } from 'svelte';
	import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import { Button, Fold, Pager, Problem, Switch, TextInput } from '$lib/components/common';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import FolderPicker from '$lib/library/FolderPicker.svelte';
	import { Picker } from '$lib/library/picker.svelte';
	import { bridge } from '$lib/bridge';
	import PathText from '$lib/components/PathText.svelte';
	import { clock } from '$lib/edit/edit.svelte';
	import { ratingScale } from '$lib/library/rating.svelte';
	import { ApiError, api } from '$lib/api/client';
	import { counted } from '$lib/entity/entity-counts';
	import { DownloadWatch, sayWaiting } from '$lib/jobs/watch-download.svelte';
	import LastRun from '$lib/jobs/LastRun.svelte';
	import { runWords } from '$lib/jobs/last-run';
	import { COPY as PANE } from './Backup.search';
	import { followSwitch } from './follow-switch';
	import { serverBootId } from '$lib/shell/health';

	const COPY = PANE.switcher.stash;

	/* The words for the pictures choice, the two counts it adds to the read, and the line that
	 * links to what Stash attached to nothing. */
	const MORE = {
		pictures: 'Bring their pictures',
		picturesHelp: (list: string) =>
			`The pictures Stash kept on ${list}. Each becomes a cover where there isn't one, and a cover you chose stays.`,
		blobs: "Stash's blobs folder",
		blobsHelp:
			"Stash keeps some of these pictures as files. Choose the folder they're in, in a folder you've added to Sift.",
		blobsPickerTitle: "Choose Stash's blobs folder",
		blobsPickerHelp: 'Click through to the folder Stash keeps its pictures in, then choose it.',
		groups: (count: number) =>
			count === 1
				? '1 group, which becomes a Collection of its files'
				: `${counted(count)} groups, which become Collections of their files`,
		resume: (count: number) =>
			count === 1
				? '1 file that remembers where you left off'
				: `${counted(count)} files that remember where you left off`,
		unattached: (list: string, one: boolean) =>
			`${list} had nothing attached in Stash. ${one ? 'It' : 'They'} came across with favorites, ratings and stash-box links, so you can look them over and delete what you don't want:`,
		review: { people: 'People', sites: 'Sites', tags: 'Tags' },
		/* One of each kind, for a count of one. */
		reviewOne: { people: 'person', sites: 'Site', tags: 'Tag' }
	} as const;

	/** The walls a review opens, each filtered to what Stash attached to nothing. */
	const UNATTACHED_WALL = {
		people: '/people?created=stash_unattached',
		sites: '/sites?created=stash_unattached',
		tags: '/tags?created=stash_unattached'
	} as const;

	/** The job type the server runs a Stash import as. `STASH_IMPORT` in the server's slice. */
	const STASH_JOB = 'stash_import';

	type StashRead = components['schemas']['StashRead'];
	type WaitingPage = components['schemas']['StashWaitingPage'];
	type WaitingRow = components['schemas']['StashWaitingRow'];

	/** How many waiting files a page of the list holds. */
	const WAITING_PAGE = 50;

	let path = $state('');
	let reading = $state(false);
	let read = $state<StashRead | null>(null);
	let problem = $state<string | null>(null);
	let asking = $state(false);
	/** Asking for a new library: the name field and its press are drawn. */
	let naming = $state(false);
	let newName = $state<string>(COPY.defaultName);
	let creating = $state(false);
	let opening = $state<string | null>(null);
	/** What waits for its file, one page of it, and where that page starts. */
	let waiting = $state<WaitingPage | null>(null);
	let waitingAt = $state(0);
	let waitingProblem = $state<string | null>(null);
	/** Bring Stash's pictures of People, Sites and Tags, and the blobs folder where it needs one. */
	let pictures = $state(true);
	let blobs = $state('');

	/* The folder the server offers is the starting value, once, so a folder somebody typed is
	   never replaced by a later read of the same answer. */
	$effect(() => {
		const offered = read?.blobs ?? '';
		if (offered !== '' && blobs === '') blobs = offered;
	});

	/** How the pictures come, as the two presses send it. */
	function chosen(): components['schemas']['BringStash'] {
		const on = offersPictures && pictures;
		return { pictures: on, blobs: on && read?.blobs ? blobs.trim() || null : null };
	}

	/* The run's own words are the record's: when it ends the read is asked again, and the line
	   under it then says what came across. */
	const importing = new DownloadWatch(
		STASH_JOB,
		async () => {
			read = await api.get<StashRead | null>('/stash-migration').catch(() => read);
			return read?.ran ? '' : COPY.finished;
		},
		COPY.lost
	);

	const RAN = { ...runWords(), ran: COPY.ran };

	/* The database the kept read came from names the row when the pane opens again. */
	function namedBy(kept: StashRead | null): void {
		if (path === '' && kept?.source) path = kept.source;
	}

	onMount(() => {
		void (async () => {
			try {
				read = await api.get<StashRead | null>('/stash-migration');
				namedBy(read);
			} catch {
				/* Nothing read yet is the ordinary answer; the row still draws. */
			}
			await importing.resume();
		})();
	});

	/* What waits moves when a landing brings a record across or a run replaces the list
	   (`waiting.py`), which rings the settings bell: the read is asked again, and the list with it. */
	whenChanged(settingChanges, () => {
		void (async () => {
			try {
				read = await api.get<StashRead | null>('/stash-migration');
			} catch {
				/* The list already drawn stays; the next bell asks again. */
			}
		})();
	});

	function said(error: unknown, fallback: string): string {
		return error instanceof ApiError ? (error.detail ?? error.message) : fallback;
	}

	/* The browser's way to choose: the folder picker over the folders Sift has, in a sheet. */
	const picker = new Picker();
	let picking = $state(false);
	let pressedAtTop = $state(false);
	/** What the open sheet is choosing: Stash's folder (its database is found in it) or its blobs. */
	let pickingFor = $state<'database' | 'blobs'>('database');

	async function choose() {
		if (bridge.canChooseFile()) {
			const chosen = await bridge.chooseFile();
			// Closing the dialog without choosing is not an error and changes nothing.
			if (chosen === null) return;
			path = chosen;
			await readIt();
			return;
		}
		pickingFor = 'database';
		pressedAtTop = false;
		await picker.open();
		picking = true;
	}

	/* The blobs folder the same way: the machine's own folder dialog in the desktop application,
	   the folder picker in a browser. */
	async function chooseBlobs() {
		if (bridge.canChooseFolder()) {
			const chosen = await bridge.chooseFolder();
			if (chosen !== null) blobs = chosen;
			return;
		}
		pickingFor = 'blobs';
		pressedAtTop = false;
		await picker.open();
		picking = true;
	}

	function useFolder(event: SubmitEvent) {
		event.preventDefault();
		const chosen = picker.selected;
		if (picker.atTopLevel || !chosen) {
			pressedAtTop = true;
			return;
		}
		picking = false;
		if (pickingFor === 'blobs') {
			blobs = chosen.path;
			return;
		}
		path = chosen.path;
		void readIt();
	}

	async function readIt() {
		const chosen = path.trim();
		if (chosen === '') return;
		reading = true;
		problem = null;
		/* A new read replaces the copy the last one left, so what it found is gone with it:
		   offering to bring it in while this runs would ask for a file that is being written. */
		read = null;
		asking = false;
		naming = false;
		try {
			read = await api.post<StashRead>('/stash-migration/read', { body: { path: chosen } });
			asking = false;
		} catch (error) {
			problem = said(error, COPY.cannotRead);
		} finally {
			reading = false;
		}
	}

	async function bringIn() {
		problem = null;
		try {
			const started = await api.post<components['schemas']['StashRunStarted']>(
				'/stash-migration/run',
				{ body: chosen() }
			);
			importing.follow(started.job_id);
			asking = false;
		} catch (error) {
			problem = said(error, COPY.cannotRun);
		}
	}

	async function bringIntoNew() {
		const name = newName.trim();
		if (name === '') return;
		problem = null;
		creating = true;
		const before = await serverBootId();
		try {
			await api.post<components['schemas']['StashSwitch']>('/stash-migration/new-library', {
				body: { name, ...chosen() }
			});
		} catch (error) {
			problem = said(error, COPY.cannotCreate);
			creating = false;
			return;
		}
		naming = false;
		opening = name;
		if (!(await followSwitch(before))) {
			opening = null;
			creating = false;
			problem = COPY.stillOpening;
		}
	}

	const n = (key: string): number => Number(read?.summary?.[key] ?? 0);

	/* The counts, each a sentence of its own words, in the order a person checks them. */
	const lines = $derived(
		read === null
			? []
			: [
					COPY.people(n('people')),
					COPY.sites(n('sites'), n('sites_with_a_parent')),
					COPY.tags(n('tags'), n('tags_with_one_parent'), n('tags_with_several_parents')),
					COPY.scenes(n('scenes'), n('rated_scenes')),
					COPY.images(n('images'), n('images_in_zips')),
					COPY.galleries(n('galleries')),
					COPY.markers(n('markers_with_an_end') + n('markers_without_an_end')),
					COPY.favorites(n('favorite_people'), n('favorite_sites'), n('favorite_tags')),
					COPY.matchedIds(
						n('people_with_a_box_id'),
						n('sites_with_a_box_id'),
						n('scenes_with_a_box_id')
					),
					COPY.filters(n('saved_filters'), n('filters_over_files')),
					...(n('groups') > 0 ? [MORE.groups(n('groups'))] : []),
					...(n('scenes_with_a_resume_point') > 0
						? [MORE.resume(n('scenes_with_a_resume_point'))]
						: [])
				]
	);

	/* "1,200 People, 150 Sites and 3 Tags", only the kinds that have any. */
	function kinds(people: number, sites: number, tags: number): string {
		const said = [
			[people, MORE.reviewOne.people, MORE.review.people],
			[sites, MORE.reviewOne.sites, MORE.review.sites],
			[tags, MORE.reviewOne.tags, MORE.review.tags]
		]
			.filter(([count]) => Number(count) > 0)
			.map(([count, one, many]) => `${counted(Number(count))} ${Number(count) === 1 ? one : many}`);
		return said.length <= 1
			? (said[0] ?? '')
			: `${said.slice(0, -1).join(', ')} and ${said.at(-1)}`;
	}

	const offersPictures = $derived(
		n('people_with_a_picture') + n('sites_with_a_picture') + n('tags_with_a_picture') > 0
	);

	/* What Stash attached to nothing, still here: each kind that has any, and its wall. */
	const unattached = $derived(
		(['people', 'sites', 'tags'] as const).filter((kind) => (read?.unattached?.[kind] ?? 0) > 0)
	);
	const unattachedTotal = $derived(
		unattached.reduce((sum, kind) => sum + (read?.unattached?.[kind] ?? 0), 0)
	);

	/* The list is read whenever the read says anything waits, and again as the page moves; a run
	   under way replaces what waits, so nothing is drawn while one runs. */
	$effect(() => {
		const total = read?.waiting ?? 0;
		const offset = waitingAt;
		if (total === 0 || importing.running) {
			waiting = null;
			return;
		}
		void (async () => {
			try {
				waiting = await api.get<WaitingPage>('/stash-migration/waiting', {
					query: { offset, limit: WAITING_PAGE }
				});
				waitingProblem = null;
			} catch (error) {
				waitingProblem = said(error, COPY.cannotListWaiting);
			}
		})();
	});

	/* What waits on one file, each part a sentence of its own: its stars as this account draws
	   them, its markers by title and where they start, then its People, Sites and Tags by name. */
	function waitsOn(row: WaitingRow): string[] {
		const parts: string[] = [];
		const stars = ratingScale.shown(row.rating ?? null);
		if (stars !== null) parts.push(COPY.stars(stars));
		if (row.markers.length > 0) {
			const at = row.markers.map((one) => COPY.markerAt(one.title, clock(one.start_ms)));
			parts.push(COPY.markersAre(at.join(', ')));
		}
		if (row.people.length > 0) parts.push(COPY.peopleAre(row.people.join(', ')));
		if (row.sites.length > 0) parts.push(COPY.sitesAre(row.sites.join(', ')));
		if (row.tags.length > 0) parts.push(COPY.tagsAre(row.tags.join(', ')));
		return parts;
	}

	const lastPage = (total: number): number =>
		Math.max(0, Math.floor((total - 1) / WAITING_PAGE) * WAITING_PAGE);

	const status = $derived(
		importing.waiting
			? sayWaiting(importing.position)
			: COPY.running(Math.round(importing.fraction * 100))
	);
</script>

<!-- The row: the database chosen, the press that chooses it, and the press that reads it again. -->
<div class="from-stash">
	<LabelledRow id="backup.stash" label={COPY.name} help={COPY.help} wide besideField>
		<span class="chosen" id="stash-path">
			{#if path}<PathText {path} />{:else}{COPY.nothingChosen}{/if}
		</span>
		<Button
			tone="secondary"
			icon="folder"
			onclick={() => void choose()}
			disabled={reading || importing.running}>{COPY.choose}</Button
		>
		<Button
			icon="find_in_page"
			onclick={() => void readIt()}
			disabled={reading || path.trim() === '' || importing.running}
			aria-busy={reading}>{COPY.read}</Button
		>
	</LabelledRow>

	<Problem message={problem} />

	<Modal
		bind:open={picking}
		title={pickingFor === 'blobs' ? MORE.blobsPickerTitle : COPY.pickerTitle}
	>
		<form class="stash-folder" onsubmit={useFolder}>
			<span class="field-label" id="stash-folders">{COPY.pickerList}</span>
			<FolderPicker {picker} labelledBy="stash-folders" describedBy="stash-folders-help" />
			<p class="note" id="stash-folders-help">
				{pickingFor === 'blobs' ? MORE.blobsPickerHelp : COPY.pickerHelp}
			</p>
			{#if pressedAtTop && picker.atTopLevel}
				<p class="note" role="alert">{COPY.pickerAtTop}</p>
			{/if}
			<div class="picker-actions">
				<Button onclick={() => (picking = false)}>{COPY.cancel}</Button>
				<Button type="submit" tone="primary" icon="check">{COPY.useFolder}</Button>
			</div>
		</form>
	</Modal>

	{#if read !== null && !importing.running}
		<div class="read" role="group" aria-label={COPY.readHeading}>
			<p class="note">{COPY.readSays(String(read.summary?.version ?? ''))}</p>
			<ul class="counts">
				{#each lines as line (line)}<li>{line}</li>{/each}
			</ul>
			<p class="note">
				{Object.keys(read.mapping ?? {}).length > 0
					? COPY.matched(Object.keys(read.mapping ?? {}).length)
					: COPY.noneMatched}
			</p>
			<Fold summary={COPY.notComingFold} id="stash-not-coming">
				<ul class="counts">
					{#each COPY.notComing as line (line)}<li>{line}</li>{/each}
				</ul>
			</Fold>
			{#if read.ran}
				<!-- A library that already came across says so, or the press reads as never pressed. -->
				<p class="warning" role="status">
					<LastRun run={read.ran} words={RAN} />
				</p>
				{#if unattached.length > 0}
					<!-- What Stash attached to nothing, each kind one press from its own wall filtered
					     to exactly those rows. -->
					<p class="note">
						{MORE.unattached(
							kinds(
								read.unattached?.people ?? 0,
								read.unattached?.sites ?? 0,
								read.unattached?.tags ?? 0
							),
							unattachedTotal === 1
						)}
						{#each unattached as kind, index (kind)}<a
								href={UNATTACHED_WALL[kind]}
								data-unattached={kind}>{MORE.review[kind]}</a
							>{index < unattached.length - 1 ? ', ' : '.'}{/each}
					</p>
				{/if}
			{/if}
			{#if (read.waiting ?? 0) > 0}
				<Fold summary={COPY.waitingFold(read.waiting ?? 0)} id="stash-waiting">
					<p class="note">{COPY.waitingLede}</p>
					<Problem message={waitingProblem} />
					{#if waiting !== null}
						<ul class="waiting-rows">
							{#each waiting.rows as row (row.id)}
								{@const carries = waitsOn(row)}
								<li>
									<span class="what"
										><span class="label">{row.label}</span><span class="kind"
											>{COPY.kindOf(row.kind)}</span
										></span
									>
									{#each row.paths as path (path)}<span class="path"><PathText {path} /></span
										>{/each}
									{#if carries.length > 0}<span class="carries">{carries.join('. ')}</span>{/if}
								</li>
							{/each}
						</ul>
						{#if waiting.total > waiting.rows.length}
							{@const total = waiting.total}
							<Pager
								offset={waiting.offset}
								shown={waiting.rows.length}
								{total}
								noun={COPY.waitingNoun}
								one={COPY.waitingNoun}
								onfirst={() => (waitingAt = 0)}
								onprevious={() => (waitingAt = Math.max(0, waitingAt - WAITING_PAGE))}
								onnext={() => (waitingAt = Math.min(waitingAt + WAITING_PAGE, lastPage(total)))}
								onlast={() => (waitingAt = lastPage(total))}
								onjump={(position) =>
									(waitingAt = Math.floor(Math.max(0, position - 1) / WAITING_PAGE) * WAITING_PAGE)}
							/>
						{/if}
					{/if}
				</Fold>
			{/if}
			{#if offersPictures}
				<!-- The pictures ride with either press: a choice about what comes, asked before it. -->
				<LabelledRow
					label={MORE.pictures}
					help={MORE.picturesHelp(
						kinds(n('people_with_a_picture'), n('sites_with_a_picture'), n('tags_with_a_picture'))
					)}
				>
					<Switch label={MORE.pictures} bind:checked={pictures} disabled={creating} />
				</LabelledRow>
				{#if pictures && read.blobs}
					<LabelledRow label={MORE.blobs} help={MORE.blobsHelp} wide besideField>
						<span class="chosen" id="stash-blobs">
							{#if blobs}<PathText path={blobs} />{:else}{COPY.nothingChosen}{/if}
						</span>
						<Button
							tone="secondary"
							icon="folder"
							onclick={() => void chooseBlobs()}
							disabled={creating}>{COPY.choose}</Button
						>
					</LabelledRow>
				{/if}
			{/if}
			{#if naming}
				<p class="warning">{COPY.newSays}</p>
				<div class="line">
					<label class="field-label" for="stash-new-name">{COPY.newName}</label>
					<TextInput
						id="stash-new-name"
						class="stash-new-name"
						bind:value={newName}
						maxlength={64}
						disabled={creating}
					/>
					<Button
						icon="add"
						onclick={() => void bringIntoNew()}
						disabled={creating || newName.trim() === ''}
						aria-busy={creating}>{COPY.create}</Button
					>
					<Button tone="ghost" disabled={creating} onclick={() => (naming = false)}
						>{COPY.cancel}</Button
					>
				</div>
			{:else if asking}
				<p class="warning">{COPY.changes}</p>
				<div class="line">
					<Button icon="upload" onclick={() => void bringIn()}>{COPY.confirm}</Button>
					<Button tone="ghost" onclick={() => (asking = false)}>{COPY.cancel}</Button>
				</div>
			{:else}
				<div class="line">
					<Button icon="upload" onclick={() => (naming = true)}>{COPY.intoNew}</Button>
					<Button icon="upload" onclick={() => (asking = true)}
						>{read.ran ? COPY.bringInAgain : COPY.bringIn}</Button
					>
				</div>
			{/if}
		</div>
	{/if}
	{#if opening !== null}
		<p class="note" role="status">{COPY.opening(opening)}</p>
	{:else if importing.running}
		<p class="note" role="status">{status}</p>
	{:else if importing.outcome}
		<p class="note" role="status">{importing.outcome}</p>
	{/if}
</div>

<style>
	.read {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: var(--space-2);
		margin-block-start: var(--space-3);
	}

	.line {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
	}

	.counts {
		margin: 0;
		padding-inline-start: var(--space-5);
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	.note {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	/* The name field is as wide as a library's name, not the column: it sits on the line with
	   its label and its two presses. */
	.from-stash :global(.stash-new-name) {
		inline-size: var(--name-field-width);
		flex: 0 1 18rem;
	}

	/* The new library's name, labelled in the row's own small type beside its field. */
	.field-label {
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	/* The database chosen, as long as it is, cut at the start of the column rather than widening
	   it, as a chosen folder is in its own row. */
	.chosen {
		flex: 1 1 0;
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		color: var(--sift-ink-2);
		font: var(--text-body);
		text-align: end;
	}

	/* The folder sheet: the list, what to do with it, then its two presses at the far edge. */
	.stash-folder {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		margin-block-start: var(--space-4);
	}

	.picker-actions {
		display: flex;
		gap: var(--space-2);
		margin-block-start: var(--space-2);
	}

	.picker-actions > :global(:first-child) {
		margin-inline-start: auto;
	}

	/* The one sentence here about what changes in this library, in the reader's own ink. */
	.warning {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink);
	}
	/* What waits: one entry per file, its name and kind on the first line, then each path Stash
	   had it at, then what waits on it, in the row's small type. */
	.waiting-rows {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.waiting-rows li {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	.what {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: var(--space-2);
	}

	.label {
		color: var(--sift-ink);
		font-weight: 600;
	}

	.kind,
	.carries {
		color: var(--sift-ink-3);
	}

	.path {
		overflow-wrap: anywhere;
	}

	/* The pane's rows and the read-back under them, one column at the pane's own gap. */
	.from-stash {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}
</style>
