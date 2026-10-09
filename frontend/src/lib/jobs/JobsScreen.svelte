<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	import { onMount, tick } from 'svelte';
	import { replaceState } from '$app/navigation';
	import { page } from '$app/state';
	import {
		Badge,
		Button,
		ConfirmDialog,
		ContextMenuItem,
		DataRow,
		DataRows,
		Empty,
		MenuButton,
		Problem,
		ProgressBar,
		Select,
		SettingLink,
		Skeleton
	} from '$lib/components/common';
	import { openAssetInstead } from '$lib/player/asset-view';
	import { LABELS, type BadgeState } from '$lib/components/common/Badge.svelte';
	import Tabs from '$lib/components/common/Tabs.svelte';
	import ContextMenuGroup from '$lib/components/common/ContextMenuGroup.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { jobChanges, whenChanged } from '$lib/library/changes.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import {
		ACTIVITY_ACTIONS,
		ACTIVITY_CARD,
		ACTIVITY_CARD_ACTIONS,
		ACTIVITY_COLUMNS,
		chores,
		nowState,
		pile,
		passes,
		shownState,
		stepsLeft,
		stepsLine,
		type ChoreView,
		type Job,
		type Pass,
		type PassPart
	} from './family';
	import { canCancel, canRetry, offeredWhileViewing, startsIn } from './labels';
	import { OWNED, addressOf, landingFor, type ActivityTab } from './tabs';
	import { COPY } from './JobsScreen.search';
	import { currentTurboMode } from '$lib/components/shell/turbo-mode';
	import Ledger from '$lib/settings-ui/Ledger.svelte';
	import UnlockField from '$lib/settings-ui/UnlockField.svelte';
	import { session } from '$lib/shell/session.svelte';
	import Logs from '$lib/settings-ui/Logs.svelte';
	import ScheduledTasks from '$lib/settings-ui/ScheduledTasks.svelte';
	import { drilldown } from '$lib/settings-ui/drilldown.svelte';
	import { exactly, sayAgo } from '$lib/shell/when';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import SectionHeading from '$lib/components/common/SectionHeading.svelte';
	import Note from '$lib/components/common/Note.svelte';
	import { QUEUE_PAGE, Queue, type JobState, type Which } from './queue.svelte';
	import { pressTasks, taskList } from './tasks.svelte';
	import { leftOut } from './left-out.svelte';
	import TasksLeftOut from '$lib/settings-ui/TasksLeftOut.svelte';
	import { kindChoices } from './kinds';
	import Pager from '$lib/components/common/Pager.svelte';

	/* Settings > Tasks and Activity: when each task runs, what Sift is doing, what happened and what
	 * the program wrote down. Admin-only by its endpoints, not by this screen. The tabs filter in
	 * place (this pane is inside the Settings sheet); which one an address opens is `tabs.ts`. */
	const landed = landingFor(
		typeof location === 'undefined' ? '' : location.search,
		typeof location === 'undefined' ? '' : location.hash
	);
	let tab = $state<ActivityTab>(landed.tab);
	/* The act History opens filtered to, where a door asked for one. History is keyed on it, so a
	   second door asking for another act opens a fresh list rather than a stale one. */
	let historyVerb = $state<string | undefined>(landed.verb);
	/* And whether it opens on the decisions alone, keyed the same way. */
	let historyDecisions = $state(landed.decisions ?? false);

	const TABS: { id: ActivityTab; label: string }[] = [
		{ id: 'tasks', label: COPY.tabs.tasks },
		{ id: 'history', label: COPY.tabs.history },
		{ id: 'log', label: COPY.tabs.log }
	];

	/** Show a tab, and put its address in the bar so a refresh or a copied link comes back to it.
	 *  `replaceState` and not a push: the panel is one stop in the history, and which of its tabs
	 *  was showing is not a place somebody navigated to: the rule `showSettingsSection` keeps. */
	function choose(next: ActivityTab, verb?: string) {
		tab = next;
		historyVerb = next === 'history' ? verb : undefined;
		historyDecisions = false;
		if (next === 'tasks') void queue.refresh(); // read only while its tab shows
		if (typeof location === 'undefined') return;
		try {
			replaceState(addressOf(next), page.state);
		} catch {
			// Before the router has started, or in a test without one: the tab still changes.
		}
	}

	/* EVERY KEY DRAWN ON A TAB IS OWNED BY THAT TAB, through the frame's own mechanism: a deep
	   link asks the key's owner to open its place before it hunts for the row (`revealSetting`).
	   So a link to the download switch, the older address of "How long tasks take", or a search
	   result for "Saved to a device" lands on the right tab, filtered, and rings. */
	onMount(() => {
		/* Tasks draws a row per task, from the server, so no table lists them: a key on this
		   section no other tab owns is a Tasks row, and a link to one opens Tasks first. */
		const tasksTab = drilldown.ownSection('tasks', () => {
			if (tab !== 'tasks') [tab, historyVerb, historyDecisions] = ['tasks', undefined, false];
		});
		const released = Object.entries(OWNED).map(([key, landing]) =>
			drilldown.own([key], () => {
				const decisions = landing.decisions ?? false;
				if (tab !== landing.tab || historyVerb !== landing.verb || historyDecisions !== decisions) {
					tab = landing.tab;
					historyVerb = landing.verb;
					historyDecisions = decisions;
				}
			})
		);
		return () => {
			tasksTab();
			for (const release of released) release();
		};
	});

	const queue = new Queue();

	// The clock the ages are measured against. A row saying "2m ago" is only true for a minute, and a
	// list that only re-reads its clock when the server pushes would sit at "2m ago" for an hour if
	// the queue went quiet.
	let now = $state(Math.floor(Date.now() / 1000));

	let compact = $state(false);
	let confirming = $state<Job | null>(null);
	let confirmOpen = $state(false);

	/* Read when its tab opens, and again when the connection says the queue moved while it shows. */
	whenChanged(jobChanges, () => void (tab === 'tasks' && queue.refresh()));

	/* A slow beat while anything is outstanding: the counts are the library's and the estimate the
	   ledger's, and neither moves the queue, so the socket alone would leave them stale. */
	const ASK_EVERY_MS = 5000;

	onMount(() => {
		if (tab === 'tasks') void queue.refresh();
		const clock = setInterval(() => (now = Math.floor(Date.now() / 1000)), 1000);
		// Only while Activity is showing: the other tabs draw nothing the queue decides.
		const beat = setInterval(() => {
			if (tab === 'tasks' && outstanding > 0) void queue.refresh();
		}, ASK_EVERY_MS);
		const again = () => {
			if (tab === 'tasks' && document.visibilityState === 'visible') void queue.refresh();
		};
		window.addEventListener('focus', again);
		document.addEventListener('visibilitychange', again);
		return () => {
			clearInterval(clock);
			clearInterval(beat);
			window.removeEventListener('focus', again);
			document.removeEventListener('visibilitychange', again);
		};
	});

	/* Families, newest first, under every kind; a kind's tasks, flat, while one is chosen. */
	const jobs = $derived(queue.listed?.jobs ?? []);
	const counts = $derived(queue.page?.counts ?? {});
	/* From the rail's own read, so a press there answers here immediately too. Activity is an admin's. */
	const eco = $derived(currentTurboMode(true));

	// Only the states that exist right now, so the row of tallies is not mostly zeroes. Ordered by the
	// list rather than by the object's keys: what comes back from the server is a tally, and the order
	// of its keys is not something to hang a stable UI on.
	const STATES: JobState[] = [
		'running',
		'queued',
		'blocked',
		'paused',
		'failed',
		'canceled',
		'done'
	];

	/* The tab for "everything", which is the absence of a filter rather than a state. Named once
	   here because the strip works in ids and the queue works in states, and the two meet on this
	   one word. */
	const ALL = 'all';
	/* The states that have something in them, and the one being looked at, even once it is empty.
	   Left out, the tab you are on would vanish from under you the moment its last task finished,
	   and the row would light nothing while the list underneath stayed filtered to it. */
	const present = $derived(
		STATES.filter((state) => (shownCounts[state] ?? 0) > 0 || state === queue.filter)
	);

	/* THE TYPE NARROWING: one kind of task, or every kind, by the names the server declares and
	   one "Older tasks" for every kind this version no longer runs. See `kinds.ts`. */
	const EVERY_KIND = 'all';
	/* Every name a page has given a kind, so a chosen kind whose last row went keeps its words. */
	const remembered: Record<string, string> = {};
	$effect(() => {
		Object.assign(remembered, queue.page?.names ?? {});
	});
	const kindChoicesShown = $derived([
		{ value: EVERY_KIND, label: COPY.list.everything },
		...kindChoices(queue.page, queue.kind, COPY.list.older, remembered)
	]);

	/* The strip's numbers are the server's (`tallies`), in the universe the list draws; the bulk
	   actions read `counts` and are not offered while a kind is chosen. */
	const shownCounts = $derived<Record<string, number>>(queue.page?.tallies ?? {});
	const shownTotal = $derived(shownCounts.all ?? 0);

	/* What the waiting mark on Blocked says, to a pointer and to anybody reading the page out. */
	const BLOCKED_WAITS = 'Waiting on you \u2014 these run once what they need is in place';

	/* What the list under the strip is called, so each tab can say it controls it. */
	const listId = $props.id();

	/** The failed ones in the list below, brought into view: where a failed run's row says why. */
	async function showFailed() {
		// After the failed page has landed and drawn: scrolled while the list is still a skeleton,
		// the list grows under the reader and the rows they were sent to are off the screen.
		await queue.setFilter('failed');
		await tick();
		document.getElementById(listId)?.scrollIntoView({ block: 'start' });
	}

	/* One bar per family, from the page the socket pushes, with each task's When and the files its
	   products left out; the arithmetic and the words are in `passes`, where a test can reach them. */
	const whens = $derived(Object.fromEntries(taskList.tasks.map((one) => [one.id, one.when])));
	const queues = $derived(passes(queue.page, { whens, leftOut: leftOut.counts }));
	$effect(() => leftOut.follow(queues.map((one) => one.moving).join()));
	const chored = $derived(chores(queue.page, now));

	/* The one list above the queue: a group heading, the group's rows, and under a pass of several
	   kinds one row per kind. Keyed so a row keeps its place as the numbers tick. */
	type Line =
		| { kind: 'group'; id: string; title: string }
		| { kind: 'pass'; id: string; pass: Pass }
		| { kind: 'part'; id: string; pass: Pass; part: PassPart }
		| { kind: 'chore'; id: string; chore: ChoreView };

	/* A pass of several kinds folds its rows under an arrow and starts folded; a phone's card
	   list has no arrow track, so there the kinds are always shown. */
	let openPasses = $state<string[]>([]);
	function togglePass(id: string): void {
		openPasses = openPasses.includes(id)
			? openPasses.filter((one) => one !== id)
			: [...openPasses, id];
	}

	const lines = $derived<Line[]>([
		{ kind: 'group', id: 'group:passes', title: COPY.groups.passes },
		...queues.flatMap((pass): Line[] => [
			{ kind: 'pass', id: `pass:${pass.id}`, pass },
			...(phoneWidth.yes || openPasses.includes(pass.id) ? pass.parts : []).map((part): Line => ({
				kind: 'part',
				id: `part:${pass.id}:${part.type}`,
				pass,
				part
			}))
		]),
		{ kind: 'group', id: 'group:housekeeping', title: COPY.groups.housekeeping },
		...chored.map((chore): Line => ({ kind: 'chore', id: `chore:${chore.id}`, chore }))
	]);

	/* Run now here is the task's own Run now (`pressTasks`), one press with one sentence; a pass
	   presses what the server says it is (`runs`), less a sub-task switched off. */
	function pressesOf(one: Pass | ChoreView): { task: string; parts?: string[] }[] {
		if ('runs' in one && one.runs.length > 0)
			return one.runs.map((run) => ({ task: run.task, parts: run.parts ?? undefined }));
		return one.task ? [{ task: one.task }] : [];
	}

	async function runNow(one: Pass | ChoreView): Promise<void> {
		await pressTasks(pressesOf(one), 'now');
		void queue.refresh();
	}

	/* PAUSE, RESUME AND CANCEL, on a pass, a sub-task and (on Options) the whole queue. A pause holds
	   what starts next and lets what runs finish its step; Resume starts exactly what it held. */
	async function doPause(which: Which, paused: boolean): Promise<void> {
		try {
			await queue.pause(which, paused);
		} catch {
			toasts.show(paused ? "Couldn't pause that" : "Couldn't resume that", { tone: 'error' });
		}
	}

	let cancelingPass = $state<{ which: Which; title: string } | null>(null);
	let cancelPassOpen = $state(false);

	function askCancelPass(which: Which, title: string): void {
		cancelingPass = { which, title };
		cancelPassOpen = true;
	}

	async function doCancelPass(): Promise<void> {
		const asked = cancelingPass;
		if (!asked) return;
		try {
			const count = await queue.cancelWork(asked.which);
			toasts.show(count === 1 ? 'One task was canceled' : `${counted(count)} tasks were canceled`, {
				tone: 'success'
			});
		} catch {
			toasts.show("Those tasks couldn't be canceled", { tone: 'error' });
		}
	}

	/* The row's subject is the server's, resolved from the payload's ids, so a renamed file reads
	   by its name now; whole-library work has none and leads with what it does. */
	/* The row parked for the password, opened to its field, or null. */
	let unlocking = $state<string | null>(null);

	/* A row about its own file or folder is titled by it; any other is titled by what it does, and
	   the one file its steps are about goes on the line under it. */
	function subjectOf(job: Job): string {
		return job.subject ?? job.name;
	}

	/** The file a finished row opens: its own. */
	function fileOf(job: Job): string | null {
		return job.subject_id ?? null;
	}

	/** Why a row failed, in one line with the file it was on: its family's newest failure on a
	 *  folded row, its own reason on any other. */
	function whyOf(job: Job): string | null {
		const failure = job.steps?.failure;
		if (failure) {
			const on = failure.subject ? ` on ${failure.subject}` : '';
			const tries = failure.attempts > 1 ? ` (tried ${failure.attempts} times)` : '';
			return `${failure.name} failed${on}${tries}: ${failure.reason}`;
		}
		return job.reason ?? job.error ?? null;
	}

	/**
	 * The second line: what is being done, a running or done job's note, and on a folded row what
	 * is under it, a middle dot between. The doing is dropped when it is already the first line.
	 */
	function doingOf(job: Job, steps: string | null): string | null {
		const doing = job.subject ? job.name : (job.steps?.subject ?? null);
		const note = job.state === 'running' || job.state === 'done' ? job.note : null;
		const said = [doing, note, steps].filter((one): one is string => Boolean(one));
		return said.length > 0 ? said.join(' \u00b7 ') : null;
	}

	function ask(job: Job) {
		confirming = job;
		confirmOpen = true;
	}

	/* What a cancel calls off: a folded row's steps as the server counted them. */
	function consequenceFor(job: Job): string {
		if (job.type === 'performance_benchmark')
			return "The tasks it paused start again, and the last benchmark's result stays. You can run it again afterwards.";
		const kids = stepsLeft(job.steps);
		const work = kids === 1 ? '1 task' : `${kids.toLocaleString()} tasks`;
		return kids > 0
			? `This cancels it and the ${work} it started that haven't finished. Work already done is kept, and you can run it again afterwards.`
			: 'Work already done is kept, and you can run it again afterwards.';
	}

	async function doCancel() {
		const job = confirming;
		if (!job) return;
		try {
			await queue.cancel(job.id);
		} catch {
			// Never auto-dismissed, by the toaster's own rule: an action that did not happen is exactly
			// the message worth not missing.
			toasts.show("Couldn't cancel that task", { tone: 'error' });
		}
	}

	async function doRetry(job: Job) {
		try {
			await queue.retry(job.id);
		} catch {
			toasts.show("Couldn't run that task again", { tone: 'error' });
		}
	}

	/* Failures arrive in batches; offered only when there is something to press it for. */
	let retryingAll = $state(false);

	async function doRetryAll() {
		retryingAll = true;
		try {
			const count = await queue.retryFailed();
			toasts.show(
				count === 1 ? 'One task is running again' : `${counted(count)} tasks are running again`,
				{
					tone: 'success'
				}
			);
		} catch {
			toasts.show("Couldn't run those tasks again", { tone: 'error' });
		} finally {
			retryingAll = false;
		}
	}

	/* A failure that cannot succeed would otherwise stay until retried and failed twice. */
	let clearingAll = $state(false);

	/* Stopping the whole queue: for work nobody wants, not work gone wrong. Behind a confirmation,
	   as the machine time already spent is not given back. */
	let cancelingAll = $state(false);
	let cancelAllOpen = $state(false);

	/* Starting them again, rather than a folder scan that re-walks the library. Confirmed, as it can
	   be hours of the machine. */
	let startingCanceled = $state(false);
	let startCanceledOpen = $state(false);

	const outstanding = $derived(
		(counts.queued ?? 0) + (counts.running ?? 0) + (counts.blocked ?? 0)
	);
	const canceled = $derived(counts.canceled ?? 0);
	const listedOutstanding = $derived(
		(shownCounts.queued ?? 0) + (shownCounts.running ?? 0) + (shownCounts.blocked ?? 0)
	);
	// `failures` rather than `failed`, which is what a queue's own tally of them is called a few
	// lines up. Two things called the same word in one file is how the wrong one gets read.
	const failures = $derived(counts.failed ?? 0);

	/* A bulk action is offered for the pile being looked at, and its label names that pile and its
	   number, so "them" always points at rows on screen. */
	function offeredFor(...states: JobState[]): boolean {
		return queue.kind === null && offeredWhileViewing(queue.filter, ...states);
	}

	/* Which piles have an answer on the Options menu now. Named once, because the menu's groups
	   are drawn only where they hold a row: a group with none would put a line under nothing. */
	const failedOffered = $derived(offeredFor('failed') && failures > 0);
	const outstandingOffered = $derived(
		offeredFor('queued', 'running', 'blocked') && outstanding > 0
	);
	const canceledOffered = $derived(offeredFor('canceled') && canceled > 0);

	/* The stopped rows thrown away: the opposite answer to starting them again. */
	let clearingCanceled = $state(false);
	let clearCanceledOpen = $state(false);

	async function doClearCanceled() {
		clearingCanceled = true;
		try {
			const count = await queue.clearCanceled();
			toasts.show(
				count === 1
					? 'One canceled task was cleared'
					: `${count.toLocaleString()} canceled tasks were cleared`,
				{ tone: 'success' }
			);
		} catch {
			toasts.show("Those tasks couldn't be cleared", { tone: 'error' });
		} finally {
			clearingCanceled = false;
		}
	}

	async function doStartCanceled() {
		startingCanceled = true;
		try {
			const count = await queue.retryCanceled();
			toasts.show(
				count === 1
					? 'One canceled task resumed'
					: `${count.toLocaleString()} canceled tasks resumed`,
				{ tone: 'success' }
			);
		} catch {
			toasts.show("Those tasks couldn't be resumed", { tone: 'error' });
		} finally {
			startingCanceled = false;
		}
	}

	async function doCancelAll() {
		cancelingAll = true;
		try {
			const count = await queue.cancelAll();
			toasts.show(count === 1 ? 'One task was canceled' : `${counted(count)} tasks were canceled`, {
				tone: 'success'
			});
		} catch {
			toasts.show("Those tasks couldn't be canceled", { tone: 'error' });
		} finally {
			cancelingAll = false;
		}
	}

	async function doClearAll() {
		clearingAll = true;
		try {
			const count = await queue.clearFailed();
			toasts.show(
				count === 1 ? 'One failed task was cleared' : `${count} failed tasks were cleared`,
				{
					tone: 'success'
				}
			);
		} catch {
			toasts.show("Those tasks couldn't be cleared", { tone: 'error' });
		} finally {
			clearingAll = false;
		}
	}
</script>

<!-- A pass's or a sub-task's pause (or resume) and cancel: glyph presses, named on the hover. -->
{#snippet holdAndCancel(which: Which, title: string, paused: boolean)}
	<Tooltip label={paused ? COPY.resume : COPY.pause}>
		<Button
			tone="ghost"
			onclick={() => void doPause(which, !paused)}
			aria-label="{paused ? COPY.resume : COPY.pause}: {title}"
		>
			<Icon name={paused ? 'play_arrow' : 'pause'} size={16} />
		</Button>
	</Tooltip>
	<Tooltip label={COPY.cancel}>
		<Button
			tone="ghost"
			onclick={() => askCancelPass(which, title)}
			aria-label="{COPY.cancel}: {title}"
		>
			<Icon name="close" size={16} />
		</Button>
	</Tooltip>
{/snippet}

<!-- One row of the queue: a family's top row folded (its steps counted, one badge, failed if
     anything failed), a step under an opened family (`step`), or a step on a state's tab. -->
{#snippet jobRow(job: Job, step: boolean)}
	{@const state = shownState(job)}
	{@const line = step ? null : stepsLine(job.steps)}
	{@const folds = queue.folded && !step && (job.steps?.count ?? 0) > 0}
	<DataRow
		{compact}
		indent={step ? 1 : 0}
		cells={phoneWidth.yes
			? { card: jobCard }
			: { name: named, status: badge, left: startsAt, bar: moving, count: age }}
		expanded={folds ? queue.isOpen(job.id) : undefined}
		ontoggle={folds ? () => void queue.toggle(job.id) : undefined}
		toggleLabel="the steps of {subjectOf(job)}"
	>
		{#snippet actions()}
			<!-- Run again reads the row's own state; Cancel what the row shows, since a cancel of a
			     top calls off every step still to finish. -->
			{#if canRetry(job.state)}
				<Tooltip label="Try again">
					<Button
						tone="ghost"
						onclick={() => doRetry(job)}
						aria-label="Try again, {subjectOf(job)}"
					>
						<Icon name="sync" size={16} />
					</Button>
				</Tooltip>
			{/if}
			{#if canCancel(state)}
				<Tooltip label="Cancel">
					<Button tone="ghost" onclick={() => ask(job)} aria-label="Cancel: {subjectOf(job)}">
						<Icon name="close" size={16} />
					</Button>
				</Tooltip>
			{/if}
		{/snippet}
		{#snippet expansion()}
			{#if folds && queue.isOpen(job.id)}
				{@const opened = queue.stepsOf(job.id)}
				{#if opened === null}
					<div class="steps-note"><Skeleton lines={1} /></div>
				{:else}
					<DataRows
						items={opened.jobs}
						key={(one: Job) => one.id}
						label="Steps of {subjectOf(job)}"
					>
						{#snippet row(one: Job)}
							{@render jobRow(one, true)}
						{/snippet}
					</DataRows>
					{#if opened.jobs.length < opened.total}
						<div class="steps-note">
							<Button tone="ghost" size="small" onclick={() => void queue.moreSteps(job.id)}>
								{COPY.moreSteps}
							</Button>
						</div>
					{/if}
				{/if}
			{/if}
		{/snippet}
	</DataRow>
	{#snippet named()}
		<span class="subject" class:compact>
			<!-- A download's row points at the Downloads screen (`?job=`), which knows its Site. -->
			{#if step}
				<span class="what">{job.name}</span>
			{:else if job.type === 'download'}
				<a class="what opens" href="/downloads?job={job.id}">{subjectOf(job)}</a>
			{:else if state === 'done' && fileOf(job)}
				<!-- A finished row's result is what somebody wants to look at, and the name is what
				     they reach for. Bound out of the block so the handler closes over a string. -->
				{@const opened = fileOf(job) ?? ''}
				<a
					class="what opens"
					href="/asset/{opened}"
					onclick={(event) => openAssetInstead(event, opened)}>{subjectOf(job)}</a
				>
			{:else}
				<span class="what">{subjectOf(job)}</span>
			{/if}
			{#if !step && doingOf(job, line)}
				<span class="doing">{doingOf(job, line)}</span>
			{/if}
			{#if whyOf(job)}
				<!-- Already redacted on the way into the queue. This is the screen a screenshot in a
				     bug report is most likely to be of, and a raw path would ride out on it. -->
				<!-- The tool's own words whole on the hover; the line itself wraps to at most three. -->
				<Tooltip label={job.error ?? whyOf(job) ?? ''}>
					<span class="why">{whyOf(job)}</span>
				</Tooltip>
			{/if}
			{#if job.waits_for_password && session.secretsLocked}
				{#if unlocking === job.id}
					<span class="unlock-here"><UnlockField layout="inline" focus /></span>
				{:else}
					<span class="unlock-door">
						<Button tone="link" size="small" onclick={() => (unlocking = job.id)}>Unlock</Button>
					</span>
				{/if}
			{/if}
		</span>
	{/snippet}
	{#snippet badge()}<Badge state={state as BadgeState} />{/snippet}
	<!-- EACH COLUMN HOLDS WHAT ITS HEAD SAYS. Time left: a job waiting for a TIME says when it
	     starts, the one time-left a row knows; without it a backup scheduled overnight reads as
	     waiting beside work that really is stuck. -->
	{#snippet startsAt()}
		{#if startsIn(job.run_after, now)}
			<!-- The moment it starts on the hover: a live list says how far away, and the whole
			     moment is one gesture off (`$lib/shell/when`). -->
			<Tooltip label={exactly(job.run_after ?? 0)}>
				<span class="scheduled">{startsIn(job.run_after, now)}</span>
			</Tooltip>
		{/if}
	{/snippet}
	<!-- Done: when a finished row finished, and on a row tried more than once its tries. -->
	{#snippet age()}
		<span class="finished">
			{#if state === 'done' || state === 'failed' || state === 'canceled'}
				<Tooltip label={exactly(job.updated_at)}>
					<span class="when">{sayAgo(job.updated_at, now)}</span>
				</Tooltip>
			{/if}
			{@render tries()}
		</span>
	{/snippet}
	{#snippet moving()}
		{#if job.state === 'running'}
			<ProgressBar value={job.progress * 100} label={subjectOf(job)} />
		{/if}
	{/snippet}
	{#snippet tries()}
		{#if job.attempts > 1}
			<span class="attempts">try {job.attempts}/{job.max_attempts}</span>
		{/if}
	{/snippet}
	<!-- The job as a card on a phone: its name, then its state, when and its tries on one line, and
	     its bar under them while it runs. The same cells the columns hold, one under another. -->
	{#snippet jobCard()}
		<span class="card">
			{@render named()}
			<span class="card-facts">{@render badge()}{@render startsAt()}{@render age()}</span>
			{@render moving()}
		</span>
	{/snippet}
{/snippet}

<!-- The three tabs. Above everything, because they decide what everything under them is. -->
<div class="activity-tabs">
	<Tabs
		tabs={TABS}
		current={tab}
		onselect={(id: string) => choose(id as ActivityTab)}
		controls="activity-tab"
		label={COPY.tabsLabel}
	/>
</div>

<div id="activity-tab" role="tabpanel" aria-label={TABS.find((one) => one.id === tab)?.label}>
	{#if tab === 'tasks'}
		<ScheduledTasks {activity} />
	{:else if tab === 'history'}
		{#key `${historyVerb}|${historyDecisions}`}
			<Ledger verb={historyVerb} decisions={historyDecisions} />
		{/key}
	{:else if tab === 'log'}
		<Logs />
	{/if}
</div>

<!-- Activity: what Sift is doing, under the tasks on the Tasks tab (`ScheduledTasks`' `activity`). -->
{#snippet activity()}
	<Problem message={queue.problem} />

	<!-- One list for passes and housekeeping on the tab's columns (`ACTIVITY_COLUMNS`), so every
	     status stands at one x; every row is listed, work or not, so none jumps as queues fill. -->
	<!-- On a phone the same list as cards (`ACTIVITY_CARD`): a list reads its columns once, when
	     it is made, so it is made again when the window crosses the phone's width. -->
	{#key phoneWidth.yes}
		<DataRows
			items={lines}
			key={(line: Line) => line.id}
			label={COPY.summaryLabel}
			columns={phoneWidth.yes ? ACTIVITY_CARD : ACTIVITY_COLUMNS}
			actions={phoneWidth.yes ? undefined : ACTIVITY_ACTIONS}
			folds={!phoneWidth.yes}
			edges
		>
			{#snippet row(line: Line)}
				{#if line.kind === 'group'}
					<DataRow><SectionHeading>{line.title}</SectionHeading></DataRow>
				{:else if line.kind === 'part'}
					<!-- One kind of a pass that is several: under the pass, its own bar and count. The
				     summed figure would count the library once per kind and describe none of them. -->
					<DataRow
						indent={1}
						cells={phoneWidth.yes
							? { card: partCard }
							: { name: partName, bar: partBar, count: partCount }}
						actions={phoneWidth.yes ? undefined : partActions}
						{compact}
					/>
					{#snippet partCard()}
						<span class="card">
							<span class="card-head">{@render partName()}{@render partActions()}</span>
							<span class="card-facts">{@render partCount()}</span>
							{@render partBar()}
						</span>
					{/snippet}
					<!-- A sub-task's own pause and cancel, as its pass has them. -->
					{#snippet partActions()}
						<span class="row-actions">
							{@render holdAndCancel({ type: line.part.type }, line.part.label, line.part.paused)}
						</span>
					{/snippet}
					{#snippet partName()}<span class="pass-title">{line.part.label}</span>{/snippet}
					{#snippet partBar()}<ProgressBar
							value={line.part.progress}
							label={`${line.part.label} \u2014 ${line.part.count}`}
						/>{/snippet}
					{#snippet partCount()}<span class="pass-count">{line.part.count}</span>{/snippet}
				{:else}
					{@const one = line.kind === 'pass' ? line.pass : line.chore}
					<DataRow
						cells={phoneWidth.yes
							? { card: lineCard }
							: line.kind === 'pass'
								? {
										name: title,
										status: nowWord,
										left: timeLeft,
										bar: passBar,
										count: passCount
									}
								: { name: title, status: nowWord, left: timeLeft, bar: lastRun }}
						spans={line.kind === 'chore' && !phoneWidth.yes ? { bar: 'count' } : undefined}
						actions={phoneWidth.yes ? undefined : lineActions}
						expanded={line.kind === 'pass' && line.pass.parts.length > 0 && !phoneWidth.yes
							? openPasses.includes(line.pass.id)
							: undefined}
						ontoggle={line.kind === 'pass' && line.pass.parts.length > 0 && !phoneWidth.yes
							? () => togglePass(line.pass.id)
							: undefined}
						toggleLabel="the kinds of {one.title}"
						{compact}
					/>
					<!-- Run now, the task's own press; on a pass its pause and cancel after it. A chore that
				     is not a task (a download, a transcode) has no Run now. On a phone they end the
				     card's first line instead (`lineCard`), and the list has no actions track. -->
					{#snippet lineActions()}
						<span class="row-actions">
							{#if pressesOf(one).length > 0}
								<Button tone="link" size="small" onclick={() => void runNow(one)}
									>{COPY.runNow}</Button
								>
							{/if}
							{#if line.kind === 'pass'}
								{@render holdAndCancel(
									{ family: line.pass.id as Which['family'] },
									one.title,
									line.pass.paused
								)}
							{/if}
						</span>
					{/snippet}
					<!-- The row as a card: the same cells, one under another, in the order they read
				     across. The name with the row's one action at the card's end; the state, when
				     and the count on one line, each its own words; the bar the card's full width,
				     so every bar on the list is one length and reads against the next. -->
					{#snippet lineCard()}
						<span class="card">
							<span class="card-head">{@render title()}{@render lineActions()}</span>
							<span class="card-facts"
								>{@render nowWord()}{@render timeLeft()}{#if line.kind === 'pass'}{@render passCount()}{:else}{@render lastRun()}{/if}</span
							>
							{#if line.kind === 'pass'}{@render passBar()}{/if}
						</span>
					{/snippet}
					{#snippet title()}<span class="pass-title">{one.title}</span>{/snippet}
					<!-- The state as a pill, the shape Done and Failed wear on the rows under it: up to
				     date is done's, waiting on something is blocked's, work under way is the In
				     progress chip the job rows under it wear (blue, its mark turning), anything else
				     is quiet. -->
					<!-- On the app's tooltip as well, as a column can cut the pill's words short; a failed
				     pass's hover says why, and pressed it opens the failed ones below. -->
					{#snippet nowWord()}
						{#if line.kind === 'pass' && line.pass.why}
							<Tooltip label={COPY.failedWhy(line.pass.why)} stretch>
								<Button tone="quiet" onclick={showFailed}
									><Badge state={nowState(one.tone)} label={one.now} /></Button
								>
							</Tooltip>
						{:else if line.kind === 'pass' && line.pass.leftOut > 0}
							<TasksLeftOut
								products={leftOut.named(line.pass.leftOutOf)}
								title={one.title}
								tone="quiet"><Badge state={nowState(one.tone)} label={one.now} /></TasksLeftOut
							>
						{:else}
							<Tooltip label={one.now} stretch
								><Badge state={nowState(one.tone)} label={one.now} /></Tooltip
							>
						{/if}
					{/snippet}
					<!-- An estimate, or the reason there is not going to be one: "it cannot start" is an
				     answer to "when will it be done". A switched-off pass links to where it is
				     switched on. -->
					{#snippet timeLeft()}
						{#if line.kind === 'pass' && line.pass.settingLink}
							<span class="pass-eta"><SettingLink section="importing">{one.when}</SettingLink></span
							>
						{:else}
							<span class="pass-eta">{one.when}</span>
						{/if}
					{/snippet}
					<!-- DONE OVER WHAT WANTS DOING, both counted from the library, so the bar is defined
				     while nothing runs. A pass of several kinds draws its bars on the rows under it. -->
					{#snippet passBar()}
						{#if line.kind === 'pass' && (line.pass.parts.length === 0 || line.pass.moving)}
							<ProgressBar
								value={line.pass.progress}
								label={`${one.title} \u2014 ${line.pass.done}`}
							/>
						{/if}
					{/snippet}
					{#snippet passCount()}
						{#if line.kind === 'pass' && (line.pass.parts.length === 0 || line.pass.moving)}
							<span class="pass-count">{line.pass.done}</span>
						{/if}
					{/snippet}
					<!-- No bar for a chore: nothing counts the files that want a duplicate sweep, so the
				     bar and count columns say how the last run went instead. -->
					<!-- A run that failed says why on the hover, and the phrase opens the failed ones in
				     the list below, where its row carries the whole of what went wrong. -->
					{#snippet lastRun()}
						{#if line.kind === 'chore' && line.chore.why}
							<span class="pass-last">
								<Tooltip label={COPY.failedWhy(line.chore.why)}>
									<Button tone="link" size="small" class="failed-run" onclick={showFailed}
										>{line.chore.last}</Button
									>
								</Tooltip>
							</span>
						{:else if line.kind === 'chore'}<span class="pass-last">{line.chore.last}</span>{/if}
					{/snippet}
				{/if}
			{/snippet}
		</DataRows>
	{/key}

	<!-- Which pile is looked at: the strip every entity page draws, in its value mode (a link would
	     tear down the page under the Settings sheet), in the badge's own words. -->
	<!-- THE LIST'S OWN HEADING. Its narrowing by kind is on the strip's line, after Options: which
	     STATE is the strip, which KIND the chooser beside it, one line of what is shown. -->
	<div class="list-head">
		<SectionHeading>{COPY.list.heading}</SectionHeading>
	</div>

	<div class="filters">
		<Tabs
			tabs={[
				// No number until the queue has answered: a nought that turns into a twelve a moment later
				// reports a fault that is not there.
				{ id: ALL, label: 'All', count: queue.page ? shownTotal : undefined },
				...present.map((state) => ({
					id: state,
					label: LABELS[state as BadgeState],
					count: queue.page ? (shownCounts[state] ?? 0) : undefined,
					// The pile that is waiting for a person rather than for a worker wears the waiting
					// mark: blocked work sits there until somebody unlocks something, and nothing else on
					// this screen says so.
					attention: state === 'blocked' ? BLOCKED_WAITS : undefined
				}))
			]}
			current={queue.filter ?? ALL}
			onselect={(id: string) => queue.setFilter(id === ALL ? null : (id as JobState))}
			controls={listId}
			label="Which tasks to show"
		/>
		<!-- The pile's actions at the end of the strip that chooses the pile, the way every
		     row's actions stand at its end, rather than alone above the list. -->
		<!-- Every bulk action behind one door, each naming its pile and number; always drawn, as it
		     always holds at least the density row. -->
		<MenuButton label="What can be done with these tasks" words="Options">
			<!-- Four parts: the whole queue's pause, what to do with a pile, how the rows are drawn,
			     and the two that throw rows away, last and below their own line. -->
			<ContextMenuGroup>
				<ContextMenuItem
					label={queue.paused ? COPY.resumeAll : COPY.pauseAll}
					icon={queue.paused ? 'play_arrow' : 'pause'}
					onselect={() => void doPause({}, !queue.paused)}
				/>
			</ContextMenuGroup>
			{#if failedOffered || outstandingOffered || canceledOffered}
				<ContextMenuGroup>
					{#if outstandingOffered}
						<ContextMenuItem
							label="Cancel the {pile(outstanding, listedOutstanding, 'queued')}"
							icon="close"
							disabled={cancelingAll}
							onselect={() => (cancelAllOpen = true)}
						/>
					{/if}
					{#if canceledOffered}
						<ContextMenuItem
							label="Resume the {pile(canceled, shownCounts.canceled, 'canceled')}"
							icon="play_arrow"
							disabled={startingCanceled}
							onselect={() => (startCanceledOpen = true)}
						/>
					{/if}
					{#if failedOffered}
						<ContextMenuItem
							label="Try again ({pile(failures, shownCounts.failed, 'failed')})"
							icon="sync"
							disabled={retryingAll}
							onselect={doRetryAll}
						/>
					{/if}
				</ContextMenuGroup>
			{/if}
			<!-- The row density: how this screen is drawn, so behind the same door, as a checkable row. -->
			<ContextMenuGroup>
				<ContextMenuItem
					label="Compact rows"
					checked={compact}
					onselect={() => (compact = !compact)}
				/>
			</ContextMenuGroup>
			{#if canceledOffered || failedOffered}
				<ContextMenuGroup>
					{#if canceledOffered}
						<ContextMenuItem
							label="Clear the {pile(canceled, shownCounts.canceled, 'canceled')}"
							icon="delete"
							destructive
							disabled={clearingCanceled}
							onselect={() => (clearCanceledOpen = true)}
						/>
					{/if}
					{#if failedOffered}
						<ContextMenuItem
							label="Clear the {pile(failures, shownCounts.failed, 'failed')}"
							icon="delete"
							destructive
							disabled={clearingAll}
							onselect={doClearAll}
						/>
					{/if}
				</ContextMenuGroup>
			{/if}
		</MenuButton>
		<Tooltip label={COPY.list.type.help}>
			<span>
				<Select
					label={COPY.list.type.label}
					value={queue.kind ?? EVERY_KIND}
					options={kindChoicesShown}
					onValueChange={(next) => void queue.setKind(next === EVERY_KIND ? null : next)}
				/>
			</span>
		</Tooltip>
	</div>
	{#if queue.paused}
		<div class="stepping-back">
			<Note>{COPY.pausedAll}</Note>
		</div>
	{/if}
	<!-- Under the running count, in eco mode or turbo mode: the sidebar leaf's words. -->
	{#if eco !== null}
		<div class="stepping-back"><Note>{COPY.steppingBack(eco)}</Note></div>
	{/if}

	<!-- The one list the strip filters, whichever state it is in. On the same columns as the list
	     above, so every status on the tab stands at one x. -->
	<div id={listId} role="tabpanel" aria-label="Tasks">
		<!-- Nothing read is not nothing there: a skeleton until a read lands, and the Problem above
		     speaks for a read that failed (`Queue.refresh` keeps a good page). -->
		{#if queue.listed === null}
			{#if !queue.problem}<Skeleton lines={3} />{/if}
		{:else if jobs.length === 0}
			<Empty scope="block">
				{#if queue.kind !== null}
					{queue.filter === null ? COPY.list.noneOfType : COPY.list.noneOfTypeInState}
				{:else}
					{queue.filter === null ? 'No tasks are running or waiting.' : 'No tasks in this state.'}
				{/if}
			</Empty>
		{:else}
			{#key phoneWidth.yes}
				<DataRows
					items={jobs}
					key={(job: Job) => job.id}
					label="Activity"
					columns={phoneWidth.yes ? ACTIVITY_CARD : ACTIVITY_COLUMNS}
					actions={phoneWidth.yes ? ACTIVITY_CARD_ACTIONS : ACTIVITY_ACTIONS}
					folds
					edges
				>
					{#snippet row(job: Job)}
						{@render jobRow(job, false)}
					{/snippet}
				</DataRows>
			{/key}
			<!-- A page of the list at a time, by number, and a page typed in: the queue of a big
			     import is tens of thousands of rows, and every page of it has to be reachable. -->
			{@const listed = queue.listed?.total ?? 0}
			{#if listed > QUEUE_PAGE}
				<Pager
					offset={queue.offset}
					shown={jobs.length}
					total={listed}
					noun="tasks"
					perPage={QUEUE_PAGE}
					onfirst={() => void queue.goTo(0)}
					onprevious={() => void queue.goTo(queue.offset - QUEUE_PAGE)}
					onnext={() => void queue.goTo(queue.offset + QUEUE_PAGE)}
					onlast={() => void queue.goTo(Math.floor((listed - 1) / QUEUE_PAGE) * QUEUE_PAGE)}
					onjump={(position) =>
						void queue.goTo(Math.floor((position - 1) / QUEUE_PAGE) * QUEUE_PAGE)}
				/>
			{/if}
		{/if}
	</div>

	<ConfirmDialog
		bind:open={confirmOpen}
		title={confirming ? `Cancel ${subjectOf(confirming)}?` : 'Cancel this task?'}
		consequence={confirming ? consequenceFor(confirming) : ''}
		confirmLabel="Cancel task"
		cancelLabel="Keep running"
		destructive={false}
		onconfirm={doCancel}
	/>

	<ConfirmDialog
		bind:open={clearCanceledOpen}
		title="Clear {canceled.toLocaleString()} canceled tasks?"
		consequence="The rows are deleted and can't be restored &mdash; to run the work later, resume them instead. Work already done is kept, and no file in your library is touched."
		confirmLabel="Clear tasks"
		destructive={true}
		onconfirm={doClearCanceled}
	/>

	<ConfirmDialog
		bind:open={startCanceledOpen}
		title="Resume {canceled.toLocaleString()} canceled tasks?"
		consequence="They continue from where they were canceled, so no file is scanned twice. On a large library this can take hours, and you can cancel it again."
		confirmLabel="Resume tasks"
		destructive={false}
		onconfirm={doStartCanceled}
	/>

	<ConfirmDialog
		bind:open={cancelPassOpen}
		title={cancelingPass ? COPY.cancelPass(cancelingPass.title) : ''}
		consequence={COPY.cancelPassSays}
		confirmLabel="Cancel tasks"
		cancelLabel="Keep running"
		destructive={false}
		onconfirm={doCancelPass}
	/>

	<ConfirmDialog
		bind:open={cancelAllOpen}
		title="Cancel all {outstanding.toLocaleString()} tasks?"
		consequence="Running tasks are canceled too, so a running scan can't add the tasks back. Nothing is deleted, and a later scan picks up any file that was imported but not yet processed."
		confirmLabel="Cancel all tasks"
		cancelLabel="Keep running"
		destructive={false}
		onconfirm={doCancelAll}
	/>
{/snippet}

<style>
	/* The four tabs sit apart from the Activity tab's own strip under them, which narrows one list. */
	.activity-tabs {
		margin-block-end: var(--space-5);
	}

	/* The strip at the start and the pile's actions at the end, a group's space clear of the list. */
	.filters {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: var(--space-4);
		margin-block: var(--space-4);
	}

	/* The strip at the start; Options and the Type chooser after it at the end. */
	.filters > :global(:first-child) {
		margin-inline-end: auto;
	}

	/* The sentence under the strip stands a step clear of the column heads under it. */
	.stepping-back {
		margin-block-end: var(--space-4);
	}

	/* The list's heading: a group's space clear of the one before it. */
	.list-head {
		margin-block-start: var(--space-6);
	}

	/* A row as a card on a phone: the cells one under another; the facts line wraps, not cuts. */
	.card {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		min-inline-size: 0;
		padding-block: var(--space-1);
	}

	.card-facts {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		column-gap: var(--space-3);
		row-gap: var(--space-1);
	}

	/* The name at the start and the row's one action at the card's end, on one line. */
	.card-head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: var(--space-3);
	}

	/* Words on a card start where the name starts: the Done column's end edge is not there. */
	.card .pass-last {
		text-align: start;
	}

	.card .finished {
		justify-content: flex-start;
	}

	.subject {
		display: flex;
		flex-direction: column;
		gap: 2px;
		min-width: 0;
	}

	/* Compact lays the subject along the row, so a two-line row reaches the compact floor too. */
	.subject.compact {
		flex-direction: row;
		align-items: baseline;
		gap: var(--space-2);
	}

	/* Sharing a line, each part gives way, or the longest pushes the rest off the row's end. */
	.subject.compact .what,
	.subject.compact .doing,
	.subject.compact .why {
		display: block;
		flex: 0 1 auto;
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	/* The name, what is being done and why WRAP rather than cut a word; compact lays them along. */
	.what {
		color: var(--sift-ink);
		font: var(--text-body);
		overflow-wrap: anywhere;
	}

	/* The job's name under its file's, one step down and muted: the file, then what happens to it. */
	.opens {
		color: inherit;
		text-decoration: none;
	}

	.opens:hover,
	.opens:focus-visible {
		text-decoration: underline;
	}

	.doing {
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* The note under an opened family, across the row's tracks rather than in the first. */
	.steps-note {
		grid-column: 1 / -1;
		padding-inline-start: var(--space-8);
		padding-block: var(--space-1);
	}

	.pass-title {
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
	}

	/* How the last run went spans Progress and Done, ending on Done's edge as the counts do. */
	.pass-last {
		display: block;
		text-align: end;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* A failed run's phrase in the failure's ink, dotted under: there is more behind it. */
	.pass-last :global(.failed-run) {
		font: inherit;
		color: var(--sift-bad-text);
		text-decoration: underline dotted;
		text-underline-offset: 0.15em;
	}

	/* Words, so left: right-aligned sentences of different lengths have a ragged left edge. */
	.pass-eta {
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* The count column is declared `end`; this is only its colour and face. */
	.pass-count {
		color: var(--sift-ink-3);
		font: var(--text-data);
	}

	/* The field a row parked for the password opens, under the sentence that asks for it. */
	.unlock-here,
	.unlock-door {
		display: block;
		margin-block-start: var(--space-1);
	}

	.why {
		display: -webkit-box;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 3;
		line-clamp: 3;
		overflow: hidden;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
		overflow-wrap: anywhere;
	}

	/* A finished row's time and its tries, under Done, ending on the column's edge as its head does. */
	.finished {
		display: flex;
		justify-content: flex-end;
		flex-wrap: wrap;
		column-gap: var(--space-2);
	}

	/* A row's presses together at its end: Run now, then the glyphs. */
	.row-actions {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
	}

	.attempts,
	.when {
		color: var(--sift-ink-3);
		font: var(--text-data);
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}

	/* Same shape as the age it replaces, so a row does not change width when a job is due. */
	.scheduled {
		color: var(--sift-ink-3);
		font: var(--text-data);
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}
</style>
