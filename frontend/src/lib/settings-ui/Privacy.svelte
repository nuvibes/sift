<script lang="ts">
	/* The vault's settings: how it hides, and when it shuts by itself. */
	import { onMount } from 'svelte';
	import { ConfirmDialog, Problem } from '$lib/components/common';
	import { api } from '$lib/api/client';
	import { showSettingsSection } from '$lib/settings-ui/settings-view';
	import ActionRow from './ActionRow.svelte';
	import { fetchSettingValues, saveSettings } from '$lib/settings-ui/settings';
	import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import SettingRow from './SettingRow.svelte';
	import { SettingsPanel } from './panel.svelte';
	import { COPY } from './Privacy.search';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { session } from '$lib/shell/session.svelte';
	import {
		CONCEALMENT_KEY,
		LOCK_AFTER_IDLE_KEY,
		LOCK_ON_BLUR_KEY,
		APP_LOCK_AFTER_IDLE_KEY,
		APP_LOCK_ENABLED_KEY,
		LOCK_ON_CLOSE_KEY,
		LOCK_ON_LAUNCH_KEY,
		MAX_IDLE_MINUTES,
		idleMinutesFrom,
		readVaultPreferences,
		type Concealment,
		type VaultPreferences
	} from '$lib/shell/vault-preferences';

	// Registered in the Privacy section by the app itself.
	const GUESTS_MAY_SAVE_KEY = 'guests.can_save_to_device';
	/* How long a sign-in lasts. */
	const SESSION_DAYS_KEY = 'sessions.stay_signed_in_days';
	/* How long Sift keeps its notes about searches. The install's rule, so an admin's row. */
	const SEARCH_RECORDS_KEY = 'search.keep_records_days';
	/* Whether a full path names the account Sift runs as. */
	const HIDE_ACCOUNT_NAME_KEY = 'paths.hide_account_name';
	/* Whether Sift keeps this person's history of use. */
	const KEEP_HISTORY_KEY = 'history.keep';

	let clearing = $state(false);
	let clearingNow = $state(false);

	async function clearHistory() {
		clearingNow = true;
		try {
			await api.del('/insights/history');
			toasts.show(COPY.yourHistory.clear.done);
		} catch {
			toasts.show(COPY.yourHistory.clear.failed, { tone: 'error' });
		} finally {
			clearingNow = false;
		}
	}

	/* The declarations, for the rows. */
	const declarations = new SettingsPanel();

	let prefs = $state<VaultPreferences | null>(null);
	let guestsMaySave = $state(false);
	let loadFailed = $state(false);

	async function load() {
		try {
			prefs = await readVaultPreferences();
			const values = await fetchSettingValues();
			guestsMaySave = Boolean(values.get(GUESTS_MAY_SAVE_KEY));
			loadFailed = false;
		} catch {
			loadFailed = true;
		}
	}

	onMount(() => {
		void declarations.load();
		void load();
	});

	/* And again when a setting moves somewhere else: this account in a browser, a second window,
	 * or another admin changing one the installation shares. */
	whenChanged(settingChanges, () => void load());

	async function saveGuestsMaySave(on: boolean) {
		const previous = guestsMaySave;
		guestsMaySave = on;
		try {
			await saveSettings({ [GUESTS_MAY_SAVE_KEY]: on });
		} catch {
			guestsMaySave = previous;
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	async function save(key: keyof VaultPreferences, value: unknown) {
		if (!prefs) return;
		// Only this key is remembered, and only this key is put back.
		const previous = prefs[key];
		prefs = { ...prefs, [key]: value } as VaultPreferences;
		try {
			await saveSettings({ [key]: value });
		} catch {
			// Snapped back, deliberately. Leaving the control showing a setting that did not save
			// is how somebody comes to believe the vault locks on a timer that was never stored.
			if (prefs) prefs = { ...prefs, [key]: previous } as VaultPreferences;
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	const concealment = $derived(prefs?.[CONCEALMENT_KEY] ?? 'fully_gone');

	function saveIdleMinutes(entered: number) {
		// Null means the box was empty, which is somebody midway through typing rather than a
		// request to turn the timer off.
		const minutes = idleMinutesFrom(entered);
		if (minutes !== null) save(LOCK_AFTER_IDLE_KEY, minutes);
	}

	function saveAppLockMinutes(entered: number) {
		// The same reading as the timer above: an empty box is somebody midway through typing, and
		// 0 is a real answer meaning never.
		const minutes = idleMinutesFrom(entered);
		if (minutes !== null) save(APP_LOCK_AFTER_IDLE_KEY, minutes);
	}
</script>

<section>
	<header>
		<p class="lede">{COPY.lede}</p>
	</header>

	{#if loadFailed}
		<Problem message="These settings couldn't be loaded. Reload the page to try again." />
	{:else if prefs}
		<!--
			Not about Hidden at all: it is how long the BROWSER stays signed in, which is the outer
			door rather than the inner one.
		-->
		{#if session.isAdmin}
			<SettingGroup heading="Sign-in">
				{@const entry = declarations.entry(SESSION_DAYS_KEY)}
				{#if entry}
					<SettingRow
						{entry}
						value={declarations.value(SESSION_DAYS_KEY)}
						onchange={(next: unknown) => void declarations.save(SESSION_DAYS_KEY, next)}
					/>
				{/if}
			</SettingGroup>
		{/if}

		<SettingGroup
			heading="Hidden"
			help="Hidden removes files from your lists, counts and search until you unlock it with your PIN. It guards against someone looking at your screen, not against other users. Your files don't move and aren't encrypted."
		>
			{@const entry = declarations.entry(CONCEALMENT_KEY)}
			{#if entry}
				<SettingRow
					{entry}
					value={concealment}
					onchange={(next: unknown) => save(CONCEALMENT_KEY, next as Concealment)}
				/>
			{/if}
		</SettingGroup>

		<!-- Four answers to one question (when should this shut by itself?), so they are a table
		     rather than four headings with a paragraph each. -->
		<SettingGroup heading="Auto-lock">
			{#each [LOCK_AFTER_IDLE_KEY, LOCK_ON_BLUR_KEY, LOCK_ON_LAUNCH_KEY, LOCK_ON_CLOSE_KEY] as key (key)}
				{@const entry = declarations.entry(key)}
				{#if entry}
					<SettingRow
						{entry}
						value={prefs?.[key as keyof VaultPreferences]}
						onchange={(next: unknown) =>
							key === LOCK_AFTER_IDLE_KEY
								? saveIdleMinutes(Number(next))
								: save(key as keyof VaultPreferences, next)}
					/>
				{/if}
			{/each}
		</SettingGroup>

		<p class="note">
			Ctrl + H locks Hidden right away, from anywhere in Sift, and Ctrl + L locks Sift. Restarting
			Sift always locks Hidden, whatever these settings say.
		</p>

		<SettingGroup
			heading="Sift lock"
			help="Locking Sift covers the whole app, in every tab, until you unlock it, and locks Hidden too. Locking Hidden only hides your hidden files."
		>
			{@const enabledEntry = declarations.entry(APP_LOCK_ENABLED_KEY)}
			{#if enabledEntry}
				<SettingRow
					entry={enabledEntry}
					value={prefs?.[APP_LOCK_ENABLED_KEY]}
					showHelp={true}
					onchange={(next: unknown) => save(APP_LOCK_ENABLED_KEY, next)}
				/>
			{/if}
			{@const idleEntry = declarations.entry(APP_LOCK_AFTER_IDLE_KEY)}
			{#if idleEntry}
				<SettingRow
					entry={idleEntry}
					value={prefs?.[APP_LOCK_AFTER_IDLE_KEY]}
					showHelp={true}
					onchange={(next: unknown) => saveAppLockMinutes(Number(next))}
				/>
			{/if}
		</SettingGroup>

		<!-- Everybody's own: whether Sift keeps the record of what they watch, open and search
		     for, and clearing it. Every User has a history, so every User has this group. -->
		<SettingGroup id="privacy.your_history" heading={COPY.yourHistory.heading}>
			{@const entry = declarations.entry(KEEP_HISTORY_KEY)}
			{#if entry}
				<SettingRow
					{entry}
					value={declarations.value(KEEP_HISTORY_KEY)}
					showHelp={true}
					onchange={(next: unknown) => void declarations.save(KEEP_HISTORY_KEY, next)}
				/>
			{/if}
			<ActionRow
				id="privacy.clear_history"
				label={COPY.yourHistory.clear.label}
				help={COPY.yourHistory.clear.help}
				action={COPY.yourHistory.clear.action}
				actionLabel={COPY.yourHistory.clear.link}
				destructive
				busy={clearingNow}
				onclick={() => (clearing = true)}
			/>
		</SettingGroup>

		<!--
			The one setting on this pane that is about the install rather than about you, so it is the
			one the pane hides from a guest.
		-->
		{#if session.isAdmin}
			<SettingGroup heading="Save to device">
				{@const entry = declarations.entry(GUESTS_MAY_SAVE_KEY)}
				{#if entry}
					<SettingRow
						{entry}
						value={guestsMaySave}
						onchange={(next: unknown) => saveGuestsMaySave(next === true)}
					/>
				{/if}
				<!-- A door to Activity, drawn as every door on a pane is: the bordered press. -->
				<ActionRow
					id="privacy.saved"
					label={COPY.saved.label}
					help={COPY.saved.help}
					action={COPY.saved.open}
					actionLabel={COPY.saved.link}
					onclick={() => showSettingsSection('tasks', 'activity.saved')}
				/>
			</SettingGroup>
			<SettingGroup heading={COPY.locations.heading}>
				{@const entry = declarations.entry(HIDE_ACCOUNT_NAME_KEY)}
				{#if entry}
					<SettingRow
						{entry}
						value={declarations.value(HIDE_ACCOUNT_NAME_KEY)}
						onchange={(next: unknown) => void declarations.save(HIDE_ACCOUNT_NAME_KEY, next)}
					/>
				{/if}
			</SettingGroup>
			<!-- How long searches are kept. The clean-up that deletes the older ones runs in the
			     background on its own, so it has no row to set. -->
			<SettingGroup id="privacy.search_history" heading={COPY.searchHistory.heading}>
				{@const entry = declarations.entry(SEARCH_RECORDS_KEY)}
				{#if entry}
					<SettingRow
						{entry}
						value={declarations.value(SEARCH_RECORDS_KEY)}
						onchange={(next: unknown) => void declarations.save(SEARCH_RECORDS_KEY, next)}
					/>
				{/if}
			</SettingGroup>
		{/if}
	{/if}
</section>

<ConfirmDialog
	bind:open={clearing}
	title={COPY.yourHistory.clear.title}
	consequence={COPY.yourHistory.clear.consequence}
	confirmLabel={COPY.yourHistory.clear.confirm}
	onconfirm={() => void clearHistory()}
/>

<style>
	/* No width of its own: the shell caps the content once, for every pane. */
	.lede,
	.note {
		margin: var(--space-2) 0 0;
	}

	.lede {
		margin-block-end: var(--space-6);
	}

	/* SEPARATE from the rule above on purpose, and `shared-rules.test.ts` is what says so. */
	.note {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}
</style>
