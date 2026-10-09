import type { components } from '$lib/api/schema';
import { NOT_ENOUGH_TO_SAY, sayAtLeast, sayWindow } from '$lib/shell/when';
import { sayDuration } from './labels';
import { sayAgo } from '$lib/shell/when';
import type { Column } from '$lib/components/common/DataRows.svelte';
import { LABELS, type BadgeState } from '$lib/components/common/Badge.svelte';
import { COPY } from './JobsScreen.search';

/* ONE ROW PER FAMILY, and what the row says about the work folded under it. */

export type Job = components['schemas']['JobView'];
export type JobsPage = components['schemas']['JobsPage'];
export type Steps = components['schemas']['StepSummary'];
export type StepsPage = components['schemas']['StepsOfJob'];
export type RunPress = components['schemas']['RunPress'];

/** THE NOW TAB'S COLUMNS, declared once and read by both of its lists: the passes and
 * housekeeping above, and the queue below. */
export const ACTIVITY_COLUMNS: readonly Column[] = [
	{ id: 'name', width: 'minmax(0, 2.2fr)' },
	{ id: 'status', label: COPY.columns.status, width: 'minmax(17rem, 2.2fr)' },
	{ id: 'left', label: COPY.columns.left, width: 'minmax(0, 1.4fr)' },
	{ id: 'bar', label: COPY.columns.progress, width: 'minmax(0, 1fr)' },
	{ id: 'count', label: COPY.columns.done, width: 'minmax(0, 1.4fr)', align: 'end' }
];

/** How wide a row's actions are ("Run now" and two glyph buttons, or two glyph buttons and the
 * arrow): their track on a touch screen. */
export const ACTIVITY_ACTIONS = '11rem';

/** THE SAME LISTS ON A PHONE, as cards: one column, and each row draws its cells inside it, one
 * under another, in the order the columns read across (the name; its state, when and how many;
 * the bar). */
export const ACTIVITY_CARD: readonly Column[] = [{ id: 'card', width: 'minmax(0, 1fr)' }];

/** The actions track on a phone's cards: a job's two glyph presses and its arrow. */
export const ACTIVITY_CARD_ACTIONS =
	'calc(2 * var(--control-height) + var(--control-height-sm) + 2 * var(--space-1))';

/** A pile an Options action names: the rows it acts on, and where the list shows fewer (its steps
 * are folded under their tasks), the tasks the list shows first: "77 failed, 101 with their
 * steps". */
export function pile(rows: number, listed: number | undefined, word = ''): string {
	const said = `${rows.toLocaleString()}${word ? ` ${word}` : ''}`;
	if (listed === undefined || listed === rows) return said;
	return `${listed.toLocaleString()}${word ? ` ${word}` : ''}, ${rows.toLocaleString()} with their steps`;
}

/** The order a family's steps are counted out in: what is finished first, as it is read. */
const STEP_ORDER = ['done', 'failed', 'running', 'paused', 'blocked', 'queued', 'canceled'];

/** A count that may have stopped at the server's cap: "1,000+" then. */
function counted(value: number, steps: Steps): string {
	const said = value.toLocaleString();
	return steps.at_least && value >= steps.cap ? `${said}+` : said;
}

/** The folded row's line: "8 steps: 7 done, 1 running". */
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

/** The ONE state a row shows. */
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
 * THE LONG PASSES, as the Activity screen reads them. */

/** The order the passes are read in. Declared here, not taken from the object's keys: what comes
 *  back is a map, and the order of its keys is not something to hang a stable screen on. */
const FAMILY_ORDER = ['scan', 'generate', 'fingerprint', 'identify', 'semantic'];

/** What a pass's time column says when the work cannot run at all and the server gave no reason. */
export const WAITING_FOR_RUNTIME = 'Waiting for the recognition runtime';

/** The tone of the word in the "Now" column, in the shared Badge/pill vocabulary. */
type PassTone = 'good' | 'warn' | 'accent' | 'plain';

/** One kind of work inside a pass, counted on its own: a sub-row under the pass. */
export interface PassPart {
	/** The kind's job type on the wire, which keys the row and names it to a pause or a cancel. */
	type: string;
	/** Switched off with nothing of it queued: drawn, and counted in none of the pass's figures. */
	off: boolean;
	/** Whether somebody paused this sub-task. */
	paused: boolean;
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
	/** Each kind of the pass's work with a count of its own, when there is MORE THAN ONE. */
	parts: PassPart[];
	/** The task this pass IS on Tasks, whose row is where it is run and scheduled, or null. */
	task: string | null;
	/** What Run now presses: each task, or the part of it that is this pass's. */
	runs: RunPress[];
	/** Whether somebody paused this pass: nothing new of it starts until Resume. */
	paused: boolean;
	/** Whether work of it is under way: its own row then draws its bar and count, sub-rows or not. */
	moving: boolean;
	/** Whether this pass is allowed to do anything: switched on, and able to run here. */
	allowed: boolean;
	/** Why its last run failed, while that failure stands and nothing runs, or null. */
	why: string | null;
	/** Files its products gave up on, and those products: the Now column's "1 left out". */
	leftOut: number;
	leftOutOf: string[];
}

/** What the page does not carry: each task's When by task id, and each product's left-out files. */
export interface Around {
	whens?: Readonly<Record<string, string>>;
	leftOut?: Readonly<Record<string, number>>;
}

/** The products whose left-out files are each pass's, by the keys the build sheet gives them. */
const LEFT_OUT_OF: Readonly<Record<string, readonly string[]>> = {
	generate: ['thumbnails', 'previews', 'sprites'],
	fingerprint: ['fingerprints', 'music'],
	identify: ['faces', 'watermarks'],
	semantic: ['meaning']
};

/** Which task on Tasks a pass or a chore is, as the server says it: a fact about the server's
 * registrations, carried on the wire, never a map here. */
function taskOf(declared: { task: string | null }): string | null {
	return declared.task || null;
}

/** What the time column says when the sample cannot price the work. */
export const NOT_ENOUGH = NOT_ENOUGH_TO_SAY;

/** And what it says while nothing is waiting at all. */
export const NOTHING_WAITING = 'Nothing waiting';

/* A time-left window stands alone in its column, beside "Nothing waiting": so it opens with a
   capital, as a cell does. */
function standsAlone(window: string): string {
	return window.charAt(0).toUpperCase() + window.slice(1);
}

/** What a sub-task switched off says beside its count: why its files are not in the pass's. */
export const SUB_TASK_OFF = 'Turned off';

/** Where a switched-off pass is switched back on. */
const CHANGE_IN_IMPORTING = 'Turn on in Import tasks';

/** The passes the server declared, each with its state, its range, its bar and its press. */
export function passes(page: JobsPage | null, around: Around = {}): Pass[] {
	return FAMILY_ORDER.flatMap((family) => {
		const declared = page?.families?.[family];
		if (!declared) return [];
		let outstanding = 0;
		let waiting = 0;
		if (declared.outstanding !== undefined && declared.waiting !== undefined) {
			// The server's own attribution: a task of a product-carrying kind counts under the
			// family of what it MAKES, so a pressed Smart Search run is Smart Search's work here
			// and not Identify's, whatever its task type is called.
			outstanding = declared.outstanding;
			waiting = declared.waiting;
		} else {
			for (const type of declared.types) {
				const kind = page?.work?.[type];
				if (!kind) continue;
				outstanding += kind.outstanding;
				// `waiting` is counted from the LIBRARY and already includes whatever is queued, so
				// it replaces the queue's number rather than adding to it.
				waiting += kind.waiting ?? Math.ceil(kind.left_units ?? kind.outstanding);
			}
		}
		const on = declared.on !== false;
		const ready = declared.ready !== false;
		const total = declared.total ?? 0;
		const finished = Math.min(declared.done ?? 0, total);
		const range = standsAlone(
			declared.at_least
				? sayAtLeast(declared.quick_seconds)
				: sayWindow(declared.quick_seconds, declared.slow_seconds)
		);
		const idle = waiting === 0 && outstanding === 0 && !declared.time_unknown;
		const { failed, why } = failureOf(declared, { on, ready, outstanding });
		const { leftOutOf, leftOut, when } = aroundOf(family, declared, around);
		const how = { on, ready, outstanding, idle, failed, waiting, leftOut, when };
		return [
			{
				id: family,
				title: declared.label,
				now: nowWord(declared, how),
				tone: nowTone({ ...how, reason: declared.reason ?? null }),
				when: timeLeft(declared, { on, ready, idle, outstanding }, range),
				settingLink: !on,
				// Done over what wants doing, from the library, so a finished library is full.
				progress: total > 0 ? (finished / total) * 100 : 0,
				done:
					total > 0 ? `${finished.toLocaleString()} of ${total.toLocaleString()}` : NOTHING_WAITING,
				parts: partsOf(declared),
				task: taskOf(declared),
				runs: declared.runs ?? [],
				paused: declared.paused ?? false,
				moving: outstanding > 0,
				allowed: on && ready,
				why,
				leftOut,
				leftOutOf
			}
		];
	});
}

/** A pass's products' left-out files, and the When of the task it runs as. */
function aroundOf(
	family: string,
	declared: NonNullable<JobsPage['families']>[string],
	around: Around
): { leftOutOf: string[]; leftOut: number; when: string | undefined } {
	const count = (one: string) => around.leftOut?.[one] ?? 0;
	const leftOutOf = (LEFT_OUT_OF[family] ?? []).filter((one) => count(one) > 0);
	const task = declared.task || declared.runs?.[0]?.task || family;
	return {
		leftOutOf,
		leftOut: leftOutOf.reduce((sum, one) => sum + count(one), 0),
		when: around.whens?.[task]
	};
}

/** When the work will be done, "it cannot start" included, then what waits and the read's pace. */
function timeLeft(
	declared: NonNullable<JobsPage['families']>[string],
	how: { on: boolean; ready: boolean; idle: boolean; outstanding: number },
	range: string
): string {
	if (!how.on) return CHANGE_IN_IMPORTING;
	if (!how.ready) return declared.problem ?? WAITING_FOR_RUNTIME;
	// Work nothing queued or held has no time left, the Now column says what starts it; nor has
	// under a minute of work.
	const held = declared.reason && declared.reason !== NOTHING_WAITING;
	const quiet = (how.outstanding === 0 && !held) || range === UNDER_A_MINUTE;
	let when = how.idle ? NOTHING_WAITING : (declared.time_unknown ?? (quiet ? '' : range));
	for (const then of [declared.for_task, declared.pace])
		if (then) when = when ? `${when.replace(/\.$/, '')}. ${then}` : then;
	return when;
}

function failureOf(
	declared: NonNullable<JobsPage['families']>[string],
	how: { on: boolean; ready: boolean; outstanding: number }
): { failed: number; why: string | null } {
	const failed = how.on && how.ready && how.outstanding === 0 ? (declared.failed ?? 0) : 0;
	return { failed, why: failed > 0 ? (declared.last_error ?? null) : null };
}

/** The parts a row draws on their own lines: two or more, or none. See `Pass.parts`. */
function partsOf(declared: NonNullable<JobsPage['families']>[string]): PassPart[] {
	const counted = (declared.parts ?? []).filter((part) => part.total > 0);
	if (counted.length < 2) return [];
	return counted.map((part) => {
		const finished = Math.min(part.done, part.total);
		const off = part.on === false;
		const count = `${finished.toLocaleString()} of ${part.total.toLocaleString()}`;
		return {
			type: part.type,
			off,
			paused: part.paused ?? false,
			label: part.caption.charAt(0).toUpperCase() + part.caption.slice(1),
			progress: (finished / part.total) * 100,
			count: off ? `${count} \u00b7 ${SUB_TASK_OFF}` : count
		};
	});
}

interface How {
	on: boolean;
	ready: boolean;
	outstanding: number;
	idle: boolean;
	failed: number;
	waiting: number;
	leftOut: number;
	/** The task's When (`work`, `quiet`, `press`), where the task list has said. */
	when?: string;
}

const UNDER_A_MINUTE = standsAlone(sayWindow(0, 0));

/** A running STATUS, in the In progress chip's own word (`Badge`'s `running`): "In progress", or
 * "12 in progress" where several run together. */
function inProgress(together: number): string {
	return together > 1
		? `${together.toLocaleString()} ${LABELS.running.toLowerCase()}`
		: LABELS.running;
}

/** The chip the "Now" column draws for a tone: up to date is Done's, waiting on something is
 * Blocked's, work under way is the In progress chip the job rows under it wear, and anything
 * else is quiet. */
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
	// The server's "Nothing waiting" is the time-left column's sentence; here it is the state it
	// describes, so the two columns do not say one thing twice.
	if (declared.reason && declared.reason !== NOTHING_WAITING)
		return declared.reason.replace(/\.$/, '');
	if (how.outstanding > 0) return inProgress(declared.running ?? 0);
	if (how.failed > 0) return failedWord(how.failed);
	if (how.idle) return how.leftOut > 0 ? COPY.leftOut(how.leftOut) : 'Up to date';
	// Nothing queued: what waits, and what starts it ("48 waiting, runs as files arrive").
	return COPY.waiting(how.waiting, how.when) ?? 'Not started';
}

function nowTone(how: How & { reason: string | null }): PassTone {
	if (!how.on) return 'plain';
	if (!how.ready) return 'warn';
	/* Work that is held (for quiet hours, or the night window) is outstanding and not running,
	   and the word beside it says so; the running colour under "Waiting for quiet hours" would
	   say the opposite of its own word. */
	if (how.outstanding > 0 && how.reason && how.reason !== NOTHING_WAITING) return 'warn';
	if (how.outstanding > 0) return 'accent';
	if (how.failed > 0 || (how.idle && how.leftOut > 0)) return 'warn';
	if (how.idle) return 'good';
	return how.reason && how.reason !== NOTHING_WAITING ? 'warn' : 'plain';
}

/* ---------------------------------------------------------------------------------------------
 * THE HOUSEKEEPING, drawn beside the passes with the same four columns. */

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
		/* Why it is not running, in the server's sentence: "Waiting for quiet hours." */
		const held = !running && one.outstanding > 0 && one.reason ? one.reason : null;
		const said = standsAlone(sayWindow(one.quick_seconds, one.slow_seconds));
		const range = said === UNDER_A_MINUTE ? '' : said;
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
			/* Running and waiting on somebody (a swap, before the other side is there): the
			   server's words for it, rather than a time that nothing is counting down. */
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

/** The last column: when this last ran and how it went, or that it never has. */
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
