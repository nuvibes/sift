<script lang="ts">
	import { Problem } from '$lib/components/common';
	/* How downloading behaves, set once: the registered rows of Settings, Downloads. */
	import { onMount } from 'svelte';
	import { api } from '$lib/api/client';
	import type { components } from '$lib/api/schema';
	import { fetchSettings, saveSettings, type SettingEntry } from '$lib/settings-ui/settings';
	import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import ActionRow from './ActionRow.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import SettingRow from './SettingRow.svelte';
	import NamingTemplate from './NamingTemplate.svelte';
	import { drilldown } from './drilldown.svelte';
	import { COPY } from './Downloads.search';
	import { drawnOn, explainAbsentRows } from '$lib/settings-ui/settings-anchor.svelte';

	const SECTION = 'Downloads';

	/** The one setting whose automatic answer is a number worth showing. */
	const AT_ONCE = 'download.at_once';

	/* Which settings sit in which group, and in what order within it. */
	const GROUPS: { id: string; heading: string; keys: string[] }[] = [
		{
			id: 'downloading',
			heading: COPY.groups.downloading,
			keys: ['download.quality', 'download.remember', 'download.people_from_usernames']
		},
		{
			id: 'limits',
			heading: COPY.groups.limits,
			keys: ['download.skip_smaller_mb', 'download.skip_larger_mb', 'download.min_free_gb']
		},
		{
			id: 'finished',
			heading: COPY.groups.finished,
			keys: ['download.finished_message', 'download.sound', 'download.sound_volume']
		}
	];

	/** What the More settings page holds, in order. Anything unclaimed joins it at the end. */
	const MORE = [
		'download.at_once',
		'download.bandwidth_kbps',
		'download.pace_ms',
		'download.timeout_seconds',
		'download.retries',
		'download.backoff_seconds'
	];

	/* Settings this pane deliberately does not draw, because something else does. */
	const DRAWN_ELSEWHERE = [
		/* A Chip beside the queue it holds back, on the Downloads SCREEN. */
		'download.paused'
	];

	let entries = $state<SettingEntry[]>([]);

	/* A search or a link naming one of them is sent on to the screen that draws it. */
	$effect(() =>
		explainAbsentRows((key) => {
			const label = entries.find((one) => one.key === key)?.label;
			if (!DRAWN_ELSEWHERE.includes(key) || !label) return null;
			return drawnOn(label, { href: '/downloads', label: COPY.downloadsScreen });
		})
	);
	let problem = $state<string | null>(null);

	/* How many downloads "Default" actually means. */
	let workers = $state<number | null>(null);

	async function load() {
		try {
			const sections = await fetchSettings();
			entries = sections.find((one) => one.name === SECTION)?.settings ?? [];
			problem = null;
		} catch {
			problem = COPY.cannotLoad;
		}
		try {
			const hardware =
				await api.get<components['schemas']['HardwareView']>('/performance/hardware');
			workers = hardware.worker_concurrency;
		} catch {
			// Not worth a message. The label simply stays the word without the number.
		}
	}

	onMount(() => void load());

	/* And again when a setting moves somewhere else: this account in a browser, a second window,
	 * or another admin changing one the installation shares. */
	whenChanged(settingChanges, () => void load());

	/** The settings as drawn, which is the settings as declared plus the one number the server
	 *  could not put in a label. */
	const shown = $derived(
		workers === null
			? entries
			: entries.map((entry) =>
					entry.key === AT_ONCE && entry.automatic_label
						? { ...entry, automatic_label: `${entry.automatic_label} (${workers})` }
						: entry
				)
	);

	/** The entries for a list of keys, in the declared order. */
	function inOrder(keys: string[]): SettingEntry[] {
		return keys
			.map((key) => shown.find((entry) => entry.key === key))
			.filter((entry): entry is SettingEntry => entry !== undefined);
	}

	/** Anything the server declared that nothing here claims. */
	const unclaimed = $derived.by(() => {
		const named = new Set([...GROUPS.flatMap((group) => group.keys), ...MORE, ...DRAWN_ELSEWHERE]);
		return shown.filter((entry) => !named.has(entry.key));
	});

	const onTheMorePage = $derived([...inOrder(MORE), ...unclaimed]);

	function openMore(): void {
		drilldown.open(COPY.more.title, morePage, COPY.more.open);
	}

	/* Claimed in an effect, not once at setup: the unclaimed keys arrive with the settings, and
	   a deep link to one of them has to find the page that draws it. */
	$effect(() => drilldown.own([...MORE, ...unclaimed.map((entry) => entry.key)], openMore));

	/* One optimistic write, put back if the server refuses it. */
	async function save(key: string, value: unknown) {
		const before = entries;
		entries = entries.map((one) => (one.key === key ? { ...one, value } : one));
		try {
			await saveSettings({ [key]: value });
		} catch {
			entries = before;
			toasts.show(COPY.notSaved, { tone: 'error' });
		}
	}
</script>

{#snippet morePage()}
	<SettingGroup help={COPY.more.help}>
		{#each onTheMorePage as entry (entry.key)}
			<SettingRow
				{entry}
				value={entry.value}
				showHelp={true}
				onchange={(next: unknown) => void save(entry.key, next)}
			/>
		{/each}
	</SettingGroup>
{/snippet}

<Problem message={problem} />

{#each GROUPS as group (group.id)}
	{@const rows = inOrder(group.keys)}
	{#if rows.length > 0}
		<SettingGroup heading={group.heading}>
			{#each rows as entry (entry.key)}
				<SettingRow
					{entry}
					value={entry.value}
					showHelp={true}
					onchange={(next: unknown) => void save(entry.key, next)}
				/>
			{/each}
		</SettingGroup>
	{/if}
{/each}

<NamingTemplate />

{#if onTheMorePage.length > 0}
	<SettingGroup>
		<ActionRow
			id="downloads.more"
			label={COPY.more.label}
			help={COPY.more.help}
			action={COPY.more.open}
			onclick={openMore}
		/>
	</SettingGroup>
{/if}
