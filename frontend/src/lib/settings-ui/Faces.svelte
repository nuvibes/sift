<script lang="ts">
	/* Recognizing faces. The switch is a consent gate: nothing loads or is written until it is on. */
	import { onMount } from 'svelte';
	import {
		ChooseFile,
		ConfirmDialog,
		Empty,
		Fold,
		LabelledRow,
		MoreAbout,
		NarrowBox,
		Problem,
		ProgressBar,
		Scroller,
		SettingLink
	} from '$lib/components/common';
	import {
		fetchSettings,
		refusalOf,
		saveSettings,
		type SettingEntry
	} from '$lib/settings-ui/settings';
	import { jobChanges, settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import SettingRow from './SettingRow.svelte';
	import SwitchPointer, { namesOf } from './SwitchPointer.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import ActionRow from './ActionRow.svelte';
	import FolderImportRow from './FolderImportRow.svelte';
	import RecognitionPane, {
		DANGER_HEADING,
		deviceWords,
		openPage,
		type SubPage
	} from './RecognitionPane.svelte';
	import { filedUnder } from './drilldown.svelte';
	import { explainAbsentRows } from '$lib/settings-ui/settings-anchor.svelte';
	import { COPY, DELETE_FACE_DATA, SEARCHABLE, facesStatus } from './Faces.search';
	import {
		FACES_ENABLED_KEY as ENABLED_KEY,
		FACES_DEVICE_KEY as DEVICE_KEY,
		faceSettings,
		fetchModels,
		forgetFaces,
		regroupFaces,
		knownPeople,
		type KnownPerson,
		startersOffer,
		useStarters,
		scanLibrary,
		stopScanning,
		sweep,
		type FaceSettings
	} from '$lib/people/faces.svelte';
	import { exportPack, importPack, type PackImported } from '$lib/people/fingerprint-packs';
	import type { WaitingFingerprints } from '$lib/people/fingerprint-offers';
	import { folderImport } from '$lib/people/folder-import.svelte';
	import { refusedMark } from '$lib/swap/refused';
	// The download is followed OUTSIDE this component. See the module. A watcher held here dies
	// with the pane, and the first run starts the same download from a screen that is not this one.
	import { modelFetch } from '$lib/people/faces-runs.svelte';
	import { faceRemoval } from '$lib/jobs/watch-removal.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { ApiError } from '$lib/api/client';
	import { counted } from '$lib/entity/entity-counts';
	import DataRows, { type Column } from '$lib/components/common/DataRows.svelte';
	import DataRow from '$lib/components/common/DataRow.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import { api } from '$lib/api/client';
	import type { components } from '$lib/api/schema';
	import WaitingForAFace from './WaitingForAFace.svelte';
	import ExportPeopleSheet from './ExportPeopleSheet.svelte';
	import { stampForAFileName } from '$lib/shell/when';

	/* The people list's columns: each count right-aligned under a heading naming what it counts. */
	const KNOWN_COLUMNS: readonly Column[] = [
		{ id: 'who', width: 'minmax(0, 1fr)' },
		{ id: 'faces', label: 'Confirmed faces', width: '9rem', align: 'end' },
		{ id: 'starters', label: 'Starters', width: '6rem', align: 'end' }
	];
	/* Where a person has none of a count: quieter than a figure, so a zero is not read as data. */
	const NONE = '\u2014';

	/* Which settings this screen draws, by key, in order: each is worded by its declaration. */
	interface Field {
		key: string;
		/** Drawn only while `key` holds `is`. */
		when?: { key: string; is: string };
	}

	/** The one dial on the page itself: how much of each video Sift checks. */
	const THOROUGH_KEY = 'faces.effort';

	const MORE_GROUPS: { heading: string; fields: Field[] }[] = [
		{
			heading: COPY.groups.device,
			fields: [{ key: DEVICE_KEY }, { key: 'faces.model' }]
		},
		{
			heading: COPY.groups.finding,
			fields: [{ key: 'faces.minimum_quality' }, { key: 'faces.file_budget_seconds' }]
		}
	];

	/** Every key the More settings page draws, so a deep link to one opens the page first. */
	const MORE_KEYS = MORE_GROUPS.flatMap((group) => group.fields.map((field) => field.key));

	/* Whether a field's condition is met. An unset condition is met; a condition naming a setting
	 * this screen has not read yet is not, which is the safe way round: a field that appears for
	 * an instant while the values load and then vanishes reads as a fault. */
	function applies(field: Field): boolean {
		if (!field.when) return true;
		return String(entries.get(field.when.key)?.value ?? '') === field.when.is;
	}

	let entries = $state<Map<string, SettingEntry>>(new Map());

	/* Read here rather than with a const tag in the markup, which has to be the immediate child of
	   a block and this one sits inside an ordinary section. */
	const consentEntry = $derived(entries.get(ENABLED_KEY));
	let enabled = $state(false);
	// Not called `state`: that name shadows the `$state` rune in this file's own scope.
	let feature = $state<FaceSettings | null>(null);
	/* Where recognition stands, in one line under the switch, every number the server's. The words
	   are shared with the switch on Importing (`facesStatus`), so the two doors say the same thing. */
	const status = $derived(
		facesStatus(
			feature,
			enabled,
			feature ? deviceWords(entries.get(DEVICE_KEY), feature.device) : ''
		)
	);
	/* Switched on and unable to run: no models, or a device it cannot use. */
	const caution = $derived(
		enabled && feature !== null && (!feature.ready || Boolean(feature.device_problem))
	);

	let loadFailed = $state(false);
	let forgetOpen = $state(false);
	/* The sweep is followed by `sweep`, which outlives this screen; only the press's state is here. */
	let sweepFailed = $state(false);
	let stopping = $state(false);

	/* A file taken in says what came, never who: the pass after it decides that by face. */
	let packFile = $state<File | null>(null);
	/* Whether the pass over facial fingerprints makes a new person of an entry whose faces match
	   nobody's: a stored setting, drawn as the registry words it. Off, the group that looks like
	   that entry asks instead, in Organize. */
	const PEOPLE_FROM_FILES_KEY = 'faces.people_from_files';
	const peopleFromFiles = $derived(entries.get(PEOPLE_FROM_FILES_KEY));
	/* Rebuilding the piles. See the section it draws in: it is a one-off job like the rebuilds on
	   Maintenance, and it is here because every dial that decides HOW faces are grouped is here. */
	let regrouping = $state(false);

	async function regroup() {
		regrouping = true;
		try {
			await regroupFaces();
			toasts.show(COPY.regroup.started, { tone: 'success' });
		} catch {
			toasts.show(COPY.regroup.failed, { tone: 'error' });
		} finally {
			regrouping = false;
		}
	}

	let importing = $state(false);
	let imported = $state<PackImported | null>(null);

	/* Said after an import that held new faces: the pass over them is queued at once. */
	const RECOGNIZING_STARTED = ' Sift has started recognizing who they are in your library.';

	/* How many faces a file brought and how many people it holds, then that recognizing has
	   started when recognition is on (off, the line under it says what waits). A second import of
	   the same file brings nothing, and says that plainly rather than as a failure. */
	function packSaid(done: PackImported): string {
		if (done.added === 0) return 'Nothing new in this file. Its faces were already here.';
		const faces = `${counted(done.added)} ${done.added === 1 ? 'face' : 'faces'}`;
		const who = `${counted(done.people)} ${done.people === 1 ? 'person' : 'people'}`;
		return `Took in ${faces} of ${who}.${done.recognizing ? RECOGNIZING_STARTED : ''}`;
	}
	let packError = $state<string | null>(null);

	/* Who Sift can already identify, re-read on every keystroke: the reference gallery can be
	 * hundreds of People, and the server is what knows which of them have faces attached. */
	let known = $state<KnownPerson[]>([]);
	let knownTotal = $state(0);
	let lookingFor = $state('');
	let knownLoaded = $state(false);
	let knownFailed = $state(false);
	let knownDeclined = $state<string | null>(null); // the server's sentence while it is off

	async function readKnown() {
		try {
			const answer = await knownPeople(lookingFor.trim());
			known = answer.items;
			knownTotal = answer.total;
			knownDeclined = answer.declined ?? null;
			knownLoaded = true;
			knownFailed = false;
		} catch {
			knownLoaded = false;
			knownFailed = true;
		}
	}

	/* Starter pictures: counted before the press, which asks a stash-box for each. */
	let starterPeople = $state<number | null>(null);
	/* Who they are, by name, so the count can be checked as people before the press. */
	let starterWho = $state<{ id: string; name: string }[]>([]);
	let startingStarters = $state(false);

	async function readStarters() {
		try {
			const offer = await startersOffer();
			starterPeople = offer.people;
			starterWho = offer.who ?? [];
		} catch {
			starterPeople = null;
			starterWho = [];
		}
	}

	async function addStarters() {
		if (startingStarters) return;
		startingStarters = true;
		try {
			const queued = await useStarters();
			toasts.show(COPY.starters.started(queued.people), { tone: 'success' });
			await readStarters();
		} catch {
			toasts.show(COPY.starters.failed, { tone: 'error' });
		} finally {
			startingStarters = false;
		}
	}

	async function takeInPack() {
		if (!packFile || importing) return;
		importing = true;
		packError = null;
		imported = null;
		try {
			imported = await importPack(packFile);
			await readKnown();
			waitingRead += 1;
		} catch (error) {
			// A 400 is the file's own refusal, written to be shown: another face model, another
			// version, damaged, or not one of these files at all. Said as the server words it.
			packError =
				error instanceof ApiError && error.status === 400
					? error.detail || COPY.pack.notOne
					: COPY.pack.couldNotAdd;
		} finally {
			importing = false;
		}
	}

	/* Who the export carries: the people Sift can recognize who have a page of their own and faces
	   somebody confirmed. The row's own press sends everyone; the sheet behind Choose people sends a
	   choice (`ExportPeopleSheet`). */
	let chooser = $state<ReturnType<typeof ExportPeopleSheet> | null>(null);
	/* Bumped after an import, so the list of who is waiting for a matching face is read again. */
	let waitingRead = $state(0);
	/* How many people waiting for a matching face the file carries as their own, the server's
	   count; null until read, and while the list could not be read. */
	let waitingCarried = $state<number | null>(null);
	let waitingFailed = $state(false);
	function waitingSaid(answer: WaitingFingerprints | null) {
		waitingFailed = answer === null;
		waitingCarried = answer === null ? null : (answer.exportable ?? 0);
	}
	/* How many the file would carry. A person known by starter pictures alone has nothing of their
	   own to export, and one kept local or out of swaps is refused, as a swap refuses them. */
	const shareable = $derived(
		known.filter((one) => one.id && one.faces > 0 && !refusedMark(one)).length
	);
	const exportable = $derived(knownLoaded && shareable + (waitingCarried ?? 0) > 0);

	/* A folder import that ends changes both lists the export row counts. */
	let importSeen = folderImport.outcome;
	$effect(() => {
		const outcome = folderImport.outcome;
		if (outcome === importSeen) return;
		importSeen = outcome;
		if (outcome === null) return;
		void readKnown();
		waitingRead += 1;
	});
	/* Off by default each time: the pictures are small crops of real faces, and only somebody who
	   chose to send them should. */
	let includePictures = $state(false);

	type LibrariesView = components['schemas']['LibrariesView'];

	/* The library's own name for the saved file, so two libraries' exports never share one. */
	async function libraryName(): Promise<string> {
		try {
			const listed = await api.get<LibrariesView>('/libraries');
			return listed.libraries.find((one) => one.current)?.name || 'Sift';
		} catch {
			return 'Sift';
		}
	}

	/* No names sent is everybody, so the row's own press sends none and the sheet sends its lists.
	   The library's name is the file's own: the other side keys a file by it. */
	async function sendOutPack(personIds: string[] = [], entryIds: string[] = []) {
		packError = null;
		try {
			const library = await libraryName();
			const bytes = await exportPack(library, { personIds, entryIds, includePictures });
			// An anchor and an object URL: the plain way to hand a file to the browser, and one that
			// works over plain http where the fancier APIs are simply absent.
			const url = URL.createObjectURL(bytes);
			const link = document.createElement('a');
			link.href = url;
			link.download = COPY.pack.fileName(library, stampForAFileName(new Date()).split(' ')[0]);
			link.click();
			URL.revokeObjectURL(url);
		} catch (error) {
			packError = (error instanceof ApiError && error.detail) || COPY.pack.couldNotSave;
		}
	}

	/* Bumped by every write, so a read still in flight cannot put back a value from before a press. */
	let generation = 0;

	onMount(() => {
		void load();
		void readKnown();
		void readStarters();
		// A sweep already going is picked up rather than ignored, so opening this screen halfway
		// through a run shows the run rather than the button that starts one.
		sweep.announceWith((message) => toasts.show(message, { tone: 'success' }));
		void sweep.resume();
		// The same for the model download: started here, left running, joined again on the way back.
		void modelFetch.resume();
		void faceRemoval.resume();
	});

	/* The row that follows a scan of every file is drawn only while one runs. A search or a link
	   landing on it otherwise rings the press that starts one, on More settings, and says so. */
	$effect(() =>
		explainAbsentRows((key) => {
			if (feature === null) return null;
			const startable = feature.ready && !feature.device_problem;
			if (key === 'faces.sweep' && sweep.jobId === null) {
				return { because: COPY.sweep.notRunning, near: startable ? 'faces.rescan' : undefined };
			}
			/* The two rows drawn only once the models are here: the press that brings them is rung
			   in their place, or the switch where recognition is off. */
			const waiting =
				(key === 'faces.rescan' && !startable) ||
				(key === 'faces.models' && (!feature.ready || modelFetch.running));
			if (!waiting) return null;
			const row = key === 'faces.rescan' ? COPY.rescan.label : COPY.models.again;
			return {
				because: COPY.models.notReady(row),
				near: enabled ? 'faces.download' : 'faces.scan'
			};
		})
	);

	/* Read again when a setting changes elsewhere: nothing here is held unsaved. */
	whenChanged(settingChanges, () => void load());
	/* How many files are scanned, waiting or failed is written per file by the scans, which the
	   queue reports: the counts on this pane follow the jobs bell while it is open, rather than
	   reading only when the pane opens. The status alone, not the settings beside it. */
	whenChanged(jobChanges, () => {
		void faceSettings().then(
			(state) => (feature = state),
			() => undefined
		);
	});

	/* The server's answer once a download has ended, drawn without asking again. The watcher
	   announces the ending itself. */
	$effect(() => {
		if (modelFetch.outcome !== null) feature = modelFetch.settled ?? feature;
	});

	async function load() {
		const mine = generation;
		try {
			const sections = await fetchSettings();
			const all = sections.flatMap((section) => section.settings ?? []);
			const state = await faceSettings();
			if (mine !== generation) return;
			entries = new Map(all.map((entry) => [entry.key, entry]));
			enabled = Boolean(entries.get(ENABLED_KEY)?.value);
			feature = state;
		} catch {
			if (mine === generation) loadFailed = true;
		}
	}

	async function save(key: string, value: unknown) {
		generation += 1;
		const entry = entries.get(key);
		const previous = entry?.value;
		if (entry) entries.set(key, { ...entry, value });
		entries = new Map(entries);
		try {
			await saveSettings({ [key]: value });
			// The switch changes what the feature can report about itself, so the status line is
			// re-read rather than guessed at: turning it on does not make it ready.
			if (key === ENABLED_KEY) feature = await faceSettings();
		} catch (error) {
			if (entry) entries.set(key, { ...entry, value: previous });
			entries = new Map(entries);
			// The server's own words when it sent any. Choosing a graphics card this installation
			// cannot drive is refused with a sentence saying so, and a generic toast would hide it.
			toasts.show(refusalOf(error), { tone: 'error' });
		}
	}

	/* Start a sweep and follow it, so the press says what the job queued, nothing included. */
	async function scanEverything(everything = false) {
		sweepFailed = false;
		try {
			sweep.follow(await scanLibrary({ everything }));
		} catch {
			sweepFailed = true;
		}
	}

	/* Stop the sweep and everything it queued. The watcher is told at once rather than left to
	 * notice on its next tick: cancelling is instant, and two seconds of a screen still counting
	 * down reads as a button that did nothing. */
	async function stopScanningEverything() {
		if (!sweep.jobId || stopping) return;
		stopping = true;
		try {
			await stopScanning(sweep.jobId);
		} catch {
			toasts.show("Couldn't cancel the scan", { tone: 'error' });
		} finally {
			stopping = false;
		}
	}

	/* Ask for the models. The job is followed by the module above, which the first run also
	   watches, so both screens read one download rather than each polling it. */
	async function fetchTheModels(again = false) {
		try {
			modelFetch.follow(await fetchModels(again));
		} catch {
			modelFetch.couldNotStart(COPY.models.couldNotStart);
		}
	}

	async function forget() {
		try {
			faceRemoval.follow(await forgetFaces());
		} catch {
			faceRemoval.couldNotStart();
		}
	}

	/* The page one level in. Declared as a value, so the row that opens it and the claim that lets a
	   deep link open it first are about the same page. The People lists are on the pane itself,
	   after it: the lists a section owns are part of its page, not one level in. */
	const morePage: SubPage = $derived({
		title: COPY.more.label,
		/* The hand rows drawn on the page are the ones its search entries are filed under. */
		keys: [...MORE_KEYS, ...filedUnder(SEARCHABLE, COPY.more.label)],
		body: moreSettings,
		door: COPY.more.action
	});
	const pages = $derived(enabled ? [morePage] : []);
	/* What is drawn only while recognition is on, for a link landing on it while it is off. */
	const offRows = $derived(
		namesOf([...morePage.keys, THOROUGH_KEY, 'faces.more', 'faces.starters'], entries, SEARCHABLE)
	);
</script>

<!-- More settings: for somebody tuning recognition. Every registered row is the setting's own. -->
{#snippet moreSettings()}
	{#each MORE_GROUPS as group (group.heading)}
		<SettingGroup heading={group.heading}>
			{#each group.fields as field (field.key)}
				{@const entry = applies(field) ? entries.get(field.key) : undefined}
				{#if entry}
					<SettingRow
						{entry}
						value={entry.value}
						onchange={(next: unknown) => void save(field.key, next)}
					/>
				{/if}
			{/each}
		</SettingGroup>
	{/each}
	<!--
		Rebuilding the groups and identifying everything again: the two acts that apply a change made
		above to what is already there, so they sit under the settings. Identify all files again is
		not Run now on Tasks: Run now covers what the settings now ask for; this answers what cannot
		be worked out from settings at all (a model changed underneath, crops that came out badly).
	-->
	<SettingGroup>
		<ActionRow
			id="faces.regroup"
			label={COPY.regroup.label}
			help={COPY.regroup.help}
			action={regrouping ? COPY.regroup.busy : COPY.regroup.action}
			busy={regrouping}
			onclick={() => void regroup()}
		/>
		{#if feature?.ready && !feature.device_problem}
			<ActionRow
				id="faces.rescan"
				label={COPY.rescan.label}
				help={COPY.rescan.help}
				action={COPY.rescan.action}
				disabled={sweep.jobId !== null}
				onclick={() => void scanEverything(true)}
			/>
			{#if sweepFailed}
				<Problem message="Couldn't start the scan." />
			{/if}
		{/if}
	</SettingGroup>
	{#if feature?.ready && !modelFetch.running}
		<!-- Named for what pressing it would DO, which is not the same sentence once the files are
		     already here. -->
		<SettingGroup heading={COPY.models.label} help={COPY.models.help}>
			<ActionRow
				id="faces.models"
				label={COPY.models.again}
				help={COPY.models.againHelp}
				action={COPY.models.againPress}
				actionLabel={COPY.models.again}
				onclick={() => void fetchTheModels(true)}
			/>
		</SettingGroup>
	{/if}
{/snippet}

<!-- The lists this section owns: who Sift can recognize, and the two ways to teach it many at once.
     Drawn with recognition off too, because switching it off for a week is no reason to lose the
     list. -->
<!-- A count in the people list: the figure, or a quiet dash read out as none. -->
{#snippet tally(count: number)}
	{#if count > 0}
		<span class="count">{counted(count)}</span>
	{:else}
		<span class="none" aria-hidden="true">{NONE}</span><span class="unseen">none</span>
	{/if}
{/snippet}

{#snippet lists()}
	<SettingGroup id="faces.people" heading={COPY.lists.people.name} help={COPY.lists.people.help}>
		<div class="stack">
			<NarrowBox
				label="Search people Sift can recognize"
				bind:value={lookingFor}
				oninput={() => void readKnown()}
			/>

			{#if knownDeclined}
				<Empty scope="block">{knownDeclined}</Empty>
			{:else if !knownLoaded}
				<Problem message="Couldn't load this list." />
			{:else if known.length === 0}
				<Empty scope="block">
					{lookingFor.trim()
						? `No one matches "${lookingFor.trim()}".`
						: 'No one yet. Add people from a file or confirm a face, and the person appears here.'}
				</Empty>
			{:else}
				<p class="quiet small">
					{knownTotal}
					{knownTotal === 1 ? 'person' : 'people'}.
				</p>
				<!-- The name goes to that person's own page. Reading this list is almost always
			     followed by wanting to look at somebody in it, and a name that is only text makes
			     that a search somewhere else. Anybody without a page of their own is drawn dimmer
			     and stays plain text, so a name you cannot follow looks different before you
			     click it rather than after. -->
				<!-- A table: the name, then each count in a column of its own under a heading that
				     says what it counts, right-aligned, and a quiet dash where there is none. Starter
				     pictures from a stash-box are never counted with the confirmed faces: a face like
				     them waits under Needs your input, never named. -->
				<div class="known-box">
					<Scroller>
						<!-- The list stands on the pane's edges by its own option, which pulls it out
						     by a row's padding. The scroller clips at its root, so the room that overhang
						     needs is given on a box inside it, as the Scroller asks. -->
						<div class="known-room">
							<DataRows
								items={known}
								key={(person: KnownPerson) => person.id || person.name}
								label={COPY.lists.people.name}
								columns={KNOWN_COLUMNS}
								edges
							>
								{#snippet row(person: KnownPerson)}
									<DataRow compact cells={{ who, faces, starters }} />
									{#snippet who()}
										{#if person.id}
											<a class="who" href="/people/{person.id}">{person.name}</a>
										{:else}
											<span class="who pageless">{person.name}</span>
										{/if}
										<!-- Created from facial fingerprints: the file or folder, as their
										     History line names it. -->
										{#if person.from_fingerprints}
											<span class="came-from"
												>{COPY.fromFingerprints(person.from_fingerprints)}</span
											>
										{/if}
									{/snippet}
									{#snippet faces()}{@render tally(person.faces)}{/snippet}
									{#snippet starters()}{@render tally(person.starters)}{/snippet}
								{/snippet}
							</DataRows>
						</div>
					</Scroller>
				</div>
				<!-- What the Confirmed faces column counts, folded under the numbers it explains.
				     As a group of its own, a heading over three sentences with nothing to set, it
				     would read as a section that had lost its rows. "Confirmed face" rather than
				     "reference" throughout: the same object, said in the words of the act that
				     makes one, needs nothing explained before it can be read. -->
				<MoreAbout text={COPY.lists.confirmed} />
			{/if}
		</div>
		<!-- The starter pictures are about who Sift can recognize, so their row closes this
		     group rather than opening a group of its own with no heading. -->
		{#if starterPeople !== null}
			<ActionRow
				id="faces.starters"
				label={COPY.starters.label(starterPeople)}
				help={starterPeople > 0 ? COPY.starters.help : COPY.starters.none}
				action={COPY.starters.action}
				busy={startingStarters}
				disabled={starterPeople === 0}
				onclick={() => void addStarters()}
			>
				{#if starterPeople > 0}<MoreAbout text={COPY.starters.more} />{/if}
				<!-- Which People the count is: each name opens that person, so the press can be
				     checked before it reaches out to a stash-box for every one of them. -->
				{#if starterWho.length > 0}
					<Fold summary={COPY.starters.who(starterWho.length)} id="faces.starters.who">
						<div class="known-box">
							<Scroller>
								<ul class="starter-names">
									{#each starterWho as person (person.id)}
										<li><a class="who" href="/people/{person.id}">{person.name}</a></li>
									{/each}
								</ul>
							</Scroller>
						</div>
					</Fold>
				{/if}
			</ActionRow>
		{/if}
	</SettingGroup>

	<!-- Held by a file or a folder and matched by no face yet. See `WaitingForAFace`. -->
	<WaitingForAFace
		{enabled}
		read={waitingRead}
		onchanged={() => void readKnown()}
		onread={waitingSaid}
	/>

	<SettingGroup id="faces.packs" heading={COPY.lists.packs.name} help={COPY.lists.packs.help}>
		<!-- Taking a file in, one row: choosing the file is the press. The shared picker, because the
		     browser's own file input cannot be restyled. See `ChooseFile`. -->
		<LabelledRow id="faces.pack-import" label={COPY.pack.import} help={COPY.pack.importHelp}>
			{#snippet foot()}
				{#if imported}
					<!-- What was taken in, and nothing about who: the pass that follows decides that by
					     face and says so in History, so this line names nobody and asks nothing. -->
					<p class="status ready">{packSaid(imported)}</p>
					{#if !imported.recognizing}
						<!-- Taken in with recognition off: kept, and nothing recognized until it is on. -->
						<p class="small">
							{COPY.pack.waitsForSwitch[0]}<SettingLink section="importing" setting={ENABLED_KEY}
								>{consentEntry?.label ?? ENABLED_KEY}</SettingLink
							>{COPY.pack.waitsForSwitch[1]}
						</p>
					{/if}
				{/if}
				<Problem message={packError} />
			{/snippet}
			<ChooseFile
				accept=".zip,application/zip"
				label={COPY.pack.import}
				icon="upload"
				busy={importing}
				disabled={importing}
				onchoose={(file) => {
					packFile = file;
					imported = null;
					void takeInPack();
				}}
			>
				{importing ? COPY.pack.busy : COPY.pack.importAction}
			</ChooseFile>
		</LabelledRow>
		<!-- The registered setting's own row, worded by the registry: saved, and turning it on
		     queues the pass over the facial fingerprints already held. -->
		{#if peopleFromFiles}
			<SettingRow
				entry={peopleFromFiles}
				value={peopleFromFiles.value}
				onchange={(next: unknown) => void save(PEOPLE_FROM_FILES_KEY, next)}
			/>
		{/if}
		<!-- Everyone goes in; the trailing half opens the sheet that leaves people out. A swap is the
		     other way to send them, from Swap's own screen. -->
		<ActionRow
			id="faces.pack-export"
			label={COPY.pack.export}
			help={COPY.pack.exportHelp}
			action={COPY.pack.exportAction}
			icon="download"
			disabled={!enabled || !exportable}
			onclick={() => void sendOutPack()}
			trailingIcon="checklist"
			trailingLabel={COPY.pack.choose}
			ontrailing={() => void chooser?.choose()}
		>
			<!-- Always one line: who the file carries, or why there is no file to save. -->
			{#if !enabled}
				<p class="status">
					{COPY.pack.exportNeedsSwitch[0]}<SettingLink section="importing" setting={ENABLED_KEY}
						>{consentEntry?.label ?? ENABLED_KEY}</SettingLink
					>{COPY.pack.exportNeedsSwitch[1]}
				</p>
			{:else if knownFailed || waitingFailed}
				<p class="status">{COPY.pack.couldNotRead}</p>
			{:else if knownLoaded && waitingCarried !== null && !lookingFor.trim()}
				<p class="status">{COPY.pack.known(shareable, waitingCarried)}</p>
			{/if}
		</ActionRow>
		<!-- Off by default, and says what it is for: the numbers only one face model can read. Held
		     for this screen only, so every export starts without the pictures. -->
		<LabelledRow id="faces.pack-pictures" label={COPY.pack.pictures} help={COPY.pack.picturesHelp}>
			<Switch
				label={COPY.pack.pictures}
				checked={includePictures}
				onCheckedChange={(on: boolean) => (includePictures = on)}
			/>
		</LabelledRow>
	</SettingGroup>

	<ExportPeopleSheet
		bind:this={chooser}
		onsend={(personIds: string[], entryIds: string[]) => void sendOutPack(personIds, entryIds)}
		onproblem={(message: string | null) => (packError = message)}
	/>

	<SettingGroup id="faces.folder" heading={COPY.lists.folder.name} help={COPY.lists.folder.help}>
		<!-- One row: choosing the folder is the press, and the row follows the task Sift runs
		     to read it. See `FolderImportRow`. -->
		<FolderImportRow
			id="faces.folder-import"
			label={COPY.folder.import}
			help={COPY.folder.importHelp}
			action={COPY.folder.importAction}
			busy={COPY.folder.busy}
		/>
	</SettingGroup>
{/snippet}

{#if loadFailed}
	<Problem message="These settings couldn't be loaded. Reload the page to try again." />
{:else}
	<RecognitionPane
		id="faces.scan"
		on={enabled}
		{status}
		statusReady={enabled && feature?.ready === true && !feature?.device_problem}
		statusCaution={caution}
		task="faces"
		{pages}
	>
		{#snippet consent()}
			<!-- The switch itself is under Importing; this says where. -->
			{#if consentEntry}<SwitchPointer entry={consentEntry} on={enabled} hides={offRows} />{/if}
		{/snippet}
		{#snippet work()}
			{#if enabled && feature}
				{#if feature.measured_by_another_model > 0}
					<!-- The family changed. The faces the previous model described are measured again
					     from the pictures Sift kept, by a job on the dashboard; until it has been through
					     them they are out of matching and grouping, and the count says how many. -->
					<p>
						{feature.measured_by_another_model.toLocaleString()} files still have faces measured by the
						previous model. The task Measuring faces with the chosen model measures them again from the
						face crops Sift kept, without opening your files.
					</p>
				{/if}
				{#if feature.references_without_pictures > 0}
					<!-- THE ONE PLACE THE WORD "REFERENCE" IS STILL SAID TO A PERSON. A pack is a file
					     somebody else made; what is inside it is reference faces, some of which arrived
					     as numbers with no picture behind them, and there is no confirmed face here to
					     call them: nobody in this library ever said yes to one. Calling them
					     confirmed faces would name an act that did not happen. -->
					<p>
						{feature.references_without_pictures.toLocaleString()} faces from a file of facial fingerprints
						arrived without their pictures, so the chosen model can't measure them and they no longer
						help recognize anyone. Add a file made with this model.
					</p>
				{/if}
				{#if feature.device_problem}
					<!-- No scan at all. A scan started in this state queues the whole library, fails
					     every file, records nothing and reports that it went through the library: it is
					     not a scan that goes badly, it is one that cannot happen. The server refuses it
					     too, so this is the polite half of the same rule rather than the whole of it. -->
					<p>
						Face recognition is paused. Choose CPU or another GPU under More settings, Run
						recognition on.
					</p>
				{/if}
				{#if modelFetch.outcome && !modelFetch.running}
					<p>{modelFetch.outcome}</p>
				{/if}
			{/if}
		{/snippet}
		{#snippet setup()}
			{#if enabled && feature && !feature.ready}
				<!-- The one control on this screen that makes this device reach the internet, so it is
				     a deliberate press rather than something that happens on enabling. What it
				     downloads is licensed by somebody else on their own terms. It is on the page
				     itself, not one page in, because until it is pressed nothing else here works. -->
				<ActionRow
					id="faces.download"
					label={COPY.models.label}
					help={COPY.models.help}
					action={modelFetch.running ? COPY.models.downloading : COPY.models.download}
					busy={modelFetch.running}
					onclick={() => void fetchTheModels()}
				>
					{#if modelFetch.running}
						<ProgressBar value={modelFetch.fraction * 100} label={COPY.models.bar} />
						<!-- One sentence, from the watcher, which knows a download no worker has picked
						     up yet from one that is under way. -->
						<p class="status">{modelFetch.status}</p>
						<p class="small">{COPY.models.size}</p>
					{/if}
				</ActionRow>
			{:else if enabled && feature?.ready && !feature.device_problem && sweep.jobId}
				<!-- A scan of every file again, started under More settings, followed HERE: this is the
				     page a person comes back to, and a bar one page in is a bar nobody sees. The
				     ordinary scans are the task's, and its When row says what they are doing. -->
				<ActionRow
					id="faces.sweep"
					label={COPY.sweep.label}
					help={COPY.sweep.leave}
					action={stopping ? COPY.sweep.canceling : COPY.sweep.cancel}
					busy={stopping}
					onclick={() => void stopScanningEverything()}
				>
					{#if sweep.total}
						<!-- A bar only once the total is fixed. Until then a falling count, because the
						     denominator is still being discovered and a bar built on it would go
						     backwards. The estimate arrives a minute in and not before: it is the rate
						     over the last minute, and two readings seconds apart is a guess. -->
						<ProgressBar value={(sweep.done / sweep.total) * 100} label="Scanning for faces" />
						<p class="status">
							{sweep.scanned} of {sweep.total} scanned. {COPY.timeLeft(sweep.remaining)}.
						</p>
					{:else}
						<p class="status">
							{#if sweep.left > 0}
								{sweep.left}
								{sweep.left === 1 ? 'file' : 'files'} left to scan. {COPY.timeLeft(
									sweep.remaining
								)}.
							{:else}
								Finding the files to scan&hellip;
							{/if}
						</p>
					{/if}
					<!-- A failure leaves the queue exactly as a finished scan does, so without this the
					     bar counted files that were never opened. The reason comes from the task,
					     because it is the only part anybody can act on. -->
					{#if sweep.failed > 0}
						<p class="status">
							{sweep.failed}
							{sweep.failed === 1 ? 'file' : 'files'} couldn't be read.
							{#if sweep.problem}{sweep.problem}{/if}
						</p>
					{/if}
				</ActionRow>
			{/if}
		{/snippet}
		{#snippet thorough()}
			{@const entry = entries.get(THOROUGH_KEY)}
			{#if entry}
				<SettingRow
					{entry}
					value={entry.value}
					onchange={(next: unknown) => void save(THOROUGH_KEY, next)}
				/>
			{/if}
		{/snippet}
		{#snippet rows()}
			{#if enabled}
				<ActionRow
					id="faces.more"
					label={COPY.more.label}
					help={COPY.more.help}
					action={COPY.more.action}
					onclick={() => openPage(morePage)}
				/>
			{/if}
		{/snippet}
		{#snippet always()}
			{@render lists()}
		{/snippet}
		{#snippet danger()}
			<SettingGroup heading={DANGER_HEADING}>
				<ActionRow
					id="faces.forget"
					label={COPY.forget.label}
					help={COPY.forget.help}
					action={DELETE_FACE_DATA.name}
					destructive
					busy={faceRemoval.running}
					onclick={() => (forgetOpen = true)}
				/>
			</SettingGroup>
		{/snippet}
	</RecognitionPane>
{/if}

<ConfirmDialog
	bind:open={forgetOpen}
	title="Delete all face data?"
	consequence={'Every face Sift found, everything it learned from the ones you confirmed, and ' +
		"every face crop is deleted. Names you added by hand stay. Your files aren't touched. This " +
		"can't be undone; finding the faces again means scanning the library again."}
	confirmLabel="Delete face data"
	onconfirm={() => void forget()}
/>

<style>
	/* No wrapper and no gap over the groups: in ordinary block flow the space each group leaves
	   under itself collapses with the room above the next group's heading, as on every pane. What
	   a group of paragraphs and controls lays out inside itself is this column, under the heading. */
	.stack {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.small {
		font: var(--text-body-sm);
	}

	.status {
		margin: 0;
		color: var(--sift-ink-3);
	}

	.status.ready {
		color: var(--sift-ink);
	}

	.who {
		color: var(--sift-ink);
		text-decoration: none;
	}

	a.who:hover {
		text-decoration: underline;
		text-underline-offset: 3px;
	}

	a.who:focus-visible {
		border-radius: var(--radius-sm);
	}

	/* Somebody Sift identifies who has no page to send you to. Dimmer, so the difference is visible
	   before you try to follow it rather than after. */
	.who.pageless {
		color: var(--sift-ink-3);
	}

	.count {
		color: var(--sift-ink-2);
		font-variant-numeric: tabular-nums;
	}

	.none {
		color: var(--sift-ink-3);
	}

	/* The cap goes on the box that scrolls, drawn by the shared region, hence `:global`. */
	.known-box :global(.scroll-root) {
		max-block-size: var(--settings-list-cap);
	}

	/* The room the list's edges option pulls into: a row's padding on each side, so the words start
	   on the pane's name edge and the hover ground reaches to the scroller's edge, uncut. */
	/* The names the starters are for, one to a line, as the table's names read. */
	.starter-names {
		margin: 0;
		padding: 0;
		list-style: none;
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		font: var(--text-body);
	}

	.known-room {
		padding-inline: var(--space-2) var(--space-3);
	}

	/* Where a person created from facial fingerprints came from, quieter than the name it follows. */
	.came-from {
		display: block;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}
</style>
