<script lang="ts">
	import { Button, Empty, SectionHeading, Skeleton, Switch } from '$lib/components/common';
	import { bridge, type UpdateOutcome } from '$lib/bridge';
	import Scroller from '$lib/components/common/Scroller.svelte';
	/* The Updates section: what you are running, what exists, what changed in it, and the button
	 * that installs it: in the desktop app on the device running this library, the one place
	 * that can. */
	import { onMount } from 'svelte';
	import { ApiError, api } from '$lib/api/client';
	import { serverBootId } from '$lib/shell/health';
	import {
		offersServer,
		readServerDesktop,
		updateServer,
		type ServerDesktop
	} from '$lib/desktop/server-shell';
	import { followSwitch } from './follow-switch';
	import type { components } from '$lib/api/schema';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import { session } from '$lib/shell/session.svelte';
	import FactRow from './FactRow.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import { SettingsPanel } from './panel.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import {
		describeLastChecked,
		notesBlocks,
		updates,
		type NotesInline
	} from '$lib/shell/updates.svelte';
	import { COPY } from './Updates.search';
	import { explainAbsentRows } from '$lib/settings-ui/settings-anchor.svelte';
	/* The versions of the programs Sift downloads with, and the yt-dlp check. */
	import DownloadTools from './DownloadTools.svelte';
	import DeviceId from './DeviceId.svelte';
	import Copyable from '$lib/components/record/Copyable.svelte';
	import { pressTask } from '$lib/jobs/tasks.svelte';
	import { jobChanges, whenChanged } from '$lib/library/changes.svelte';

	/* WHEN Sift reads the public release page is the update check task's When, a background task
	   nobody needs to set, so it is drawn on no pane. */
	const DISMISSED_KEY = 'updates.dismissed_version';

	const SOURCE_URL = 'https://github.com/nuvibes/sift';
	const LICENSE_URL = 'https://www.gnu.org/licenses/agpl-3.0.html';
	/* The notices for the tools Sift bundles and the code it derives from, kept beside the
	   source. */
	const NOTICES_URL = `${SOURCE_URL}/blob/HEAD/NOTICE`;

	/* The installed version, from the public endpoint: null until it answers, empty from source. */
	let version = $state<string | null>(null);

	const declarations = new SettingsPanel();
	/** The version whose notice was hidden, or empty. Empty is the ordinary case. */
	const hidden = $derived(String(declarations.value(DISMISSED_KEY) ?? ''));

	/* The hidden notice's row, and the group it stands in, are drawn only while a notice is hidden. */
	$effect(() =>
		explainAbsentRows((key) =>
			(key === DISMISSED_KEY || key === 'updates.notice') &&
			session.isAdmin &&
			!declarations.loading &&
			!hidden
				? { because: COPY.nothingHidden }
				: null
		)
	);

	/* The release notes are drawn only while a new version is out. */
	const NO_NOTES = 'Release notes are shown here when a new version is out.';
	$effect(() =>
		explainAbsentRows((key) =>
			key === 'notes-heading' && current !== null && !updates.waiting ? { because: NO_NOTES } : null
		)
	);

	/* Clearing the stored version is what un-hides the notice: the server compares the newest
	   release against this, so an empty string matches nothing and the notice comes back. */
	async function showItAgain() {
		await declarations.save(DISMISSED_KEY, '');
		await updates.load();
	}

	/* What the application in front of you is, which is a different question from what the
	 * library is running. */
	const here = $derived(updates.here);

	onMount(() => {
		if (session.isAdmin) {
			void updates.load();
			void declarations.load();
			void readServerDesktop().then((answer) => (desk = answer));
		}
		void updates.readHere();
		void (async () => {
			try {
				const report = await api.get<components['schemas']['VersionReport']>('/update/version');
				version = report.version;
			} catch {
				// Left null. The foot still says everything else it has to say.
			}
		})();
	});

	const current = $derived(updates.state);
	const lastChecked = $derived(current ? describeLastChecked(current.last_checked) : '');
	/* The verdict for THIS copy: the state's own is the library's machine against the feed, which
	 * says nothing about a client two releases behind it. */
	const behindHere = $derived(updates.behindHere);

	/* WHETHER SIFT LOOKS FOR A NEW VERSION ON ITS OWN: the one request it makes to the internet
	 * without being asked. */
	const CHECK_SWITCH = 'updates.check_for_new_versions';
	const CHECK_WHEN = 'tasks.update-check.when';
	const checkWhen = $derived(declarations.value(CHECK_WHEN));
	const checksOnItsOwn = $derived(checkWhen !== undefined && checkWhen !== 'press');

	async function setChecking(next: boolean): Promise<void> {
		await declarations.save(CHECK_SWITCH, next);
		await declarations.load();
	}

	/* Check now: the update check's own run, through the one press every task has. */
	let checking = $state(false);
	async function checkNow(): Promise<void> {
		checking = await pressTask('update-check', 'now');
	}
	whenChanged(jobChanges, () => {
		if (checking) void updates.load();
	});
	/* The run is over when the check's time MOVES, not when the answer is merely read again: the
	   bell rings as the run is queued too, and that re-read carries the old time in a new
	   object; ending the wait there would leave Last checked where it was until a reload. */
	let checkedAt: number | null | undefined;
	$effect(() => {
		const at = current?.last_checked;
		if (at === checkedAt) return;
		checkedAt = at;
		checking = false;
	});

	/* Two computers, said whenever there are two, not only when the numbers differ. */
	const split = $derived(here !== null && current !== null);

	/* Whether THIS window can install: only the desktop app, a browser has no installer to run. */
	const canInstall = bridge.canApplyUpdate();
	const installsHere = $derived(canInstall && (here === null || behindHere));

	const blocks = $derived(current ? notesBlocks(current.notes, current.release_page ?? '') : []);

	let installing = $state(false);

	/** Every refusal in its own words. "It did not work" is the one message worth never showing. */
	const REFUSALS: Record<string, string> = COPY.refusals;

	/* The computer running Sift, where this window cannot install and the Sift app there answers
	 * through the server (`server-shell.ts`). */
	let desk = $state<ServerDesktop | null>(null);
	let installingThere = $state<'checking' | 'confirm-there' | null>(null);

	/** Half an hour: somebody has to walk to that computer and finish the installer. */
	const INSTALL_WAIT_MS = 30 * 60_000;

	async function installThere() {
		installingThere = 'checking';
		const before = await serverBootId();
		try {
			const outcome = await updateServer();
			if (!outcome.ok) {
				installingThere = null;
				toasts.show(REFUSALS[outcome.reason ?? 'failed'] ?? REFUSALS.failed!, { tone: 'error' });
				return;
			}
		} catch (error) {
			installingThere = null;
			const said = error instanceof ApiError ? (error.detail ?? error.message) : REFUSALS.failed!;
			toasts.show(said, { tone: 'error' });
			return;
		}
		installingThere = 'confirm-there';
		const back = await followSwitch(before, {
			limitMs: INSTALL_WAIT_MS,
			arrive: () => location.reload()
		});
		if (!back) {
			installingThere = null;
			toasts.show(COPY.there.slow, { tone: 'error' });
		}
	}

	async function install() {
		installing = true;
		try {
			const outcome: UpdateOutcome = await bridge.applyUpdate();
			if (outcome.ok) {
				toasts.show(COPY.opening, { tone: 'success' });
				return;
			}
			toasts.show(REFUSALS[outcome.reason ?? 'failed'] ?? REFUSALS.failed!, { tone: 'error' });
		} finally {
			installing = false;
		}
	}
</script>

<!-- The version, copied by pressing it: the one copy helper the file name uses. -->
{#snippet sayVersion(number: string | null | undefined)}
	{#if number}
		<Copyable text={number} what="The version"><strong>Sift {number}</strong></Copyable>
	{:else}
		<strong>Sift {COPY.fromSource}</strong>
	{/if}
{/snippet}

{#snippet runs(parts: NotesInline[])}
	{#each parts as part, index (index)}
		{#if part.kind === 'strong'}
			<strong>{part.text}</strong>
		{:else if part.kind === 'code'}
			<code>{part.text}</code>
		{:else if part.kind === 'link'}
			<a href={part.href} target="_blank" rel="noopener noreferrer">{part.text}</a>
		{:else}
			{part.text}
		{/if}
	{/each}
{/snippet}

{#if !session.isAdmin || (updates.loaded && !current)}
	<!-- What is running, for whoever the check's answer is not drawn for: a guest, or an admin whose
	     check could not answer. Said the way the answer says it, and on a client both copies. -->
	<section class="versions" id="updates.version">
		{#if here !== null}
			<p class="running">{COPY.thisCopy} <strong>{here}</strong>.</p>
		{/if}
		<p class="running">
			{here === null ? COPY.running : COPY.elsewhere}
			{#if version === null}{COPY.readingVersion}{:else}{@render sayVersion(version)}{/if}
		</p>
	</section>
{/if}

{#if !session.isAdmin}
	<!-- Nothing more above the foot: the check is an admin's, and the foot is everybody's. -->
{:else if !updates.loaded}
	<Skeleton lines={3} />
{:else if current}
	<section class="versions" id="updates.version">
		{#if split}
			<!-- TWO COMPUTERS, AND THE SCREEN HAS TO SAY WHICH IS WHICH. -->
			<p class="running">
				{COPY.thisCopy} <strong>{here}</strong>.
			</p>
			<p class="running">
				{COPY.elsewhere}
				{@render sayVersion(current.current_version)}
			</p>
			<p class="quiet">{COPY.onlyHere}</p>
		{:else}
			<p class="running">
				{COPY.running}
				{@render sayVersion(current.current_version)}
			</p>
		{/if}
		{#if behindHere}
			<p class="available">{COPY.availableHere(current.latest_version ?? '')}</p>
		{:else if current.update_available}
			<p class="available">{COPY.available(current.latest_version ?? '')}</p>
		{:else if current.latest_version}
			<p class="quiet">{COPY.newest}</p>
		{:else if !lastChecked}
			<!-- Not an error. Sift works with no outbound network at all, and a check that could not
			     happen is an ordinary thing rather than something to alarm anybody about. Drawn only
			     with no last check to show: under "Last checked" it would contradict the line below it. -->
			<p class="quiet">{COPY.unchecked}</p>
		{/if}
		{#if lastChecked}
			<p class="quiet small">{COPY.lastChecked(lastChecked)}</p>
		{/if}
	</section>

	{#if session.isAdmin && checkWhen !== undefined}
		<SettingGroup>
			<LabelledRow
				id="updates.check_for_new_versions"
				label={COPY.checking.label}
				help={checksOnItsOwn ? COPY.checking.on : COPY.checking.off}
				besideField
			>
				<Button tone="secondary" icon="sync" disabled={checking} onclick={() => void checkNow()}
					>{COPY.checking.now}</Button
				>
				<Switch
					checked={checksOnItsOwn}
					label={COPY.checking.label}
					onCheckedChange={(next) => void setChecking(next)}
				/>
			</LabelledRow>
		</SettingGroup>
	{/if}

	{#if updates.waiting}
		{#if blocks.length > 0}
			<section class="notes" aria-labelledby="notes-heading">
				<SectionHeading id="notes-heading">{COPY.notes}</SectionHeading>
				<Scroller>
					<div class="notes-body">
						{#each blocks as block, index (index)}
							{#if block.kind === 'heading'}
								<!-- A release's own headings are content, not the pane's: drawn as bold lines so
								     the pane keeps one heading style, the frame's. -->
								<p class="note-heading" data-level={Math.min(block.level, 3)}>
									{@render runs(block.inline)}
								</p>
							{:else if block.kind === 'paragraph'}
								<p>{@render runs(block.inline)}</p>
							{:else if block.kind === 'list' && block.ordered}
								<ol>
									{#each block.items as item, at (at)}
										<li>{@render runs(item)}</li>
									{/each}
								</ol>
							{:else if block.kind === 'list'}
								<ul>
									{#each block.items as item, at (at)}
										<li>{@render runs(item)}</li>
									{/each}
								</ul>
							{:else if block.kind === 'code'}
								<pre><code>{block.text}</code></pre>
							{/if}
						{/each}
					</div>
				</Scroller>
			</section>
		{/if}

		<section class="apply" aria-labelledby="apply-heading">
			<SectionHeading id="apply-heading">{COPY.install.name}</SectionHeading>
			{#if installsHere}
				<!-- The application form. One button, and it still does not update itself: it downloads,
				     checks the download really is Sift's, and opens the installer for you to agree to. -->
				<p>{COPY.app}</p>
				<Button tone="primary" icon="download" onclick={install} disabled={installing}>
					{installing ? COPY.downloading : COPY.downloadInstall(current.latest_version ?? '')}
				</Button>
				<p class="quiet small">
					{COPY.windowsWarns.before} <strong>{COPY.windowsWarns.moreInfo}</strong>
					{COPY.windowsWarns.and} <strong>{COPY.windowsWarns.runAnyway}</strong>{COPY.windowsWarns
						.after}
				</p>
			{:else if offersServer(desk)}
				<!-- Installed on the computer running Sift, from here: its installer opens there. -->
				<p>{COPY.there.says(desk.machine)}</p>
				<Button
					tone="primary"
					icon="download"
					onclick={() => void installThere()}
					disabled={installingThere !== null}
				>
					{installingThere === 'checking'
						? COPY.there.checking
						: COPY.there.action(current.latest_version ?? '', desk.machine)}
				</Button>
				{#if installingThere === 'confirm-there'}
					<p role="status">{COPY.there.agreeThere(desk.machine)}</p>
				{/if}
			{:else}
				<p>{COPY.elsewhereInstall}</p>
			{/if}
			{#if current.release_page}
				<p class="small">
					<a href={current.release_page} target="_blank" rel="noopener noreferrer"
						>{COPY.releasePage}</a
					>
				</p>
			{/if}
			{#if !current.dismissed}
				<Button tone="link" onclick={() => updates.dismiss()}>{COPY.hide}</Button>
			{:else}
				<!-- Says where to undo it. There is a button for that further down this same pane, and
				     a sentence reporting a state with no way out of it is a dead end. -->
				<p class="quiet small">{COPY.hidden}</p>
			{/if}
		</section>
	{/if}
{:else if updates.unavailable}
	<Empty scope="block">{COPY.unavailable}</Empty>
{/if}

{#if session.isAdmin && hidden}
	<!--
		A BUTTON, not a box. A text field to type a version number into (the version you hid) is not a
		preference anybody can hold an opinion about and not a string anybody can type correctly.
	-->
	<SettingGroup id="updates.notice" heading={COPY.notice}>
		<LabelledRow
			id={DISMISSED_KEY}
			label={declarations.entry(DISMISSED_KEY)?.label ?? COPY.hiddenRow.label}
			help={COPY.hiddenRow.help(hidden)}
		>
			<Button tone="secondary" onclick={() => void showItAgain()}>{COPY.hiddenRow.button}</Button>
		</LabelledRow>
	</SettingGroup>
{/if}

{#if session.isAdmin}
	<!-- The programs Sift downloads with, and whether a newer yt-dlp is out: facts about what is
	     running, under the update they arrive with. -->
	<DownloadTools />
	<!-- What a swap tells another Sift about this install. Admin only, as every swap route is. -->
	<DeviceId />
{/if}

<!-- THE FOOT: the licence and the source, for everybody. -->
<SettingGroup id="updates.license" heading={COPY.license.heading} help={COPY.license.help}>
	<FactRow label={COPY.license.label}>
		<a href={LICENSE_URL} target="_blank" rel="noreferrer">{COPY.license.name}</a>
	</FactRow>
	<FactRow label={COPY.license.source}>
		<a href={SOURCE_URL} target="_blank" rel="noreferrer">{SOURCE_URL}</a>
	</FactRow>
	<FactRow label={COPY.license.notices} help={COPY.license.noticesHelp}>
		<a href={NOTICES_URL} target="_blank" rel="noreferrer">{COPY.license.noticesLink}</a>
	</FactRow>
</SettingGroup>

<style>
	section {
		margin-bottom: var(--space-5);
	}

	p {
		margin: 0 0 var(--space-2);
		color: var(--sift-ink-2);
	}

	.running strong {
		color: var(--sift-ink);
	}

	/* The two sections this pane is: what you are running, and what to do about it. */
	.versions {
		margin-block-end: var(--space-6);
	}

	.apply {
		margin-block-start: var(--space-6);
	}

	.available {
		color: var(--sift-ink);
		font-weight: 600;
	}

	.small {
		font: var(--text-body-sm);
	}

	.notes-body {
		overflow-wrap: anywhere;
		padding: var(--space-3);
		border: 1px solid var(--sift-line);
		border-radius: var(--radius-md);
		background: var(--sift-surface-2);
		color: var(--sift-ink-2);
	}

	.notes-body .note-heading {
		margin: var(--space-3) 0 var(--space-2);
		font: var(--text-body);
		font-weight: 600;
		color: var(--sift-ink);
	}

	.notes-body .note-heading:first-child {
		margin-block-start: 0;
	}

	.notes-body ul,
	.notes-body ol {
		margin: 0 0 var(--space-2);
		padding-inline-start: var(--space-5);
	}

	.notes-body pre {
		white-space: pre-wrap;
		margin: 0 0 var(--space-2);
	}

	/* The cap moves onto the box that SCROLLS, and the rule is `:global` because that box is
	 * rendered by the shared region rather than written here. */
	.notes :global(.scroll-root) {
		max-block-size: 20rem;
	}

	a {
		color: var(--sift-accent-text);
	}

	a:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
		border-radius: var(--radius-sm);
	}
</style>
