<script lang="ts">
	/*
	 * Settings > Music: which song a file uses.
	 *
	 * Two halves of one feature behind one door. The fingerprint is read from a file's own sound on
	 * this device and is what lets Sift match files that share a song; the lookup sends a
	 * fingerprint to AcoustID to learn the song's name, only when it is switched on. The lookup's
	 * switch comes first because it is the one decision here about what leaves this device.
	 *
	 * Two tasks, two rows: making the fingerprints, and asking AcoustID about them. They are two
	 * decisions (one reads files on this device, the other sends something about them away), so
	 * each has its own When, and neither ever runs the other. The lookup's row is drawn while the
	 * lookup is on, with how many files are waiting for it; its presses are on Tasks.
	 *
	 * Beside the lookup's row, Ask again: the files AcoustID did not know, last asked long enough
	 * ago to be worth asking again, counted before the press. It is the lookup task pressed for
	 * those files, so Activity shows it as one press with a lookup per file under it.
	 */
	import MusicLookupSection from './MusicLookup.svelte';
	import { COPY as POINTER } from './SwitchPointer.svelte';
	import { LOOKUP_KEY } from './music-lookup.svelte';
	import { explainAbsentRows, hiddenWhile } from '$lib/settings-ui/settings-anchor.svelte';
	import { SettingLink } from '$lib/components/common';
	import { MusicLookup } from './music-lookup.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import TaskWhen from './TaskWhen.svelte';
	import UnlockSecrets from './UnlockSecrets.svelte';
	import { COPY } from './Music.search';
	import { labelFor } from './sections';
	import ActionRow from './ActionRow.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';

	/* One reader, shared with Unlock: the AcoustID key is sealed like a stash-box's key and locked
	   by the same restart, so unlocking re-reads it. */
	const lookup = new MusicLookup();

	/* The lookup task's row, on Tasks, where its presses are. */
	const LOOKUP_WHEN = 'tasks.music-lookup.when';

	/* How many files Ask again would ask about now, and after how long a file is asked again. */
	const again = $derived(lookup.state?.ask_again ?? 0);
	const againAfter = $derived(lookup.state?.ask_again_after_days ?? 30);

	/* When lookups run, and Ask again, are drawn only while they are on: a link landing on either
	   while they are off rings the switch and says so. */
	$effect(() =>
		explainAbsentRows((key) => {
			const state = lookup.state;
			const switchLabel = lookup.entries[LOOKUP_KEY]?.label;
			if (state === null || state.on || !switchLabel) return null;
			const row =
				key === 'music.acoustid-when'
					? COPY.lookup.label
					: key === 'music.acoustid-again'
						? COPY.again.label
						: null;
			if (!row) return null;
			return { because: hiddenWhile(row, switchLabel, POINTER.off), near: LOOKUP_KEY };
		})
	);

	async function askAgain(): Promise<void> {
		const answered = await lookup.askAgain();
		toasts.show(answered.said, { tone: answered.ok ? 'success' : 'error' });
	}
</script>

{#snippet owed()}
	{#if lookup.state && lookup.state.owed > 0}
		<span>{COPY.lookup.owed(lookup.state.owed)}</span>
	{/if}
	{#if lookup.state && (lookup.state.not_known ?? 0) > 0}
		<span>{COPY.lookup.notKnown(lookup.state.not_known ?? 0)}</span>
	{/if}
	<span
		>{COPY.lookup.runIn}
		<SettingLink section="tasks" setting={LOOKUP_WHEN}>{labelFor('tasks')}</SettingLink>.</span
	>
{/snippet}

<p class="lede">{COPY.lede}</p>

<UnlockSecrets onunlocked={() => void lookup.load()} />

<MusicLookupSection {lookup}>
	{#snippet when()}
		{#if lookup.state?.on}
			<SettingGroup id="music.acoustid-when">
				<TaskWhen task="music-lookup" label={COPY.lookup.label} press={false} more={owed} />
				<ActionRow
					id="music.acoustid-again"
					label={COPY.again.label}
					help={COPY.again.help(again, againAfter)}
					action={COPY.again.action}
					disabled={again === 0 || !lookup.state?.ready}
					busy={lookup.asking}
					onclick={() => void askAgain()}
				/>
			</SettingGroup>
		{/if}
		<SettingGroup
			id="music.fingerprints"
			heading={COPY.fingerprints.heading}
			help={COPY.fingerprints.help}
		>
			<TaskWhen task="music" label={COPY.fingerprints.when.label} press={false} />
		</SettingGroup>
	{/snippet}
</MusicLookupSection>

<style>
	.lede {
		margin: 0 0 var(--space-6);
	}
</style>
