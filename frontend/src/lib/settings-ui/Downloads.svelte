<script lang="ts">
	import { Problem } from '$lib/components/common';
	/* How downloading behaves, set once: the registered rows of Settings, Downloads.
	 *
	 * THE PAGE DECIDES FOR THIS DOWNLOAD; THIS PANE DECIDES THE DEFAULT FOR EVERY DOWNLOAD. The
	 * Downloads page's switch starts from `download.remember` here and is sent with one paste; it
	 * never writes it. This is the only place it is changed.
	 *
	 * Grouped by what somebody comes for, in the order they reach for it: what gets downloaded,
	 * the limits that stop a download, what a finish says and sounds like, then where a download
	 * lands and what it is called (`NamingTemplate`, which owns the per-Site list). The knobs
	 * somebody should be able to reach and not be invited to turn (how many at the same time, the
	 * speed limit, the pacing, the timeout, the retries and the wait after a rate limit) are one
	 * row at the foot, "More settings", which
	 * opens a page of their own: the sub-page Importing uses for its groups (`drilldown`), so a deep
	 * link to one of them opens the page first and rings the row.
	 *
	 * What is NOT on this pane, and where it is: the tunnels (Settings, Sites shows the tunnels
	 * and which Site takes which), the list of Sites Sift knows (Sites), where a file SAVED OUT of
	 * Sift goes (Folders, a folder on this device rather than a library folder), the download tools
	 * and the yt-dlp check (Updates), and the detailed download log (the log's own tab, since it
	 * changes what the log records and nothing about a download).
	 *
	 * Every control is drawn from what the server declared about the setting, so a setting added
	 * later appears here, with the right control and the right words, without this file being
	 * touched. The groups below name their members by key; anything the server declares that is
	 * not named lands on the More settings page rather than vanishing.
	 */
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

	/* Which settings sit in which group, and in what order within it.
	 *
	 * Written out rather than derived from the key's prefix: `download.at_once` and
	 * `download.skip_smaller_mb` share a prefix and answer different questions: one is how hard
	 * downloading leans on the connection, the other is what is worth fetching at all.
	 */
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

	/*
	 * Settings this pane deliberately does not draw, because something else does.
	 *
	 * WITHOUT THIS LIST, LEAVING A KEY OUT OF `GROUPS` DRAWS IT ANYWAY. The catch-all below puts
	 * anything no group claims on the More settings page, so "removed from this pane" and
	 * "forgotten by whoever wrote a group" look identical to the code, and a key drawn on another
	 * screen would be drawn here too: two controls for one setting, on two screens.
	 *
	 * Saying it out loud is what separates the two cases. A key here is a decision; a key in
	 * neither this nor `GROUPS` is still an oversight, and still lands where somebody will see it.
	 */
	const DRAWN_ELSEWHERE = [
		/* A Chip beside the queue it holds back, on the Downloads SCREEN. It is an action taken
		   because of what the queue is doing, not a preference set in advance, and this pane is not
		   where somebody is standing when they want it. */
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

	/* How many downloads "Default" actually means.
	 *
	 * Left at nothing, downloads share the machine's worker count, and that number is worked out
	 * from the hardware at run time, so the setting's own registration cannot know it and the box
	 * could only say "Default", which is a word somebody has to go and look up. It is asked for
	 * separately and folded into the label below; failing to get it leaves the plain word, which is
	 * what the server already sent. */
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
	 * or another admin changing one the installation shares. Every control on this pane writes on
	 * the press and holds nothing unsaved, so a re-read can only put the same value back; see
	 * `scripts/check_settings_followed.js`, which holds every pane to this.
	 *
	 * The worker count is re-read with them rather than left alone: "Default" means the number the
	 * machine worked out, and that number follows the performance settings, so a label saying it
	 * would otherwise go on quoting the figure from before the change. */
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

	/**
	 * Anything the server declared that nothing here claims.
	 *
	 * It goes on the More settings page rather than nowhere. A setting registered later and
	 * forgotten here would otherwise exist, be read, take effect, and appear on no screen, so its
	 * default would be the whole of the decision.
	 */
	const unclaimed = $derived.by(() => {
		const named = new Set([...GROUPS.flatMap((group) => group.keys), ...MORE, ...DRAWN_ELSEWHERE]);
		return shown.filter((entry) => !named.has(entry.key));
	});

	const onTheMorePage = $derived([...inOrder(MORE), ...unclaimed]);

	function openMore(): void {
		drilldown.open(COPY.more.title, morePage, COPY.more.open);
	}

	/* Claimed in an effect, not once at setup: the unclaimed keys arrive with the settings, and a
	   deep link to one of them has to find the page that draws it. The teardown releases the claim
	   when the list changes and when the pane goes away. */
	$effect(() => drilldown.own([...MORE, ...unclaimed.map((entry) => entry.key)], openMore));

	/* One optimistic write, put back if the server refuses it. A control that shows a state the
	   server does not hold is worse than one that flickers. */
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
