<script lang="ts" module>
	/** The words this block writes itself. The three switches carry their own. */
	export const COPY = {
		heading: 'Recognition',
		help: 'What Sift works out from your files by itself. Each one downloads its models once, from its own page in Settings.',
		models: 'Download the models in',
		device: 'Choose another device in',
		failed: "These switches couldn't be loaded. Reload the page to try again."
	} as const;
</script>

<script lang="ts">
	/*
	 * The three Recognition switches in one place: recognizing faces, describing files for Smart
	 * Search, and reading watermarks, each with where it stands in the shaded box under it.
	 *
	 * The switches are the features' own settings, written through the ordinary settings write, and
	 * this is their ONE door: the Faces, Smart Search and Watermarks sections draw a row saying
	 * whether each is on and linking here (`SwitchPointer`), never a second switch. The line under
	 * each is built by the words those sections use (`facesStatus` and its two siblings), so the
	 * two panes cannot say two things about one feature. Everything else about a feature (its models, its
	 * device, how thorough) stays on its own section, and where a switch cannot run until something
	 * there is done, the box says where, with the way there.
	 *
	 * Turning one on writes the switch and nothing else. When the work then runs is its task's When,
	 * which reads "As files arrive" until somebody chooses otherwise, so a switch never moves a When
	 * somebody set. The switch is not the When: off is a refusal that stops a press too, which no
	 * When can say.
	 */
	import { onMount } from 'svelte';
	import { Problem, SettingLink } from '$lib/components/common';
	import {
		fetchSettings,
		refusalOf,
		saveSettings,
		type SettingEntry
	} from '$lib/settings-ui/settings';
	import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import {
		FACES_DEVICE_KEY,
		FACES_ENABLED_KEY,
		faceSettings,
		type FaceSettings
	} from '$lib/people/faces.svelte';
	import {
		SEMANTIC_DEVICE_KEY,
		SEMANTIC_ENABLED_KEY,
		semanticStatus,
		type SemanticStatus
	} from '$lib/search/semantic.svelte';
	import { availability } from '$lib/jobs/semantic-runs.svelte';
	import {
		WATERMARKS_DEVICE_KEY,
		WATERMARKS_ENABLED_KEY,
		watermarkStatus,
		type WatermarkStatus
	} from '$lib/library/watermarks.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import SettingRow from './SettingRow.svelte';
	import RecognitionNote from './RecognitionNote.svelte';
	import { deviceWords } from './RecognitionPane.svelte';
	import { labelFor } from './sections';
	import { facesStatus } from './Faces.search';
	import { semanticStatusLine } from './Semantic.search';
	import { watermarksStatusLine } from './Watermarks.search';

	interface Props {
		/** The block's heading, where the page it stands on wants another. */
		heading?: string;
	}

	let { heading = COPY.heading }: Props = $props();

	/** What one switch says under itself. */
	interface Said {
		/** The switch's own setting. */
		key: string;
		/** Null while its route has not answered, so nothing is claimed before it has. */
		status: string | null;
		/** False where this device cannot run it at all: no switch, only the sentence saying so. */
		offered: boolean;
		ready: boolean;
		caution: boolean;
		/** What is left to do on its own section: the models, or another device. */
		missing: 'models' | 'device' | null;
	}

	let entries = $state<Map<string, SettingEntry>>(new Map());
	let faces = $state<FaceSettings | null>(null);
	let semantic = $state<SemanticStatus | null>(null);
	let watermarks = $state<WatermarkStatus | null>(null);
	let loadFailed = $state(false);

	/* A read still in flight must not undo a press made while it was in flight. */
	let generation = 0;

	const on = (key: string): boolean => Boolean(entries.get(key)?.value);

	const said = $derived.by((): Said[] => {
		const facesOn = on(FACES_ENABLED_KEY);
		const semanticOn = on(SEMANTIC_ENABLED_KEY);
		const watermarksOn = on(WATERMARKS_ENABLED_KEY);
		const facesReady = facesOn && faces?.ready === true && !faces.device_problem;
		const semanticReady = semanticOn && semantic?.ready === true;
		const watermarksReady = watermarksOn && watermarks?.ready === true;
		return [
			{
				key: FACES_ENABLED_KEY,
				status: facesStatus(
					faces,
					facesOn,
					faces ? deviceWords(entries.get(FACES_DEVICE_KEY), faces.device) : ''
				),
				offered: true,
				ready: facesReady,
				caution: facesOn && faces !== null && !facesReady,
				missing:
					facesOn && faces?.device_problem
						? 'device'
						: facesOn && faces !== null && !faces.ready
							? 'models'
							: null
			},
			{
				key: SEMANTIC_ENABLED_KEY,
				status: semanticStatusLine(
					semantic,
					semanticOn,
					semantic ? deviceWords(entries.get(SEMANTIC_DEVICE_KEY), semantic.device) : ''
				),
				offered: semantic?.supported !== false,
				ready: semanticReady,
				caution:
					semantic?.supported === false || (semanticOn && semantic !== null && !semanticReady),
				missing: semanticOn && semantic?.supported && !semantic.ready ? 'models' : null
			},
			{
				key: WATERMARKS_ENABLED_KEY,
				status: watermarksStatusLine(
					watermarks,
					watermarksOn,
					watermarks ? deviceWords(entries.get(WATERMARKS_DEVICE_KEY), watermarks.device) : ''
				),
				offered: true,
				ready: watermarksReady,
				caution: watermarksOn && watermarks !== null && !watermarksReady,
				missing: watermarksOn && watermarks !== null && !watermarks.ready ? 'models' : null
			}
		];
	});

	onMount(() => {
		void load();
	});

	/* And again when a setting moves somewhere else: the same switch pressed on its own section, in
	   a second window, or by another admin. Every switch here writes on the press and holds nothing
	   unsaved, so a re-read can only put the same value back. */
	whenChanged(settingChanges, () => void load());

	async function load() {
		const mine = generation;
		try {
			const sections = await fetchSettings();
			const all = sections.flatMap((section) => section.settings ?? []);
			// Each feature's own answer, read side by side. One that fails leaves its line unsaid
			// rather than the whole block unloaded: the switch still works without it.
			const [face, meaning, marks] = await Promise.all([
				faceSettings().catch(() => null),
				semanticStatus().catch(() => null),
				watermarkStatus().catch(() => null)
			]);
			if (mine !== generation) return;
			entries = new Map(all.map((entry) => [entry.key, entry]));
			faces = face;
			semantic = meaning;
			watermarks = marks;
		} catch {
			if (mine === generation) loadFailed = true;
		}
	}

	/* The feature's own answer again, after its switch moved: turning one on does not make it ready,
	   so the line is re-read rather than guessed at. */
	async function reread(key: string) {
		if (key === FACES_ENABLED_KEY) faces = await faceSettings().catch(() => faces);
		if (key === SEMANTIC_ENABLED_KEY) {
			semantic = await semanticStatus().catch(() => semantic);
			// The search box offers Smart Search only while it is on, so it hears immediately.
			await availability.refresh();
		}
		if (key === WATERMARKS_ENABLED_KEY)
			watermarks = await watermarkStatus().catch(() => watermarks);
	}

	async function save(key: string, value: boolean) {
		generation += 1;
		const entry = entries.get(key);
		const previous = entry?.value;
		if (entry) entries.set(key, { ...entry, value });
		entries = new Map(entries);
		try {
			await saveSettings({ [key]: value });
			await reread(key);
		} catch (error) {
			if (entry) entries.set(key, { ...entry, value: previous });
			entries = new Map(entries);
			// The server's own words when it sent any, so a refusal says why.
			toasts.show(refusalOf(error), { tone: 'error' });
		}
	}
</script>

<!-- The way to what is left to do, one literal link per feature, so the gate that follows every
     settings link to its row can read each of them. -->
{#snippet whereTo(one: Said)}
	{#if one.missing === 'device'}
		{COPY.device}
		<SettingLink section="faces" setting="faces.device">{labelFor('faces')}</SettingLink>.
	{:else if one.key === FACES_ENABLED_KEY}
		{COPY.models}
		<SettingLink section="faces" setting="faces.download">{labelFor('faces')}</SettingLink>.
	{:else if one.key === SEMANTIC_ENABLED_KEY}
		{COPY.models}
		<SettingLink section="semantic" setting="semantic.download">{labelFor('semantic')}</SettingLink
		>.
	{:else}
		{COPY.models}
		<SettingLink section="watermarks" setting="watermarks.download"
			>{labelFor('watermarks')}</SettingLink
		>.
	{/if}
{/snippet}

{#if loadFailed}
	<Problem message={COPY.failed} />
{:else}
	<SettingGroup id="importing.recognition" {heading} help={COPY.help}>
		{#each said as one (one.key)}
			{@const entry = entries.get(one.key)}
			{#if entry && one.offered}
				<SettingRow
					{entry}
					value={on(one.key)}
					onchange={(next: unknown) => void save(one.key, next === true)}
				/>
			{/if}
			{#if one.status !== null}
				<RecognitionNote status={one.status} ready={one.ready} caution={one.caution}>
					{#if one.missing}
						<p>{@render whereTo(one)}</p>
					{/if}
				</RecognitionNote>
			{/if}
		{/each}
	</SettingGroup>
{/if}
