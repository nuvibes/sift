<script lang="ts">
	/* Which section is showing, and whether a sub-page is open over it. */
	import { Empty } from '$lib/components/common';
	import { session } from '$lib/shell/session.svelte';
	import JobsScreen from '$lib/jobs/JobsScreen.svelte';
	import LibraryScreen from '$lib/library/LibraryScreen.svelte';
	import Users from './Users.svelte';
	import Appearance from './Appearance.svelte';
	import General from './General.svelte';
	import Backup from './Backup.svelte';
	import Editing from './Editing.svelte';
	import DownloadsSection from './DownloadsSection.svelte';
	import Faces from './Faces.svelte';
	import Sites from './Sites.svelte';
	import Semantic from './Semantic.svelte';
	import Watermarks from './Watermarks.svelte';
	import Music from './Music.svelte';
	import GetToKnow from './GetToKnow.svelte';
	import Insights from './Insights.svelte';
	import Maintenance from './Maintenance.svelte';
	import Performance from './Performance.svelte';
	import Playback from './Playback.svelte';
	import StashBoxesPane from './StashBoxesPane.svelte';
	import Privacy from './Privacy.svelte';
	import Shortcuts from './Shortcuts.svelte';
	import Profile from './Profile.svelte';
	import Updates from './Updates.svelte';
	import Documentation from './Documentation.svelte';
	import DrilldownPage from './DrilldownPage.svelte';
	import SettingsTitle from './SettingsTitle.svelte';
	import { drilldown } from './drilldown.svelte';
	import { labelFor, sectionFor, settledSection, SETTINGS_SECTIONS } from './sections';
	import { sectionPlace } from './settings-path';
	import { heldHere, holdPane, READ_ONLY_NOTE } from './read-only';
	import Note from '$lib/components/common/Note.svelte';

	let { section }: { section: string } = $props();

	/* A retired address renders what inherited its work. */
	const showing = $derived(settledSection(section));

	/* Whether this account may open the section at all, from the one list that says so. */
	const mayOpen = $derived(
		SETTINGS_SECTIONS.some((one) => one.id === showing && (!one.admin || session.isAdmin))
	);

	/* The declaration the title is drawn from: the list's own words and icon for this section. */
	const here = $derived(sectionFor(section));

	/* Every settings path on this pane starts with the section's name, the one the list draws. */
	sectionPlace(() => here?.label ?? labelFor(section));

	/* A pane about the computer Sift runs on is read only on a phone: every row inside asks this
	   pane, so the rule is made once. */
	holdPane(() => showing);
	const held = $derived(heldHere(showing));

	/* Leaving a section closes whatever was open over it. */
	$effect(() => {
		void showing;
		drilldown.close();
	});
</script>

<!-- Who may see a section is decided ONCE, by the declaration, and read here. -->
<!--
	The TITLE is drawn here, for every section, from the section's declaration: no pane writes one.
	DRESSED BY: .section-stack (SectionHeading reads it; it draws nothing itself)
-->
<div
	class="section-body section-stack"
	class:away={drilldown.title !== null}
	data-section={showing}
>
	{#if !mayOpen}
		<!-- A pane this account may not open, reached by a panel rather than by its address (the
		     address sends a guest to their own first section). One sentence, never a blank pane. -->
		<Empty scope="page" icon="lock" title={labelFor(section)}
			>Only an administrator can open this page.</Empty
		>
	{:else}
		<SettingsTitle label={here?.label ?? labelFor(section)} icon={here?.icon} />
		{#if held}<div class="held-note"><Note>{READ_ONLY_NOTE}</Note></div>{/if}
		<!-- No `schedule`, `jobs`, `importing`, `logs`, `ledger`, `theater` or `about` branch: those addresses are retired, `showing` is
		     already the section that inherited each (see `MOVED_TO`), and a branch for one could
		     never run. -->
		{#if showing === 'library'}
			<!-- Neither of these is a way of looking at a library, so neither has a rail slot:
			     one says where the files are kept, the other says what the machine is busy
			     with. -->
			<LibraryScreen />
		{:else if showing === 'tasks'}
			<JobsScreen />
		{:else if showing === 'downloads'}
			<DownloadsSection />
		{:else if showing === 'sites'}
			<Sites />
		{:else if showing === 'stash-boxes'}
			<StashBoxesPane />
		{:else if showing === 'editing'}
			<Editing />
		{:else if showing === 'performance'}
			<Performance />
		{:else if showing === 'playback'}
			<Playback />
		{:else if showing === 'shortcuts'}
			<Shortcuts />
		{:else if showing === 'maintenance'}
			<Maintenance />
		{:else if showing === 'privacy'}
			<Privacy />
		{:else if showing === 'faces'}
			<Faces />
		{:else if showing === 'semantic'}
			<Semantic />
		{:else if showing === 'watermarks'}
			<Watermarks />
		{:else if showing === 'music'}
			<Music />
		{:else if showing === 'backup'}
			<Backup />
		{:else if showing === 'updates'}
			<Updates />
		{:else if showing === 'documentation'}
			<Documentation />
		{:else if showing === 'users'}
			<Users />
		{:else if showing === 'profile'}
			<Profile />
		{:else if showing === 'get-to-know'}
			<GetToKnow />
		{:else if showing === 'insights'}
			<Insights />
		{:else if showing === 'appearance'}
			<Appearance />
		{:else if showing === 'general'}
			<General />
		{:else}
			<!-- A declared section with no pane yet. -->
			<Empty scope="block">Nothing here yet.</Empty>
		{/if}
	{/if}
</div>

<DrilldownPage behind={labelFor(section)} />

<style>
	/* Hidden, not unmounted. */
	/* Not `.pane`: the shell's scrolling section already is one, and an e2e locator needs one match. */
	.section-body.away {
		display: none;
	}

	/* The read-only note stands between the title and the pane's own first words, a paragraph's
	   gap from each, so it reads as a fact about the whole pane rather than the lede's first line. */
	.held-note {
		margin-block-end: var(--space-4);
	}

	/* Reading has a maximum measure, and a pane's prose takes the row help's: the lede under the
	 * title, a group's note, a foot line. */
	:global(:where(.section-body, .sub-page) p) {
		max-inline-size: var(--reading-measure);
	}
</style>
