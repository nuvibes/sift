import type { components } from '$lib/api/schema';
import { NOT_ENOUGH_TO_SAY, sayWindow } from '$lib/shell/when';
import { sayDuration } from './labels';
import { sayAgo } from '$lib/shell/when';
import type { Column } from '$lib/components/common/DataRows.svelte';
import { LABELS, type BadgeState } from '$lib/components/common/Badge.svelte';
import { COPY } from './JobsScreen.search';

/* ONE ROW PER FAMILY, and what the row says about the work folded under it.
 *
 * A download is one top row with a probe step under it and seven steps under that (thumbnail,
 * preview, scrubbing previews, faces, Smart Search, watermark, music), and a row each would
 * repeat the file's name seven times. The server pages by family (`?fold=true`): each top row
 * arrives once, carrying `steps`, its family counted, and the steps themselves are asked for only
 * when somebody opens the row (`/jobs/{id}/steps`).
 *
 * Not regrouped here from a flat page. Fifty rows would be a window onto a handful of downloads,
 * because every step takes a row; and a family is as deep as a paged task has pages (a Generate
 * chain can be over a hundred deep), so an indent per level walks off the edge of the screen.
 * The window is the server's answer, and an opened family's steps are drawn one level in, in the
 * order they were handed out, however deep they sit.
 */

export type Job = components['schemas']['JobView'];
export type JobsPage = components['schemas']['JobsPage'];
export type Steps = components['schemas']['StepSummary'];
export type StepsPage = components['schemas']['StepsOfJob'];

/**
 * THE NOW TAB'S COLUMNS, declared once and read by both of its lists: the passes and housekeeping
 * above, and the queue below. Every track is a share of the width or a length, never a content
 * size, so the two lists (separate grids, with the state strip between them) stand their
 * columns at the same x and a page turn moves nothing.
 *
 * Two grids each sizing their own `max-content` tracks would start the status column thirty pixels
 * apart. "Time left" is words, so it is left-aligned; only the count is a number, so only the count
 * is right.
 *
 * The status column holds the longest words on the row ("12 in progress", "Waiting for
 * quiet hours"), so it takes as much of the width as the name does. At 1.4 shares against the
 * name's 3 it is narrower than a running task's pill in an ordinary window, and the pill runs on
 * under Time left; 2.2 leaves it room. A word longer than even that is cut short by
 * the pill itself (`Badge`), with the whole of it on the hover, and never drawn over the next
 * column.
 */
export const ACTIVITY_COLUMNS: readonly Column[] = [
	{ id: 'name', width: 'minmax(0, 2.2fr)' },
	{ id: 'status', label: COPY.columns.status, width: 'minmax(0, 2.2fr)' },
	{ id: 'left', label: COPY.columns.left, width: 'minmax(0, 1.4fr)' },
	{ id: 'bar', label: COPY.columns.progress, width: 'minmax(0, 1fr)' },
	{ id: 'count', label: COPY.columns.done, width: 'minmax(0, 1.4fr)', align: 'end' }
];

/**
 * How wide a row's actions are ("Run in Tasks", or two glyph buttons and the arrow): their track
 * on a touch screen. With a pointer they are laid over the row's end and take none.
 *
 * Both lists also declare `folds`: the queue's rows fold, and the summary keeps the same small
 * arrow track so its columns stand over the queue's.
 */
export const ACTIVITY_ACTIONS = '8rem';

/**
 * THE SAME LISTS ON A PHONE, as cards: one column, and each row draws its cells inside it, one under
 * another, in the order the columns read across (the name; its state, when and how many; the bar).
 *
 * Five tracks do not fit a phone's width: the name would be cut to a word a line, "Time left" and
 * "Progress" printed over each other and a count stood as three lines of one number each. A card
 * keeps every word the row says and gives each its own line, rather than dropping a column. The
 * columns' headings go with the columns: every fact on a card says what it is by its own words.
 */
export const ACTIVITY_CARD: readonly Column[] = [{ id: 'card', width: 'minmax(0, 1fr)' }];

/**
 * The actions track on a phone's cards: a job's two glyph presses and its arrow. A summary card has
 * none, because its one action (the task's row on Tasks) is a line of the card itself.
 */
export const ACTIVITY_CARD_ACTIONS =
	'calc(2 * var(--control-height) + var(--control-height-sm) + 2 * var(--space-1))';

/** The order a family's steps are counted out in: what is finished first, as it is read. */
const STEP_ORDER = ['done', 'failed', 'running', 'paused', 'blocked', 'queued', 'canceled'];

/** A count that may have stopped at the server's cap: "1,000+" then. */
function counted(value: number, steps: Steps): string {
	const said = value.toLocaleString();
	return steps.at_least && value >= steps.cap ? `${said}+` : said;
}

/**
 * The folded row's line: "8 steps: 7 done, 1 running". Null for a top that started nothing, which
 * says nothing about steps rather than "0 steps".
 */
export function stepsLine(steps: Steps | null | undefined): string | null {
	if (!steps || steps.count === 0) return null;
	const noun = steps.count === 1 && !steps.at_least ? 'step' : 'steps';
	const parts = STEP_ORDER.filter((state) => (steps.by_state[state] ?? 0) > 0).map(
		(state) => `${counted(steps.by_state[state], steps)} ${state}`
	);
	// A state the server counts and this list does not name is still said, after the rest.
	for (const [state, many] of Object.entries(steps.by_state)) {
		if (!STEP_ORDER.includes(state) && many > 0) parts.push(`${counted(many, steps)} ${state}`);
	}
	const head = `${counted(steps.count, steps)} ${noun}`;
	return parts.length > 0 ? `${head}: ${parts.join(', ')}` : head;
}

/**
 * The ONE state a row shows. On a folded row it is the server's verdict over the whole family
 * (failed if anything failed, so folding never hides a failure) and never the top's own state:
 * a download reads done the moment its file lands, while its steps may still be running or have
 * failed. Everywhere else a row is its own state.
 */
export function shownState(job: Job): Job['state'] {
	return job.steps?.state ?? job.state;
}

/** How many of a family's steps are still to finish: what a cancel on its top calls off. */
export function stepsLeft(steps: Steps | null | undefined): number {
	if (!steps) return 0;
	return ['queued', 'running', 'blocked', 'paused'].reduce(
		(sum, state) => sum + (steps.by_state[state] ?? 0),
		0
	);
}

/* ---------------------------------------------------------------------------------------------
 * THE LONG PASSES, as the Activity screen reads them.
 *
 * Here rather than inside the screen because the whole of it is arithmetic and wording over the
 * page the server sends, and neither could be tested while it lived in a `$derived` inside the
 * markup.
 * -------------------------------------------------------------------------------------------
 */

/** The order the passes are read in. Declared here, not taken from the object's keys: what comes
 *  back is a map, and the order of its keys is not something to hang a stable screen on. */
const FAMILY_ORDER = ['scan', 'generate', 'fingerprint', 'identify', 'semantic'];

/**
 * What a pass's time column says when the work cannot run at all and the server gave no reason.
 *
 * NOT AN ESTIMATE. The ledger's rate has memory (it falls back to the last run of the same family
 * on this install), so a pass that cannot start still has a number, and "Identify, 14 days, 91,000
 * to do" on an install where recognition cannot run a single file (the runtime its card needs is
 * not installed, so every scan fails) is a time remaining for work that will never begin: the one
 * kind of wrong that looks like information.
 *
 * The server declares a reason per family, with the feature's own words, including "the feature
 * is switched off", which is not a runtime and not a fault. This is the fallback for a build that
 * sends none.
 */
export const WAITING_FOR_RUNTIME = 'Waiting for the recognition runtime';

/** The tone of the word in the "Now" column, in the shared Badge/pill vocabulary. */
type PassTone = 'good' | 'warn' | 'accent' | 'plain';

/** One kind of work inside a pass, counted on its own: a sub-row under the pass. */
export interface PassPart {
	/** The kind's job type on the wire, which keys the row. */
	type: string;
	/** The name cell: what one file of the work is, in the words its handler declared:
	 *  "Files looked at for faces". */
	label: string;
	/** Done over what wants doing, 0-100. The bar. */
	progress: number;
	/** The count cell: "9,000 of 100,000". */
	count: string;
}

/** One of the long passes, ready to draw. */
export interface Pass {
	/** The family's key on the wire, which is what a press acts on. */
	id: string;
	/** The family's name, as the server labels it. */
	title: string;
	/** The word in the "Now" column: what this pass is doing, or why it is doing nothing. */
	now: string;
	/** How to colour that word. */
	tone: PassTone;
	/** The "Time left" column: a range, a reason, or one of the two honest fallbacks. */
	when: string;
	/** Whether `when` is a pointer at the setting that decides it, rather than a plain sentence. */
	settingLink: boolean;
	/** How much of the pass's work is finished, 0-100. The bar, and nothing else. */
	progress: number;
	/** The line under the bar: what is done out of what wants doing. */
	done: string;
	/**
	 * Each kind of the pass's work with a count of its own, when there is MORE THAN ONE. Empty
	 * otherwise, and then `progress` and `done` are the row's one bar and one line.
	 *
	 * Identify is the faces pass and the watermark read, and "9,000 of 200,000" over the two would
	 * be the library counted twice: a figure about neither. With two or more parts the row draws one
	 * indented sub-row per part (its name, its bar, its count) and the summed figure is not
	 * shown.
	 */
	parts: PassPart[];
	/** The task this pass IS on Tasks, whose row is where it is run and scheduled, or null. */
	task: string | null;
	/** Whether this pass is allowed to do anything: switched on, and able to run here. */
	allowed: boolean;
}

/**
 * Which task on Tasks a pass or a chore is, as the server says it: a fact about the server's
 * registrations, carried on the wire, never a map here. A pass the server names no task for
 * (Identify is more than one) links to Tasks at the top.
 */
function taskOf(declared: { task: string | null }): string | null {
	return declared.task || null;
}

/** What the time column says when the sample cannot price the work. */
export const NOT_ENOUGH = NOT_ENOUGH_TO_SAY;

/** And what it says while nothing is waiting at all. */
export const NOTHING_WAITING = 'Nothing waiting';

/* A time-left window stands alone in its column, beside "Nothing waiting": so it opens with a
   capital, as a cell does. `sayWindow` writes it lower case for use inside a sentence. */
function standsAlone(window: string): string {
	return window.charAt(0).toUpperCase() + window.slice(1);
}

/** Where a switched-off pass is switched back on. */
const CHANGE_IN_IMPORTING = 'Turn on in Importing';

/**
 * The passes the server declared, each with its state, its range, its bar and its press.
 *
 * ## Four columns, and every one of them from the server
 *
 * The whole library priced from the single newest run of the family, with no minimum sample, would
 * let a run of one arriving file set the price of a hundred thousand: wrong by tens of times. A
 * bar of `done / (done + failed + waiting)` counted from the RUN would draw empty for a family with
 * no run, on a library that was entirely finished. Both numbers come from the library and the
 * ledger's own sample; this file does arithmetic on neither.
 *
 * What it does is choose the words, which is all it should ever do.
 */
export function passes(page: JobsPage | null): Pass[] {
	return FAMILY_ORDER.flatMap((family) => {
		const declared = page?.families?.[family];
		if (!declared) return [];
		let outstanding = 0;
		let waiting = 0;
		if (declared.outstanding !== undefined && declared.waiting !== undefined) {
			// The server's own attribution: a task of a product-carrying kind counts under the
			// family of what it MAKES, so a pressed Smart Search run is Smart Search's work here
			// and not Identify's, whatever its task type is called. Summed by type, it would be
			// Identify's.
			outstanding = declared.outstanding;
			waiting = declared.waiting;
		} else {
			for (const type of declared.types) {
				const kind = page?.work?.[type];
				if (!kind) continue;
				outstanding += kind.outstanding;
				// `waiting` is counted from the LIBRARY and already includes whatever is queued, so
				// it replaces the queue's number rather than adding to it. Null where nothing can
				// count that kind (the folder walk is the real case, since there is no record of
				// a file nobody has seen yet), and then the queue's own count of FILES is left.
				waiting += kind.waiting ?? Math.ceil(kind.left_units ?? kind.outstanding);
			}
		}
		const on = declared.on !== false;
		const ready = declared.ready !== false;
		const total = declared.total ?? 0;
		const finished = Math.min(declared.done ?? 0, total);
		const range = standsAlone(sayWindow(declared.quick_seconds, declared.slow_seconds));
		const idle = waiting === 0 && outstanding === 0;
		return [
			{
				id: family,
				title: declared.label,
				now: nowWord(declared, { on, ready, outstanding, idle }),
				tone: nowTone({ on, ready, outstanding, idle, reason: declared.reason ?? null }),
				// The reason a pass cannot run belongs in this column: it is the column somebody
				// reads to find out when the work will be done, and "it cannot start" is an answer
				// to that question rather than a different subject.
				when: !on
					? CHANGE_IN_IMPORTING
					: !ready
						? (declared.problem ?? WAITING_FOR_RUNTIME)
						: idle
							? NOTHING_WAITING
							: range,
				settingLink: !on,
				// Done over what wants doing, from the library, so a finished library is full.
				progress: total > 0 ? (finished / total) * 100 : 0,
				done:
					total > 0 ? `${finished.toLocaleString()} of ${total.toLocaleString()}` : NOTHING_WAITING,
				parts: partsOf(declared),
				task: taskOf(declared),
				allowed: on && ready
			}
		];
	});
}

/** The parts a row draws on their own lines: two or more, or none. See `Pass.parts`. */
function partsOf(declared: NonNullable<JobsPage['families']>[string]): PassPart[] {
	const counted = (declared.parts ?? []).filter((part) => part.total > 0);
	if (counted.length < 2) return [];
	return counted.map((part) => {
		const finished = Math.min(part.done, part.total);
		return {
			type: part.type,
			label: part.caption.charAt(0).toUpperCase() + part.caption.slice(1),
			progress: (finished / part.total) * 100,
			count: `${finished.toLocaleString()} of ${part.total.toLocaleString()}`
		};
	});
}

interface How {
	on: boolean;
	ready: boolean;
	outstanding: number;
	idle: boolean;
}

/**
 * A running STATUS, in the In progress chip's own word (`Badge`'s `running`): "In progress", or "12
 * in progress" where several run together. The one word every screen says for work under way, so a
 * pass, a chore and a job row in the list under them read alike, and the chip is the blue one with
 * the turning mark (the `accent` tone, which the Now column draws as `running`).
 */
function inProgress(together: number): string {
	return together > 1
		? `${together.toLocaleString()} ${LABELS.running.toLowerCase()}`
		: LABELS.running;
}

/**
 * The chip the "Now" column draws for a tone: up to date is Done's, waiting on something is
 * Blocked's, work under way is the In progress chip the job rows under it wear, and anything else
 * is quiet.
 */
export function nowState(tone: PassTone): BadgeState {
	if (tone === 'good') return 'done';
	if (tone === 'warn') return 'blocked';
	if (tone === 'accent') return 'running';
	return 'queued';
}

/** The word in the "Now" column. Switched off first, then whether it can run here at all. */
function nowWord(declared: JobsPage['families'][string], how: How): string {
	if (!how.on) return 'Turned off';
	if (!how.ready) return "Can't run yet";
	// The server's "Nothing waiting" is the time-left column's sentence; here it is the state
	// it describes, so the two columns do not say one thing twice.
	if (declared.reason && declared.reason !== NOTHING_WAITING)
		return declared.reason.replace(/\.$/, '');
	if (how.outstanding > 0) {
		const at = declared.at_once ?? 0;
		return inProgress(at);
	}
	// Files lack the work and nothing is queued for it: nothing happens until somebody presses
	// Run now or a scan brings new files. "Waiting" promised otherwise.
	return how.idle ? 'Up to date' : 'Not started';
}

function nowTone(how: How & { reason: string | null }): PassTone {
	if (!how.on) return 'plain';
	if (!how.ready) return 'warn';
	/* Work that is held (for quiet hours, or the night window) is outstanding and not running,
	   and the word beside it says so; the running colour under "Waiting for quiet hours" would say
	   the opposite of its own word. The same tone a held pass with nothing queued yet has below. */
	if (how.outstanding > 0 && how.reason && how.reason !== NOTHING_WAITING) return 'warn';
	if (how.outstanding > 0) return 'accent';
	if (how.idle) return 'good';
	return how.reason ? 'warn' : 'plain';
}

/* ---------------------------------------------------------------------------------------------
 * THE HOUSEKEEPING, drawn beside the passes with the same four columns.
 *
 * A folder pass that never succeeds and a five-minute query can take most of an install's machine
 * time in a week, and the screen that exists to say what Sift is doing has to show them.
 * -------------------------------------------------------------------------------------------
 */

export type ChoreView = {
	id: string;
	title: string;
	now: string;
	tone: PassTone;
	when: string;
	last: string;
	/** Why the last run failed, in its job row's own first line, or null for one that did not. */
	why: string | null;
	task: string | null;
};

export function chores(page: JobsPage | null, now: number): ChoreView[] {
	return (page?.housekeeping ?? []).map((one) => {
		const running = one.running > 0;
		const failed = one.failed > 0 && one.outstanding === 0;
		/* Why it is not running, in the server's sentence: "Waiting for quiet hours." over a
		   sweep whose task waits for them, not a bare "Waiting" under a pass held the same way
		   that says so. Its tone is the held pass's, for the pass's reason. */
		const held = !running && one.outstanding > 0 && one.reason ? one.reason : null;
		const range = standsAlone(sayWindow(one.quick_seconds, one.slow_seconds));
		return {
			id: one.job_type,
			title: one.label,
			now: running
				? inProgress(one.running)
				: failed
					? failedWord(one.failed)
					: held
						? held.replace(/\.$/, '')
						: one.outstanding > 0
							? 'Waiting'
							: 'Up to date',
			tone: running ? 'accent' : failed || held ? 'warn' : one.outstanding > 0 ? 'plain' : 'good',
			/* Running and waiting on somebody (a swap, before the other side is there): the server's
			   words for it, rather than a time that nothing is counting down. */
			when: one.outstanding > 0 ? (running && one.reason ? one.reason : range) : NOTHING_WAITING,
			last: lastRun(one, now),
			why: one.last_state === 'failed' ? (one.last_error ?? null) : null,
			task: taskOf(one)
		};
	});
}

function failedWord(failed: number): string {
	return failed === 1 ? 'Failed once' : `Failed ${failed.toLocaleString()} times`;
}

/** The last column: when this last ran and how it went, or that it never has. A phrase standing
 *  alone in its cell, so it starts with a capital as "Never" and "Started" do beside it. */
function lastRun(one: JobsPage['housekeeping'][number], now: number): string {
	if (one.last_started_at === null || one.last_started_at === undefined) return 'Never';
	const when = sayAgo(one.last_started_at, now);
	if (one.last_state === 'running') return `Started ${when}`;
	if (one.last_state !== 'done') return standsAlone(`${when}, ${one.last_state ?? 'stopped'}`);
	const took = one.last_seconds;
	return standsAlone(
		took === null || took === undefined ? when : `${when}, ${sayDuration(took) ?? ''}`.trim()
	);
}
