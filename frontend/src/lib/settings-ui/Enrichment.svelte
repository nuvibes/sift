<script lang="ts">
	/*
	 * What a stash-box is allowed to write, field by field.
	 *
	 * Every control on this screen is GENERATED. The server declares one rule per field a stash-box
	 * can fill in, built from the same registry the record pages are drawn from, so a field added
	 * in a later version arrives here with a control and a stored rule, and this file does not
	 * change. There is no list of field names anywhere in it, deliberately: a hand-written list
	 * beside the thing it describes is the drift the registry exists to prevent.
	 *
	 * It draws the Stash-boxes section on the one Recognition layout (`RecognitionPane`): the switch
	 * that is the feature itself and a line saying where it stands, when lookups run (the enrichment
	 * task's When row), the list of stash-boxes the section
	 * hands in, the field rules, and More settings for the rest of the switches. The switches are
	 * told apart from the rules by their keys and by nothing else this file decides.
	 *
	 * The field rules stay ON the page, as the one "For every field" control with its own page of
	 * rows: that is already one level in, and putting it behind More settings would open a page
	 * from a page, which the one sub-page mechanism cannot go back from, so Back would skip the
	 * More settings page and a deep link to a rule would land on a page that does not draw it.
	 */
	import { onMount, type Snippet } from 'svelte';
	import { fetchSettings, saveSettings, type SettingEntry } from '$lib/settings-ui/settings';
	import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import { Problem, Skeleton } from '$lib/components/common';
	import SettingGroup from './SettingGroup.svelte';
	import SettingRow from './SettingRow.svelte';
	import PresetGroup from './PresetGroup.svelte';
	import ActionRow from './ActionRow.svelte';
	import RecognitionPane, { openPage, type SubPage } from './RecognitionPane.svelte';
	import { COPY } from './StashBoxes.search';
	import { COPY as POINTER } from './SwitchPointer.svelte';
	import { explainAbsentRows, hiddenWhile } from '$lib/settings-ui/settings-anchor.svelte';
	import { STASH_SCAN_KEY, type StashBoxes } from './stash-boxes.svelte';

	interface Props {
		/** The section's list of stash-boxes, for the status line. Read, never loaded, here. */
		boxes?: StashBoxes;
		/** The list itself, drawn by the section after the rows. */
		list?: Snippet;
	}

	let { boxes, list }: Props = $props();

	const SECTION = 'Stash-boxes';

	/** The prefix every per-field rule's key opens with. The server builds these keys; this reads
	 *  them, and the two meet at one string rather than at a list of forty. */
	const RULE = 'enrich.';

	/** What each subject is called as a heading here. A key whose subject is not named falls back to
	 *  the subject itself, so a subject added later is drawn rather than dropped. */
	/* Plain plural nouns, the way every heading in Sift's settings is written: "A person" reads as
	   the start of a sentence rather than as the name of a group of rows. Sift's own words: a
	   Site is a Site and never a site. */
	const HEADINGS: Record<string, string> = {
		person: 'People',
		site: 'Sites',
		tag: 'Tags',
		asset: 'Files'
	};

	let entries = $state<SettingEntry[]>([]);
	let values = $state<Record<string, unknown>>({});
	let loading = $state(true);
	let loadFailed = $state(false);

	/* The switches behind More settings, in the order somebody reads them rather than the order
	 * they sort in.
	 *
	 * The server returns every section's settings sorted BY KEY, which is right for a generated
	 * list of thirty-six and wrong for switches that depend on each other. Named here, the way
	 * the Performance pane names its blocks. Anything not named falls in after them in the
	 * server's own order, so a switch added later appears rather than disappearing.
	 *
	 * The switch that decides whether anything is looked up at all is not among them: it is the
	 * consent at the top of the page. When new files are looked up is the enrichment task's When.
	 */
	const SWITCH_ORDER = [
		/* Which stash-box the lookups nobody pressed ask: the routing of the automatic runs. */
		'stash_boxes.auto_box',
		'stash_boxes.duration_tolerance_s',
		/* "Confirm exact matches automatically": it decides only for the runs nobody pressed (a
		   pressed Auto-enrich accepts an exact match on its own), so it sits after the choices
		   that shape every lookup. The label is the server's. */
		'stash_boxes.apply_certain'
		/* `records.show_every_field` is on Appearance, not here: it changes nothing but how much
		   of a record is DRAWN. */
	];

	const consentEntry = $derived(entries.find((one) => one.key === STASH_SCAN_KEY));
	const on = $derived(values[STASH_SCAN_KEY] === true);

	/* More settings and the switches behind it are drawn only while lookups are on: a link landing
	   on one while they are off rings the switch and says so. */
	$effect(() =>
		explainAbsentRows((key) => {
			if (on || loading || !consentEntry?.label) return null;
			const row =
				key === 'stash-boxes.more'
					? COPY.more.label
					: switches.find((one) => one.key === key)?.label;
			if (!row) return null;
			return { because: hiddenWhile(row, consentEntry.label, POINTER.off), near: consentEntry.key };
		})
	);

	const switches = $derived(
		entries
			.filter((one) => !one.key.startsWith(RULE) && one.key !== STASH_SCAN_KEY)
			.toSorted((a, b) => {
				const at = SWITCH_ORDER.indexOf(a.key);
				const bt = SWITCH_ORDER.indexOf(b.key);
				return (at === -1 ? SWITCH_ORDER.length : at) - (bt === -1 ? SWITCH_ORDER.length : bt);
			})
	);
	/* Grouped by what the rule is ABOUT, in the order the server sent them, which is the order the
	   record is read in, so the rules for a person line up with the person's own page. */
	const grouped = $derived.by(() => {
		const out = new Map<string, SettingEntry[]>();
		for (const one of entries) {
			if (!one.key.startsWith(RULE)) continue;
			const subject = one.key.slice(RULE.length).split('.')[0];
			const held = out.get(subject);
			if (held) held.push(one);
			else out.set(subject, [one]);
		}
		return [...out];
	});

	/* The one answer every rule holds, or null where they disagree.
	 *
	 * Worked out here rather than inside `PresetGroup` because this is the file that knows which
	 * keys are in the group: the component owns the question, not the settings. */
	const rules = $derived(entries.filter((one) => one.key.startsWith(RULE)));

	function sharedAcross(across: SettingEntry[]): string | null {
		if (across.length === 0) return null;
		const first = String(values[across[0].key] ?? across[0].default ?? '');
		for (const one of across) {
			if (String(values[one.key] ?? one.default ?? '') !== first) return null;
		}
		return first;
	}

	const shared = $derived(sharedAcross(rules));

	/** The three options, taken from any one rule. They are declared identically on all of them. */
	const strategies = $derived(
		(rules[0]?.choices ?? []).map((value, at) => ({
			value: String(value),
			label: rules[0]?.choice_labels?.[at] ?? String(value)
		}))
	);

	onMount(() => void load());

	/* And again when a setting moves somewhere else: this account in a browser, a second window,
	 * or another admin changing one the installation shares. Every control on this pane writes on
	 * the press and holds nothing unsaved, so a re-read can only put the same value back; see
	 * `scripts/check_settings_followed.js`, which holds every pane to this. */
	whenChanged(settingChanges, () => void load());

	/** Rising, so an older read cannot land over a newer one. */
	let asked = 0;

	/* The skeleton is for the FIRST read only (`loading` starts true and is never set again). A
	 * re-read, which every setting saved anywhere causes, this pane's own switches included, keeps
	 * the pane on screen and swaps the values in when they land; a re-read that fails keeps what is
	 * drawn rather than trading a working pane for an error. */
	async function load() {
		const mine = ++asked;
		const first = entries.length === 0;
		try {
			const sections = await fetchSettings();
			if (mine !== asked) return;
			const section = sections.find((one) => one.name === SECTION);
			// A section that is absent is different from one that is present and empty: absent means
			// the request did not answer for it, and silence must not read as "there is nothing here".
			loadFailed = section === undefined;
			entries = section?.settings ?? [];
			values = Object.fromEntries(entries.map((one) => [one.key, one.value]));
		} catch {
			if (mine === asked && first) loadFailed = true;
		} finally {
			if (mine === asked) loading = false;
		}
	}

	/* Every rule together, in ONE request.
	 *
	 * A loop of thirty-five saves would be thirty-five round trips, thirty-five change broadcasts
	 * and thirty-five chances to end up half applied. `saveSettings` already takes a batch. */
	async function saveAll(next: string) {
		const before = { ...values };
		const batch = Object.fromEntries(rules.map((one) => [one.key, next]));
		values = { ...values, ...batch };
		try {
			await saveSettings(batch);
		} catch {
			values = before;
		}
	}

	async function save(key: string, next: unknown) {
		const before = values[key];
		values = { ...values, [key]: next };
		try {
			await saveSettings({ [key]: next });
		} catch {
			// Put it back. A control that stays where it was pushed after the write failed is a
			// screen telling somebody a thing is true when it is not.
			values = { ...values, [key]: before };
		}
	}

	/* Where lookups stand, in one line under the switch, read from the list of stash-boxes the
	   section already holds. Nothing is claimed while that list is empty: before it has answered
	   that is every page, and once it has, the list itself says there are none. */
	const usable = $derived(
		(boxes?.items ?? []).filter((box) => box.enabled && box.has_key && box.key_ready).length
	);
	const line = $derived.by((): string | null => {
		if (!on) return consentEntry ? COPY.status.off : null;
		const items = boxes?.items ?? [];
		if (items.length === 0) return null;
		if (!items.some((box) => box.enabled)) return COPY.status.allOff;
		if (usable === 0) return COPY.status.noKey;
		return COPY.status.ready(usable);
	});

	const morePage: SubPage = $derived({
		title: COPY.more.label,
		keys: switches.map((entry) => entry.key),
		body: moreSettings
	});
	const pages = $derived(on && switches.length > 0 ? [morePage] : []);
</script>

<!-- More settings: the switches that shape every lookup, for somebody tuning it. -->
{#snippet moreSettings()}
	<SettingGroup>
		{#each switches as entry (entry.key)}
			<SettingRow
				{entry}
				value={values[entry.key]}
				onchange={(next: unknown) => void save(entry.key, next)}
			/>
		{/each}
	</SettingGroup>
{/snippet}

<section aria-label="Stash-box settings">
	{#if loading}
		<Skeleton lines={3} />
	{:else if loadFailed}
		<Problem message="These settings couldn't be loaded. Reload the page to try again." />
	{:else}
		<RecognitionPane
			id="stash-boxes.status"
			{on}
			status={line}
			statusReady={on && usable > 0}
			task="enrichment"
			{pages}
		>
			{#snippet consent()}
				{#if consentEntry}
					<SettingRow
						entry={consentEntry}
						value={values[STASH_SCAN_KEY]}
						onchange={(next: unknown) => void save(STASH_SCAN_KEY, next)}
					/>
				{/if}
			{/snippet}
			{#snippet rows()}
				{#if on && switches.length > 0}
					<ActionRow
						id="stash-boxes.more"
						label={COPY.more.label}
						help={COPY.more.help}
						action={COPY.more.action}
						onclick={() => openPage(morePage)}
					/>
				{/if}
			{/snippet}
			{#snippet always()}
				{@render list?.()}
				{#if rules.length > 0}
					<!--
						Forty settings, one question. As forty menus in four blocks, each carrying
						the same sentence of help, answering them would mean reading the same three
						options forty times. The one control here sets all of them; the fields are
						still real rows against the same registry, one page in, for the person who
						wants a different answer for birthdates than for tags.

						`keys` is what makes a link to one of those rows still work. See
						`drilldown`.
					-->
					<PresetGroup
						heading="Stash-box fields"
						help="What Sift does when a stash-box has a value for a field. Fill in what is missing never changes a value you already have."
						label="For every field"
						masterHelp="Applies to every field."
						options={strategies}
						{shared}
						onchoose={(next) => void saveAll(next)}
						pageTitle="Stash-box fields"
						editLabel="Choose for each field"
						keys={rules.map((entry) => entry.key)}
					>
						{#each grouped as [subject, forSubject] (subject)}
							<SettingGroup heading={HEADINGS[subject] ?? subject}>
								{#each forSubject as entry (entry.key)}
									<SettingRow
										{entry}
										value={values[entry.key]}
										showHelp={false}
										onchange={(next: unknown) => void save(entry.key, next)}
									/>
								{/each}
							</SettingGroup>
						{/each}
					</PresetGroup>
				{/if}
			{/snippet}
		</RecognitionPane>
	{/if}
</section>
