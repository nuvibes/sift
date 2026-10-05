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
		LabelledRow,
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
	import { QUEUE_PAGE, Queue, type JobState } from './queue.svelte';
	import { kindChoices } from './kinds';
	import Pager from '$lib/components/common/Pager.svelte';

	/* Settings > Tasks and Activity: when each task runs, what Sift is doing and the only place to
	 * stop it doing it, and, beside them, what happened and what the program wrote down.
	 *
	 * Admin-only, and this screen is not what makes that true: every endpoint behind it refuses
	 * a guest on its own, including the socket. Rendering it to somebody who should not see it
	 * would show them an empty table and an error, not a library.
	 *
	 * ## FOUR TABS: four answers to "what does Sift do, and when", from four stores, behind one
	 * door; Tasks first, the one a person sets. Which tab an address opens is `tabs.ts`.
	 *
	 * The strip is `Tabs` filtering in place, as the state filter under it is: this pane is
	 * inside the Settings sheet over a page, and a link would tear that page down. The library's
	 * `Tabs` keeps every pane mounted to measure the tallest, which here would read all four.
	 */
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
		{ id: 'now', label: COPY.tabs.now },
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
		if (next === 'now') void queue.refresh(); // read only while its tab shows
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
	whenChanged(jobChanges, () => void (tab === 'now' && queue.refresh()));

	/* HOW OFTEN THE NUMBERS ARE RE-ASKED, and why there is a timer.
	 *
	 * The live connection says when the queue has moved, which is exactly right for the table of
	 * rows and wrong for everything above it. The counts are read from the LIBRARY and the
	 * estimate from a sample of finished work, and neither of those moves the queue: a pass
	 * working steadily announces nothing between jobs, so without a timer a wrong number would
	 * sit there for as long as the tab was open: days, on an install left running.
	 *
	 * So: a slow beat while anything is outstanding, nothing at all while the queue is empty
	 * (an idle library costs nothing, which is the property the socket was chosen for), and one
	 * read when the window is looked at again, because a screen left open in a background tab is
	 * the case where the numbers are furthest out of date and the cheapest moment to fix it.
	 */
	const ASK_EVERY_MS = 5000;

	onMount(() => {
		if (tab === 'now') void queue.refresh();
		const clock = setInterval(() => (now = Math.floor(Date.now() / 1000)), 1000);
		// Only while Activity is showing: the other tabs draw nothing the queue decides.
		const beat = setInterval(() => {
			if (tab === 'now' && outstanding > 0) void queue.refresh();
		}, ASK_EVERY_MS);
		const again = () => {
			if (tab === 'now' && document.visibilityState === 'visible') void queue.refresh();
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

	/* The rows of the queue as the server sent them: families, newest first, under every kind (a
	   state's tab lists the families whose row shows it); a kind's tasks, flat, while one is
	   chosen. */
	const jobs = $derived(queue.listed?.jobs ?? []);
	const counts = $derived(queue.page?.counts ?? {});

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

	/* THE STRIP'S NUMBERS ARE THE SERVER'S (`tallies`), read and never worked out here, in the one
	   universe the list draws: families under every kind, each counted once under the state its
	   row shows, so All is the states added together; one kind's tasks while a kind is chosen.
	   All reads `all` rather than the page's total, which is the chosen state's number on a
	   state's tab. The bulk actions below read `counts`, the rows they act on, and are not offered
	   while a kind is chosen: they act on every kind, and a label counting one kind would name a
	   pile they do not clear. */
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

	/*
	 * WHICH LONG PASSES CANNOT RUN AT ALL, AND WHICH ARE SWITCHED OFF, both from the server.
	 *
	 * Not worked out here. A named map from a family to the feature that gates it, with those
	 * features asked directly on mount, would be wrong three ways besides the extra requests: the
	 * map is a fact about which handler the server registered under which family, so a family added
	 * there would read "no estimate" on a screen that had no idea it existed; a feature that is
	 * simply switched off is not a missing runtime and would be reported as one; and a pass
	 * somebody had turned off could not be described at all.
	 *
	 * The server declares both, per family, at the composition root beside the counters that
	 * already say how much work each family has: one answer, wired where both halves are in
	 * scope. See `Switchboard` on the server and `passes` for how the four fields are read.
	 */

	/* The long passes, summarised at the top.
	 *
	 * A library being scanned is thousands of rows, and reading them one at a time answers "what
	 * is Sift doing" far worse than a bar per kind of work does. The rows underneath are still
	 * where a single job is found and stopped; this is the answer to the question people actually
	 * open this page with. Counted from the page the socket pushes, so it is the same numbers the
	 * table is drawn from rather than a second query that could disagree with it.
	 *
	 * A row per family rather than per job type, because a family is what somebody is waiting
	 * for: "scanning" is a walk, a read of each file and the import that took it in, and nobody
	 * watching an import wants those as three lines. Which types belong to which family is
	 * declared where each handler is registered and sent with the page.
	 *
	 * The arithmetic and the wording are in `passes`, not here: inside the markup neither could
	 * be put in front of a test.
	 */
	const queues = $derived(passes(queue.page));
	const chored = $derived(chores(queue.page, now));

	/* The one list above the queue: a group heading, the group's rows, and under a pass of several
	   kinds one row per kind. Keyed so a row keeps its place as the numbers tick. */
	type Line =
		| { kind: 'group'; id: string; title: string }
		| { kind: 'pass'; id: string; pass: Pass }
		| { kind: 'part'; id: string; pass: Pass; part: PassPart }
		| { kind: 'chore'; id: string; chore: ChoreView };

	/* The passes whose kinds are shown under them. A pass of several kinds folds its rows under an
	   arrow, as a family folds its steps in the queue below, and starts folded: the pass's own row
	   already says how the whole of it stands. A phone's card list has no arrow track, so there the
	   kinds are always shown. */
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

	/* STARTING A PASS OR A CHORE IS NOT DONE FROM HERE.
	 *
	 * Every piece of work has ONE row, on Tasks, where when it runs is chosen and Run now lives,
	 * so a bar here links to that row rather than being another door. A Run now per bar beside
	 * Importing's, the Organize cards' and a folder menu's is how work comes to be startable from
	 * many places, some of them called "Scan now" and doing different things. The row is named by
	 * the task the server says the pass or chore IS (`task` on the wire).
	 *
	 * EVERY PASS HAS THE LINK, because every pass is the work of at least one task: Identify is
	 * Faces and Watermarks, Fingerprint is Generate's stash-box fingerprints and Music's: more
	 * than one row, so the wire names none and the link opens Tasks at the top, which is still
	 * the one right place. A chore links only when it IS a task: a download or a transcode is
	 * started by a link or a play press, and has no row on Tasks to send anybody to.
	 */
	function taskRow(one: Pass | ChoreView): string | undefined {
		return one.task ? `tasks.${one.task}.when` : undefined;
	}

	/* The row's subject: which file the work is about.
	 *
	 * It comes from the server, resolved from the job's payload, because the payload holds an
	 * identifier and the name a file had when the job was queued is not necessarily the name it
	 * has now. Whole-library work (a sweep, a reindex, a backup) is about no one file and
	 * says so by having none, in which case the row leads with what is being done instead.
	 *
	 * Without this the page would be twenty identical rows whenever twenty files were being read:
	 * the only identifying text on a row would be the job's type.
	 */
	/* THE ROW PARKED FOR THE PASSWORD, opened to its field: the task's id, or null. A wait for the
	   password says so in its own sentence and offers the field where the row is (`UnlockField`,
	   the one the bar across the top and `Settings > Connections` offer); every other wait keeps
	   its own words and its own door. Offered only while the keys are locked: once unlocked, the
	   work is already back in the line. */
	let unlocking = $state<string | null>(null);

	function subjectOf(job: Job): string {
		return job.subject ?? job.steps?.subject ?? job.name;
	}

	/** The file a finished row opens: its own, or on a folded row the one file its steps name. */
	function fileOf(job: Job): string | null {
		return job.subject_id ?? job.steps?.subject_id ?? null;
	}

	/**
	 * The second line: what is being done, and on a folded row what is under it:
	 * "Downloading, 8 steps: 7 done, 1 running" (a middle dot between). The doing is dropped when it is already the first
	 * line (whole-library work names no file, so it leads with what it is).
	 *
	 * A running or done job's own note goes beside it: what it is doing, or what it ended with.
	 */
	function doingOf(job: Job, steps: string | null): string | null {
		const doing = job.subject || job.steps?.subject ? job.name : null;
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

	/* All of them at once.
	 *
	 * Failures arrive in batches (a fix to how a kind of file is read, a drive that was unplugged
	 * and is back), and a row at a time is not something anybody does with forty of them. Offered
	 * only when there is something to press it for, so it is not a permanently lit button that
	 * usually does nothing. */
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

	/* And throwing them away, which running them again cannot replace.
	 *
	 * A failure that cannot succeed is offered again, fails again, and is still there. Without this
	 * the only way to empty a screen full of them would be to retry every one and watch it fail twice.
	 * Beside the retry rather than instead of it: they answer different questions. */
	let clearingAll = $state(false);

	/* And stopping the whole queue, which neither of the two above can do.
	 *
	 * Those act on failures. This one is for work that has not gone wrong at all and is simply
	 * not wanted: a folder that turned out to hold far more than anybody meant to point at, a
	 * setting switched on that queued work for the entire library. Fifty thousand rows is not
	 * something anybody stops one at a time, and without this the only way out is to close the
	 * application.
	 *
	 * Behind a confirmation even though it is reversible: `doStartCanceled` below puts every
	 * stopped job back. What is not reversible is the machine time already spent on the work that
	 * was under way, and a press that stops an import somebody has been waiting hours for should
	 * be deliberate.
	 */
	let cancelingAll = $state(false);
	let cancelAllOpen = $state(false);

	/* And starting them again.
	 *
	 * A stop leaves the rows in the table with their payloads, so the work can simply be offered
	 * again, where the only other route back to the same work is to scan the folders, which
	 * re-walks the whole library to rediscover files it already knew about.
	 *
	 * Behind a confirmation for the same reason the stop is: on a library this is hours of the
	 * machine, and a button that quietly starts fifty thousand jobs is not one to press by
	 * accident.
	 */
	let startingCanceled = $state(false);
	let startCanceledOpen = $state(false);

	const outstanding = $derived(
		(counts.queued ?? 0) + (counts.running ?? 0) + (counts.blocked ?? 0)
	);
	const canceled = $derived(counts.canceled ?? 0);
	// `failures` rather than `failed`, which is what a queue's own tally of them is called a few
	// lines up. Two things called the same word in one file is how the wrong one gets read.
	const failures = $derived(counts.failed ?? 0);

	/*
	 * A BULK ACTION IS OFFERED FOR THE PILE BEING LOOKED AT, and that is the whole rule.
	 *
	 * Offered regardless of the filter, a button called "Clear them" would mean something the
	 * reader could not see: with 100,000 stopped jobs and exactly ONE failure, filtered to the
	 * stopped ones, the screen would show a row of them and a button reading "Clear them", which
	 * clears the one failure and leaves every row on screen where it was, reading as a button that
	 * does not work while it works perfectly.
	 *
	 * Two halves and both are needed. This is the first: an action for failures is offered while
	 * looking at failures or at everything, never while looking at some other pile. The second is
	 * that every label names its pile and its number, so "them" is never a pronoun with nothing on
	 * screen to point at.
	 */
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

	/* And throwing the stopped ones away, which starting them again cannot replace.
	 *
	 * The two answers to a stopped job are opposite and both are real: the work is still wanted
	 * and was stopped by accident, or it is not wanted and the rows are just in the way. Nothing
	 * else sweeps them until a week after they were stopped.
	 */
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

<!--
	ONE ROW OF THE QUEUE, on the tab's declared columns: a family's top row on the folded view, a
	step under an opened family (`step`), or a matching step on a state's tab.

	A folded row names its file ONCE and says what is under it ("Downloading, 8 steps: 7 done,
	1 running"), with one badge: the server's verdict over the family, failed if anything failed,
	so folding never hides a failure. Its arrow, at the end, opens the steps; they are read then and
	not before. A step under it shows only the step: the file is the row above.
-->
{#snippet jobRow(job: Job, step: boolean)}
	{@const state = shownState(job)}
	{@const line = step ? null : stepsLine(job.steps)}
	{@const folds = queue.folded && !step && (job.steps?.count ?? 0) > 0}
	<DataRow
		{compact}
		indent={step ? 1 : 0}
		cells={phoneWidth.yes
			? { card: jobCard }
			: { name: named, status: badge, left: age, bar: moving, count: tries }}
		expanded={folds ? queue.isOpen(job.id) : undefined}
		ontoggle={folds ? () => void queue.toggle(job.id) : undefined}
		toggleLabel="the steps of {subjectOf(job)}"
	>
		{#snippet actions()}
			<!-- Run again reads the row's OWN state: a folded download that is done with a failed
			     step has nothing of its own to run again, and the step is one press away under it.
			     Cancel reads what the row SHOWS: cancelling a top calls off every step it started
			     that is still to finish (the server walks the family), so a done download with a
			     step running is worth cancelling. -->
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
			<!--
				A DOWNLOAD'S ROW IS A POINTER, not a second download manager: its Site, its address,
				its cookies and why it stopped are on the Downloads screen, which honours
				`?job=<this job's id>` by picking the download and scrolling to it.
			-->
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
			{#if job.error}
				<!-- Already redacted on the way into the queue. This is the screen a screenshot in a
				     bug report is most likely to be of, and a raw path would ride out on it. -->
				<span class="why" class:asks={job.waits_for_password}>{job.error}</span>
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
	<!-- A job waiting for a TIME rather than for a worker says when it starts: without it a
	     backup scheduled overnight reads as "queued, 5h ago" beside work that really is stuck. -->
	{#snippet age()}
		{#if startsIn(job.run_after, now)}
			<!-- The moment it starts on the hover, as the moment it was asked for is below: a live
			     list says how far away, and the whole moment is one gesture off (`$lib/shell/when`). -->
			<Tooltip label={exactly(job.run_after ?? 0)}>
				<span class="scheduled">{startsIn(job.run_after, now)}</span>
			</Tooltip>
		{:else}
			<Tooltip label={exactly(job.created_at)}>
				<span class="when">{sayAgo(job.created_at, now)}</span>
			</Tooltip>
		{/if}
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
			<span class="card-facts">{@render badge()}{@render age()}{@render tries()}</span>
			{@render moving()}
		</span>
	{/snippet}
{/snippet}

<!-- The four tabs. Above everything, because they decide what everything under them is. -->
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
		<ScheduledTasks />
	{:else if tab === 'history'}
		{#key `${historyVerb}|${historyDecisions}`}
			<Ledger verb={historyVerb} decisions={historyDecisions} />
		{/key}
	{:else if tab === 'log'}
		<Logs />
	{:else}
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
							{compact}
						/>
						{#snippet partCard()}
							<span class="card">
								{@render partName()}
								<span class="card-facts">{@render partCount()}</span>
								{@render partBar()}
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
							actions={phoneWidth.yes ? undefined : runInTasks}
							expanded={line.kind === 'pass' && line.pass.parts.length > 0 && !phoneWidth.yes
								? openPasses.includes(line.pass.id)
								: undefined}
							ontoggle={line.kind === 'pass' && line.pass.parts.length > 0 && !phoneWidth.yes
								? () => togglePass(line.pass.id)
								: undefined}
							toggleLabel="the kinds of {one.title}"
							{compact}
						/>
						<!-- The one action: the task's row on Tasks, where it is run and its time is chosen.
					     A chore that is not a task (a download, a transcode) has none. On a phone it is
					     the card's last line instead (`lineCard`), and the list has no actions track. -->
						{#snippet runInTasks()}
							{#if line.kind === 'pass' || one.task}
								<SettingLink section="tasks" setting={taskRow(one)}>{COPY.runInTasks}</SettingLink>
							{/if}
						{/snippet}
						<!-- The row as a card: the same cells, one under another, in the order they read
					     across. The name with the row's one action at the card's end; the state, when
					     and the count on one line, each its own words; the bar the card's full width,
					     so every bar on the list is one length and reads against the next. -->
						{#snippet lineCard()}
							<span class="card">
								<span class="card-head">{@render title()}{@render runInTasks()}</span>
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
								<span class="pass-eta"
									><SettingLink section="importing">{one.when}</SettingLink></span
								>
							{:else}
								<span class="pass-eta">{one.when}</span>
							{/if}
						{/snippet}
						<!-- DONE OVER WHAT WANTS DOING, both counted from the library, so the bar is defined
					     while nothing runs. A pass of several kinds draws its bars on the rows under it. -->
						{#snippet passBar()}
							{#if line.kind === 'pass' && line.pass.parts.length === 0}
								<ProgressBar
									value={line.pass.progress}
									label={`${one.title} \u2014 ${line.pass.done}`}
								/>
							{/if}
						{/snippet}
						{#snippet passCount()}
							{#if line.kind === 'pass' && line.pass.parts.length === 0}
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

		<!--
			WHICH PILE IS BEING LOOKED AT, drawn by the strip every entity page and the Downloads
			screen draw.

			A second strip for the same job would be two answers to what a row of states looks like,
			so this is that row too. It cannot be LINKS here: this pane is inside the Settings
			sheet, opened over a page without leaving it, and a link would navigate and tear that
			page down. So the row is in its value mode: the same words, counts and mark, as a
			tablist that tells this screen which was pressed.

			The words are the badge's OWN, imported rather than retyped, so the tab and the badges
			in the rows underneath cannot come to call one state two things.
		-->
		<!-- THE LIST'S OWN HEADING AND ITS NARROWING BY KIND, in the pane's row form, as History
		     draws its Type and Action. Which STATE is the strip under it, so a second control for
		     the outcome would be two answers to one question. -->
		<div class="list-head">
			<SectionHeading>{COPY.list.heading}</SectionHeading>
			<LabelledRow label={COPY.list.type.label} help={COPY.list.type.help}>
				<Select
					label={COPY.list.type.label}
					value={queue.kind ?? EVERY_KIND}
					options={kindChoicesShown}
					onValueChange={(next) => void queue.setKind(next === EVERY_KIND ? null : next)}
				/>
			</LabelledRow>
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
			<!--
				EVERY BULK ACTION BEHIND ONE DOOR: each names its pile and number ("Clear the 120,000
				canceled"), and four such labels across a header grow past the page, while a menu's
				column never cuts one. `offeredFor` decides which rows the pile shown offers.
			-->
			<!-- ALWAYS DRAWN. It always holds at least the density row, so there is always
			     something behind it: a menu button that opened on nothing would read as a
			     screen refusing its own actions, and a control that comes and goes beside a
			     heading is one somebody has to look for. -->
			<MenuButton label="What can be done with these tasks" words="Options">
				<!-- Three parts: what to do with a pile, how the rows are drawn, and the two that
				     throw rows away, last and below their own line. -->
				{#if failedOffered || outstandingOffered || canceledOffered}
					<ContextMenuGroup>
						{#if outstandingOffered}
							<ContextMenuItem
								label="Cancel all {outstanding.toLocaleString()}"
								icon="close"
								disabled={cancelingAll}
								onselect={() => (cancelAllOpen = true)}
							/>
						{/if}
						{#if canceledOffered}
							<ContextMenuItem
								label="Resume the {canceled.toLocaleString()} canceled"
								icon="play_arrow"
								disabled={startingCanceled}
								onselect={() => (startCanceledOpen = true)}
							/>
						{/if}
						{#if failedOffered}
							<ContextMenuItem
								label="Try again ({failures.toLocaleString()} failed)"
								icon="sync"
								disabled={retryingAll}
								onselect={doRetryAll}
							/>
						{/if}
					</ContextMenuGroup>
				{/if}
				<!--
					THE ROW DENSITY, behind the same door as everything else this screen offers.

					It is a preference about how this screen is drawn, not a thing that happens to
					the queue, so it does not belong beside the heading, where as the only control
					always present it would read as more important than the actions it sat next to.

					A checkable row rather than a worded toggle: a box that is ticked or not says
					which way it is set, and the label then only has to name the thing. The `checked`
					prop is the row itself, so `aria-checked` carries the state.
				-->
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
								label="Clear the {canceled.toLocaleString()} canceled"
								icon="delete"
								destructive
								disabled={clearingCanceled}
								onselect={() => (clearCanceledOpen = true)}
							/>
						{/if}
						{#if failedOffered}
							<ContextMenuItem
								label="Clear the {failures.toLocaleString()} failed"
								icon="delete"
								destructive
								disabled={clearingAll}
								onselect={doClearAll}
							/>
						{/if}
					</ContextMenuGroup>
				{/if}
			</MenuButton>
		</div>
		<!-- Under the strip that carries the running count, while work uses a share of this device
		     because somebody is using it: why that count is lower, in the sidebar leaf's words. -->
		{#if queue.page?.stepping_back && (counts.running ?? 0) > 0}
			<div class="stepping-back"><Note>{COPY.steppingBack(queue.page)}</Note></div>
		{/if}

		<!-- The one list the strip filters, whichever state it is in. On the same columns as the list
		     above, so every status on the tab stands at one x. -->
		<div id={listId} role="tabpanel" aria-label="Tasks">
			<!-- NOTHING READ IS NOT NOTHING THERE. Until a read lands the list is a skeleton,
			     and a read that failed leaves the Problem above to speak: "No tasks are running
			     or waiting" must not be drawn while a busy server takes seconds to answer, nor
			     under the unreachable sentence. A failed read after a good one keeps the good
			     page (`Queue.refresh`). -->
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
			bind:open={cancelAllOpen}
			title="Cancel all {outstanding.toLocaleString()} tasks?"
			consequence="Running tasks are canceled too, so a running scan can't add the tasks back. Nothing is deleted, and a later scan picks up any file that was imported but not yet processed."
			confirmLabel="Cancel all tasks"
			cancelLabel="Keep running"
			destructive={false}
			onconfirm={doCancelAll}
		/>
	{/if}
</div>

<style>
	/* The four tabs sit apart from the Activity tab's own strip under them, which narrows one list. */
	.activity-tabs {
		margin-block-end: var(--space-5);
	}

	/* The strip and the pile's actions on one line, the strip at the start and the door at the
	   end. The space above is the space a group takes from the one before it, so the strip does
	   not stand flush against the last row of the list above. The arrangement inside is `Tabs`'s. */
	.filters {
		display: flex;
		align-items: center;
		justify-content: space-between;
		flex-wrap: wrap;
		gap: var(--space-4);
		margin-block: var(--space-4);
	}

	/* The sentence under the strip stands a step clear of the column heads under it, as the strip
	   itself does. */
	.stepping-back {
		margin-block-end: var(--space-4);
	}

	/* The list's heading and its Type row: the space above is the space a group takes from the one
	   before it. */
	.list-head {
		margin-block-start: var(--space-6);
	}

	/*
	 * A ROW AS A CARD, on a phone: the cells one under another, each starting where the name starts.
	 * The facts line wraps rather than cutting a word, because every fact on it is its own sentence
	 * ("Nothing waiting", "4 minutes ago") and half of one says nothing.
	 */
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

	.subject {
		display: flex;
		flex-direction: column;
		gap: 2px;
		min-width: 0;
	}

	/*
	 * COMPACT LAYS THE SUBJECT ALONG THE ROW instead of stacking it.
	 *
	 * Four rows out of five here carry two lines (the file, and what is being done to it), and
	 * two stacked lines are taller than either of `DataRow`'s floors, so stacked, the density
	 * switch would move nothing on them: a one-line row at 36px and every two-line row still at 50.
	 *
	 * Nothing is dropped. The name, the work and the reason it failed are all still there in the
	 * same order, reading along the row as a sentence rather than down it as a block, which is
	 * what makes one line of body text fit under the floor and the row become the floor.
	 */
	.subject.compact {
		flex-direction: row;
		align-items: baseline;
		gap: var(--space-2);
	}

	/* Sharing a line means each part has to be able to give way. Without a floor of nothing the
	   longest of them pushes the rest off the end of the row instead of being cut short. */
	.subject.compact .what,
	.subject.compact .doing,
	.subject.compact .why {
		flex: 0 1 auto;
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.what {
		color: var(--sift-ink);
		font: var(--text-body);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	/* The job's name, under the filename it is about. One step down and muted, so the row reads as
	   "this file, and this is what is happening to it" rather than as two facts of equal weight. */
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

	/* The note under an opened family (its steps on their way, or the press for more of them)
	   across the row's tracks rather than in the first of them. */
	.steps-note {
		grid-column: 1 / -1;
		padding-inline-start: var(--space-8);
		padding-block: var(--space-1);
	}

	.pass-title {
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
	}

	/* How the last run went is the Done column's fact: the cell spans Progress and Done, and the
	   words end on Done's edge under its heading, as the counts above them do. */
	.pass-last {
		display: block;
		text-align: end;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* A failed run's phrase, in the failure's ink, dotted under because there is more behind it:
	   the shape a task row's last dry run has. */
	.pass-last :global(.failed-run) {
		font: inherit;
		color: var(--sift-bad-text);
		text-decoration: underline dotted;
		text-underline-offset: 0.15em;
	}

	/* Words, so left: the column's own alignment. Right-aligned, a column of sentences of
	   different lengths has a ragged left edge. */
	.pass-eta {
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* The count column is declared `end`; this is only its colour and face. */
	.pass-count {
		color: var(--sift-ink-3);
		font: var(--text-data);
	}

	/* A wait for the password is read whole: cut to "Waiting for your password to unl..." it no
	   longer says which key. Every other reason keeps its one line. */
	.subject .why.asks {
		white-space: normal;
	}

	/* The field a row parked for the password opens, under the sentence that asks for it. */
	.unlock-here,
	.unlock-door {
		display: block;
		margin-block-start: var(--space-1);
	}

	.why {
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
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
