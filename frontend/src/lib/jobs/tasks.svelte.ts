/* Every task Sift runs over the library, when each one runs, and the one press that runs it.
 *
 * ONE STORE FOR THE WHOLE APP, because one task is drawn in two places. The Tasks screen lists every
 * task's When with quiet hours at the top, and the section that owns a task draws the same row beside
 * the thing it does: Faces beside recognition, Importing beside each stage. Two stores would be
 * two reads of one answer that disagree for as long as a reload takes, and the row a person just
 * changed on Faces would read the old When on Tasks. So both doors read this, and a change made
 * through either is the same change.
 *
 * ## What is written, and through what
 *
 * A task's When is an ordinary setting (`when_key`, `tasks.<id>.when`) and goes through the ordinary
 * settings write. There is no second path to the stored value, which is how two screens come to
 * disagree about what is switched on. A stage's When that reads several tasks' is written the same
 * way, and the server writes the answer into each of them. The one write of its own is the press:
 * `POST /tasks/{id}/run`, now or at quiet hours, which queues work and leaves the When as it was.
 *
 * ## When it reads again
 *
 * On the bus, for as long as the tab is open, and only once something has asked: the queue moving is
 * when "last ran", "next" and "waiting" change, and a setting moving is when a When or quiet hours
 * did. Both bells ring often while a large scan runs, so a read that is already out is never joined
 * by a second one: the bell is remembered, and ONE more read follows the one in flight. That keeps
 * this to at most one request in the air however fast the queue moves, without dropping the last
 * change on the floor.
 *
 * ## The wire types
 *
 * Taken from the generated schema rather than written out beside it: a copy does not fail when the
 * server moves, it goes quietly wrong.
 */

import type { components } from '$lib/api/schema';
import { api, ApiError } from '$lib/api/client';
import { jobChanges, settingChanges } from '$lib/library/changes.svelte';
import { saveSettings } from '$lib/settings-ui/settings';
import { COPY } from '$lib/settings-ui/ScheduledTasks.search';
import { toasts } from '$lib/shell/toasts.svelte';
import { place, type ToastWords } from '$lib/components/common/toast-pieces';
import { clock } from '$lib/shell/clock.svelte';
import { clockTime, timeOfDay } from '$lib/shell/when';

/** Every task, with quiet hours at the top, exactly as the server describes them. */
export type TasksView = components['schemas']['Tasks'];
/** One task: its When, how its last run ended, when the next one is, how much is waiting. */
export type TaskView = components['schemas']['TaskView'];
/** What one press queued, and for Run during quiet hours when the range opens. */
export type TaskStarted = components['schemas']['TaskStarted'];

/** The two halves of the press: straight away, or when quiet hours open. */
export type At = 'now' | 'quiet';

/**
 * Part of a task, as its menu asks for it: some of its parts and some library folders by key
 * (absent is all of them), or its dry run. The server checks each against the task's own
 * declaration, the one the menu was drawn from.
 */
export type Only = Partial<Pick<components['schemas']['RunTask'], 'parts' | 'locations' | 'dry'>>;

/** The three stages a file goes through as it arrives, each one task, in the order they happen. */
export const STAGES = ['scan', 'generate', 'identify'] as const;

/** The stored value of the When that waits for quiet hours: `quiet_hours.WHEN_QUIET` on the server. */
const WHEN_QUIET = 'quiet';

/** Quiet hours' two ends, as the server holds them: `HH:MM` on this device's clock. */
type QuietRange = Pick<components['schemas']['QuietHoursView'], 'starts' | 'ends'>;

/**
 * One end of quiet hours in the reader's clock, the short way: "11 PM" on the twelve-hour clock
 * where the minutes are nought, "11:30 PM" where they are not, "23:00" on the twenty-four-hour
 * clock, whose hours are never written without their minutes.
 */
export function quietTime(value: string): string {
	const said = clockTime(value);
	return clock.hours === '12' ? said.replace(/:00(?!\d)/, '') : said;
}

/** Quiet hours as a range a person reads: "11 PM to 7 AM", or "23:00 to 07:00". */
export function quietRange(quiet: QuietRange): string {
	return COPY.quiet.range(quietTime(quiet.starts), quietTime(quiet.ends));
}

/**
 * What a When is called wherever it is drawn: the server's own words, and for the one that waits
 * for quiet hours the range after them, "During quiet hours (11 PM to 7 AM)".
 *
 * THE ONE PLACE THOSE WORDS ARE MADE. The server names the When and cannot name the range the way
 * this reader's clock writes it (the clock is each account's own choice under Appearance), so the
 * range is added here, from the setting the task list carries at its top, and every place that
 * draws a When (a task row's menu, the chooser on an owning pane, the Tasks screen's sentences)
 * reads it through this. Without quiet hours in hand the words are the server's alone.
 */
export function whenLabel(
	value: string,
	label: string,
	quiet: QuietRange | null | undefined
): string {
	return value === WHEN_QUIET && quiet ? `${label} (${quietRange(quiet)})` : label;
}

const TASKS = '/tasks';

class TaskList {
	/** The last answer, or null before the first one has come back. */
	view = $state<TasksView | null>(null);
	/** True when the list could not be read at all. Each door draws its own line about it. */
	failed = $state(false);
	/** Which half of which task's press is in flight, so one button is busy and the rest are not. */
	pressing = $state<Record<string, At>>({});

	/* Whether anything on screen has asked. A tab that never opens a screen drawing a task never
	   reads the list, and the bells below cost it nothing. */
	#asked = false;
	#inFlight: Promise<void> | null = null;
	#again = false;

	/** Every task, in the order the server declared them. Empty until the first answer. */
	get tasks(): TaskView[] {
		return this.view?.tasks ?? [];
	}

	/** What a When is called on screen, with quiet hours' range where it waits for them. */
	whenLabel(value: string, label: string): string {
		return whenLabel(value, label, this.view?.quiet_hours);
	}

	/** One task by its id, or undefined while the list is out or when no task has that id. */
	row(id: string): TaskView | undefined {
		return this.tasks.find((one) => one.id === id);
	}

	/** Whether this has been asked for. The bells read it: see the foot of this file. */
	get asked(): boolean {
		return this.#asked;
	}

	/** Read the list once. Safe to call from every door as it mounts; only the first one asks. */
	async ensure(): Promise<void> {
		if (this.#asked) return this.#inFlight ?? undefined;
		await this.load();
	}

	/**
	 * Read the list again. At most one request is ever out: a call while one is in flight asks for
	 * exactly one more read after it, rather than a second request racing the first.
	 */
	async load(): Promise<void> {
		this.#asked = true;
		if (this.#inFlight) {
			this.#again = true;
			return this.#inFlight;
		}
		this.#inFlight = (async () => {
			try {
				do {
					this.#again = false;
					try {
						this.view = await api.get<TasksView>(TASKS);
						this.failed = false;
					} catch {
						/* The last answer stays on screen. A dropped request says nothing about the
						   tasks, and blanking every row over it would hide the controls themselves. */
						this.failed = this.view === null;
					}
				} while (this.#again);
			} finally {
				this.#inFlight = null;
			}
		})();
		return this.#inFlight;
	}

	/**
	 * Change when a task runs. Shown immediately and written through the ordinary settings write; a
	 * refusal puts the old answer back and is handed to the caller to say.
	 */
	async setWhen(id: string, when: string): Promise<void> {
		const task = this.row(id);
		if (!task || task.when === when) return;
		const before = task.when;
		this.#patch(id, { when });
		try {
			await saveSettings({ [task.when_key]: when });
		} catch (failure) {
			this.#patch(id, { when: before });
			throw failure;
		} finally {
			// The When moves "next" and the cadence, which only the server can work out.
			void this.load();
		}
	}

	/**
	 * Run a task now, or when quiet hours open. A refusal is thrown for the caller to say: the
	 * server's sentence is written for a person ("Smart Search is turned off. Turn it on under
	 * Smart Search.").
	 */
	async run(id: string, at: At, only: Only = {}): Promise<TaskStarted> {
		this.pressing = { ...this.pressing, [id]: at };
		try {
			const started = await api.post<TaskStarted>(`/tasks/${id}/run`, {
				body: { at, ...only }
			});
			void this.load();
			return started;
		} finally {
			this.pressing = Object.fromEntries(
				Object.entries(this.pressing).filter(([pressed]) => pressed !== id)
			);
		}
	}

	#patch(id: string, change: Partial<TaskView>): void {
		if (!this.view) return;
		this.view = {
			...this.view,
			tasks: this.view.tasks.map((one) => (one.id === id ? { ...one, ...change } : one))
		};
	}
}

export const taskList = new TaskList();

/* The queue moved, or a setting did. Only once something has asked: a store nobody has opened has
   nothing to bring up to date, and asking would be a request on behalf of a screen nobody opened. */
jobChanges.subscribe(() => {
	if (taskList.asked) void taskList.load();
});
settingChanges.subscribe(() => {
	if (taskList.asked) void taskList.load();
});

/**
 * When a run held for quiet hours starts, as the row beside the press writes that time: the range's
 * own start ("6 AM") where the run waits for the range to open, the moment itself for any other.
 * A toast reading "6:00:00 AM" beside a row reading "6 AM" is one time said two ways.
 */
function startSaid(startsAt: number): string {
	const quiet = taskList.view?.quiet_hours;
	return quiet && quiet.opens_at === startsAt ? quietTime(quiet.starts) : timeOfDay(startsAt);
}

/** A lead that ends in Activity, the place to follow a started run, as a link. */
function inActivity(lead: string): ToastWords {
	return [`${lead} `, place('Activity', '/settings/tasks?show=now'), '.'];
}

/** What one press of a task comes to: the sentence after it, and whether anything was queued. */
interface Said {
	words: ToastWords;
	error: boolean;
	queued: boolean;
}

async function press(id: string, at: At, only: Only): Promise<Said | null> {
	if (taskList.pressing[id]) return null;
	const says = (words: ToastWords, queued: boolean): Said => ({ words, error: false, queued });
	try {
		const started = await taskList.run(id, at, only);
		const queued = started.job_ids.length > 0;
		if (started.dry) return says(COPY.when.dryStarted, queued);
		if (!queued) return says(COPY.when.nothingToDo, false);
		if (started.named) return says(inActivity(COPY.when.startedPart(started.named)), true);
		/* No hour for Run during quiet hours is the server saying the run it landed on is not waiting
		   for the range (it was already running, or a Run now had pulled it forward), so it
		   is the same answer as Run now's, and "Queued for quiet hours" over a walk under way
		   would be false. */
		if (at === 'now' || started.starts_at === null)
			return says(
				started.on_activity === false ? COPY.when.startedHere : inActivity(COPY.when.started),
				true
			);
		/* Whether it waits is the server's to say: its clock placed the run, and this browser's may
		   be a minute away from it, and would read a run starting now as one starting "at 5:08:27 AM". */
		if (started.waits) return says(COPY.when.startsAt(startSaid(started.starts_at)), true);
		return says(COPY.when.startsNow, true);
	} catch (error) {
		/* The server's sentence where it wrote one for a person ("Smart Search is turned off.
		   Turn it on under Smart Search."), and a plain one where the request never got that far. */
		const said = error instanceof ApiError && error.detail ? error.detail : COPY.when.cannotRun;
		return { words: said, error: true, queued: false };
	}
}

function show(said: Said): void {
	toasts.show(said.words, said.error ? { tone: 'error' } : {});
}

/**
 * Press a task's Run now or Run during quiet hours, and say what happened.
 *
 * THE ONE PRESS, wherever it is drawn: the task's row on Tasks and on its owning section, the empty
 * library's first scan, an Organize card's "Compare now". Each one making its own request with its
 * own sentence afterwards is how three buttons called "Scan now" come to do two different things.
 * So the request and the sentence live here, and a place that starts a task calls this.
 * Answers whether anything was queued, for a caller that wants to draw what follows.
 */
export async function pressTask(id: string, at: At, only: Only = {}): Promise<boolean> {
	const said = await press(id, at, only);
	if (said === null) return false;
	show(said);
	return said.queued;
}

/**
 * Several tasks pressed as ONE press (a pass that is a part of two tasks), with one sentence: the
 * first that queued something, else the first. A refusal is said as well, never hidden behind it.
 */
export async function pressTasks(
	presses: readonly { task: string; parts?: string[] }[],
	at: At
): Promise<boolean> {
	const all: Said[] = [];
	for (const one of presses) {
		const said = await press(one.task, at, one.parts ? { parts: one.parts } : {});
		if (said !== null) all.push(said);
	}
	all.filter((one) => one.error).forEach(show);
	const told = all.find((one) => one.queued) ?? all.find((one) => !one.error);
	if (told) show(told);
	return all.some((one) => one.queued);
}
