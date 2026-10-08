import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { checkColumns } from '$lib/components/common/DataRows.svelte';
import {
	ACTIVITY_ACTIONS,
	ACTIVITY_COLUMNS,
	chores,
	NOT_ENOUGH,
	NOTHING_WAITING,
	nowState,
	pile,
	passes,
	shownState,
	stepsLeft,
	stepsLine,
	SUB_TASK_OFF,
	WAITING_FOR_RUNTIME,
	type Job,
	type JobsPage,
	type Steps
} from './family';

/* ONE ROW PER FAMILY: what a folded row says about the work under it. */

let sequence = 0;

function job(over: Partial<Job> = {}): Job {
	sequence += 1;
	return {
		id: `job-${sequence}`,
		parent_id: null,
		// Where in the queue, for a job that is waiting; the server sends null once it has begun.
		position: null,
		type: 'download',
		name: 'Downloading',
		subject: 'clip.mp4',
		subject_id: null,
		state: 'done',
		progress: 1,
		attempts: 1,
		max_attempts: 3,
		error: null,
		note: null,
		run_after: null,
		created_at: sequence,
		updated_at: sequence,
		steps: null,
		waits_for_password: false,
		reason: null,
		...over
	};
}

function steps(over: Partial<Steps> = {}): Steps {
	return {
		count: 8,
		by_state: { done: 7, running: 1 },
		at_least: false,
		cap: 1000,
		state: 'running',
		subject: 'clip.mp4',
		subject_id: 'a1',
		failure: null,
		...over
	};
}

describe("a folded row's line", () => {
	it('counts the steps, and each state that has any, finished first', () => {
		expect(stepsLine(steps())).toBe('8 steps: 7 done, 1 running');
	});

	it('says every state with a step in it, in one fixed order whatever order they arrived in', () => {
		const line = stepsLine(
			steps({ count: 9, by_state: { queued: 2, failed: 1, done: 5, running: 1 }, state: 'failed' })
		);
		expect(line).toBe('9 steps: 5 done, 1 failed, 1 running, 2 queued');
	});

	it('says a count that stopped at the cap as a floor', () => {
		const line = stepsLine(
			steps({ count: 1000, by_state: { done: 1000, queued: 12 }, at_least: true, state: 'queued' })
		);
		expect(line).toBe('1,000+ steps: 1,000+ done, 12 queued');
	});

	it('says nothing about steps for a row that started none, rather than "0 steps"', () => {
		expect(stepsLine(steps({ count: 0, by_state: {} }))).toBeNull();
		expect(stepsLine(null)).toBeNull();
	});

	it('says one step as one', () => {
		expect(stepsLine(steps({ count: 1, by_state: { done: 1 }, state: 'done' }))).toBe(
			'1 step: 1 done'
		);
	});
});

describe('the one state a folded row shows', () => {
	/* FAILED WINS. A download reads done the moment its file lands; its steps can still fail. The
	   folded row shows the server's verdict over the whole family, never the top's own state, so
	   folding never hides a failure. */
	it("is the family's, so a failed step makes a finished download read failed", () => {
		const top = job({
			state: 'done',
			steps: steps({ by_state: { done: 7, failed: 1 }, state: 'failed' })
		});
		expect(shownState(top)).toBe('failed');
	});

	it('reads failed over running, while another step is still going', () => {
		const top = job({
			state: 'running',
			steps: steps({ by_state: { done: 6, failed: 1, running: 1 }, state: 'failed' })
		});
		expect(shownState(top)).toBe('failed');
	});

	it("is the row's own state where there is no family folded under it", () => {
		expect(shownState(job({ state: 'queued' }))).toBe('queued');
	});
});

describe('what a cancel on a folded row calls off', () => {
	it('counts the steps still to finish, and not the finished ones', () => {
		expect(stepsLeft(steps({ by_state: { done: 5, running: 1, queued: 2, blocked: 1 } }))).toBe(4);
		expect(stepsLeft(null)).toBe(0);
	});
});

describe("the Now tab's columns", () => {
	/* One declaration, read by both lists: a track sized by its content would stand the two lists'
	   columns at different x. */
	it('are sized by the column, never by what is in them', () => {
		expect(() => checkColumns(ACTIVITY_COLUMNS, ACTIVITY_ACTIONS)).not.toThrow();
		for (const column of ACTIVITY_COLUMNS) {
			expect(column.width, column.id).not.toMatch(/auto|content/);
		}
	});

	it('put words left and only the count right', () => {
		const right = ACTIVITY_COLUMNS.filter((column) => column.align === 'end').map((one) => one.id);
		expect(right).toEqual(['count']);
		// "Time left" is words ("Nothing waiting", "Change in Importing"): right-aligned, its left edge
		// would be ragged.
		expect(ACTIVITY_COLUMNS.find((column) => column.id === 'left')?.align ?? 'start').toBe('start');
	});

	it('give the status as much of the width as the name, and room for the benchmark pill whole', () => {
		const width = (id: string) => ACTIVITY_COLUMNS.find((one) => one.id === id)!.width;
		const share = (id: string) => Number(/(\d+(?:\.\d+)?)fr/.exec(width(id))![1]);
		expect(share('status')).toBeGreaterThanOrEqual(share('name'));
		// "Paused while Sift benchmarks this device" with its mark is 259px at the label font.
		expect(Number(/^minmax\((\d+)rem/.exec(width('status'))?.[1] ?? 0) * 16).toBeGreaterThan(259);
	});
});

/* ---------------------------------------------------------------------------------------------
 * THE LONG PASSES, and the two things the line must not say that are not true.
 * -------------------------------------------------------------------------------------------
 */

type Work = NonNullable<JobsPage['work']>[string];
type Family = NonNullable<JobsPage['families']>[string];

function kind(over: Partial<Work> = {}): Work {
	return {
		done: 0,
		outstanding: 0,
		failed: 0,
		waiting: null,
		left_units: 0,
		total: null,
		...over
	};
}

function page(
	families: Record<string, Partial<Family>>,
	work: Record<string, Work> = {}
): JobsPage {
	return {
		jobs: [],
		total: 0,
		counts: {},
		tallies: {},
		by_type: {},
		names: {},
		older: [],
		work,
		housekeeping: [],
		stepping_back: false,
		step_back_share: 25,
		step_back_for: null,
		step_back_over: [],
		turbo_mode: false,
		password_wanted: 0,
		paused: false,
		families: Object.fromEntries(
			Object.entries(families).map(([key, one]) => {
				// The server attributes these itself; a test that does not say them gets the same
				// sum over the family's types the client would make.
				const types = one.types ?? [];
				const outstanding = types.reduce((n, type) => n + (work[type]?.outstanding ?? 0), 0);
				const waiting = types.reduce(
					(n, type) =>
						n +
						(work[type]
							? (work[type].waiting ?? Math.ceil(work[type].left_units ?? work[type].outstanding))
							: 0),
					0
				);
				return [
					key,
					{
						label: key,
						types: [],
						paused: false,
						runs: [],
						eta_seconds: null,
						on: true,
						ready: true,
						problem: null,
						quick_seconds: null,
						slow_seconds: null,
						at_least: false,
						sample: 0,
						at_once: 12,
						outstanding,
						waiting,
						done: 0,
						total: 0,
						reason: null,
						parts: [],
						task: null,
						time_unknown: null,
						pace: null,
						for_task: null,
						failed: 0,
						last_error: null,
						running: 0,
						...one
					}
				];
			})
		)
	};
}

describe('the bar, which is done over what wants doing', () => {
	it('is full on a finished library rather than empty', () => {
		/* A library with nothing outstanding is not at 0 percent. With the denominator the size
		   of the RUN, a kind that is not busy has no run, so a library that was entirely
		   finished would divide nought by nought. Both numbers come from the library. */
		const [one] = passes(
			page(
				{ generate: { label: 'Generate', types: ['thumbnail'], done: 100000, total: 100000 } },
				{ thumbnail: kind({ waiting: 0, total: 100000 }) }
			)
		);

		expect(one.progress).toBe(100);
		expect(one.done).toBe('100,000 of 100,000');
		expect(one.now).toBe('Up to date');
		expect(one.when).toBe(NOTHING_WAITING);
	});

	it('says how much of the library has the work while a pass is part way through', () => {
		const [one] = passes(
			page(
				{
					identify: {
						label: 'Identify',
						types: ['face_scan'],
						done: 9000,
						total: 100000,
						running: 3
					}
				},
				{ face_scan: kind({ waiting: 91000, outstanding: 4, total: 100000 }) }
			)
		);

		expect(one.done).toBe('9,000 of 100,000');
		expect(Math.round(one.progress)).toBe(9);
		// The tasks running now, in the In progress chip's own word, never how many could.
		expect(one.now).toBe('3 in progress');
		expect(nowState(one.tone), 'drawn as the In progress chip').toBe('running');
	});

	it('says each kind of a two-kind pass in its own words, not one figure over both', () => {
		/* Identify is the faces pass AND the watermark read. A figure like "9,000 of 200,000"
		   would be the library counted once per kind, and a figure about neither. */
		const [one] = passes(
			page(
				{
					identify: {
						label: 'Identify',
						types: ['face_scan', 'watermark_read'],
						done: 9000,
						total: 200000,
						parts: [
							{
								type: 'face_scan',
								caption: 'files looked at for faces',
								on: true,
								paused: false,
								done: 9000,
								total: 100000
							},
							{
								type: 'watermark_read',
								caption: 'files read for watermarks',
								on: true,
								paused: false,
								done: 0,
								total: 100000
							}
						]
					}
				},
				{ face_scan: kind({ waiting: 91000, outstanding: 4, total: 100000 }) }
			)
		);

		// One sub-row per kind: its name, and its count in the count column.
		expect(one.parts.map((part) => [part.label, part.count])).toEqual([
			['Files looked at for faces', '9,000 of 100,000'],
			['Files read for watermarks', '0 of 100,000']
		]);
		expect(Math.round(one.parts[0].progress)).toBe(9);
	});

	it('says a sub-task switched off is off, and carries what Run now presses and a pause', () => {
		/* Fingerprint with music fingerprints off: drawn, counted in nothing the pass says. */
		const [one] = passes(
			page({
				fingerprint: {
					label: 'Fingerprint',
					types: ['fingerprint_file', 'audio_fingerprint'],
					done: 94242,
					total: 94242,
					paused: true,
					runs: [{ task: 'generate', parts: ['fingerprints'] }],
					parts: [
						{
							type: 'fingerprint_file',
							caption: 'files fingerprinted',
							on: true,
							paused: true,
							done: 94242,
							total: 94242
						},
						{
							type: 'audio_fingerprint',
							caption: 'files with a music fingerprint',
							on: false,
							paused: false,
							done: 0,
							total: 15533
						}
					]
				}
			})
		);

		expect(one.parts.map((part) => [part.count, part.off, part.paused])).toEqual([
			['94,242 of 94,242', false, true],
			[`0 of 15,533 \u00b7 ${SUB_TASK_OFF}`, true, false]
		]);
		expect(one.runs).toEqual([{ task: 'generate', parts: ['fingerprints'] }]);
		expect(one.paused).toBe(true);
	});

	it('draws its own bar and count beside its time left while it is in progress', () => {
		/* Identify of two kinds, running: the row itself shows motion, over the total its time
		   left is counted on, not only the sub-rows under the arrow. */
		const two = (outstanding: number) =>
			passes(
				page(
					{
						identify: {
							label: 'Identify',
							types: ['face_scan', 'watermark_read'],
							done: 9000,
							total: 9270,
							parts: [
								{
									type: 'face_scan',
									caption: 'faces',
									on: true,
									paused: false,
									done: 4500,
									total: 4635
								},
								{
									type: 'watermark_read',
									caption: 'marks',
									on: true,
									paused: false,
									done: 4500,
									total: 4635
								}
							]
						}
					},
					{ face_scan: kind({ waiting: 270, outstanding, total: 4635 }) }
				)
			)[0];
		expect([two(23).moving, two(23).done, two(23).parts.length]).toEqual([
			true,
			'9,000 of 9,270',
			2
		]);
		expect(two(0).moving).toBe(false);
		const markup = readFileSync('src/lib/jobs/JobsScreen.svelte', 'utf8');
		expect(markup.match(/line\.pass\.parts\.length === 0 \|\| line\.pass\.moving/g)).toHaveLength(
			2
		);
	});

	it('draws one line for a pass of one counted kind, as before', () => {
		const [one] = passes(
			page(
				{
					identify: {
						label: 'Identify',
						types: ['face_scan'],
						done: 9000,
						total: 100000,
						parts: [
							{
								type: 'face_scan',
								caption: 'files looked at for faces',
								on: true,
								paused: false,
								done: 9000,
								total: 100000
							}
						]
					}
				},
				{ face_scan: kind({ waiting: 91000, outstanding: 4, total: 100000 }) }
			)
		);

		expect(one.parts).toEqual([]);
		expect(one.done).toBe('9,000 of 100,000');
	});

	it('says up to date, once, when the server says nothing is waiting', () => {
		/* The server's "Nothing waiting" is the time-left column's sentence. Drawn in the Now
		   column as well, the row would read "Nothing waiting  Nothing waiting". */
		const [one] = passes(
			page(
				{
					scan: {
						label: 'Scan',
						types: ['probe'],
						done: 100,
						total: 100,
						reason: NOTHING_WAITING
					}
				},
				{ probe: kind({ waiting: 0, total: 100 }) }
			)
		);

		expect(one.now).toBe('Up to date');
		expect(one.when).toBe(NOTHING_WAITING);
	});

	it('says not started for work that lacks a job, rather than waiting', () => {
		/* Files lack the work and nothing is queued for it: nothing happens until somebody presses
		   Run now or a scan brings files. "Waiting" promised it would happen by itself. */
		const [one] = passes(
			page(
				{ identify: { label: 'Identify', types: ['face_scan'], done: 9000, total: 100000 } },
				{ face_scan: kind({ waiting: 91000, outstanding: 0, total: 100000 }) }
			)
		);

		expect(one.now).toBe('Not started');
	});

	it('takes the running count the server attributed rather than summing its own types', () => {
		/* A Build filtered to Smart Search runs as Identify's task type; summed by type, Identify
		   would read as running and Smart Search "Not started" while its own run was going. */
		const [one] = passes(
			page(
				{
					semantic: {
						label: 'Smart Search',
						types: ['semantic_describe'],
						done: 18262,
						total: 100000,
						at_once: 1,
						outstanding: 12,
						waiting: 83000
					}
				},
				{ semantic_describe: kind({ waiting: 83000, outstanding: 0, total: 100000 }) }
			)
		);

		expect(one.now).toBe('In progress');
	});

	it('never reports more done than the library holds', () => {
		const [one] = passes(
			page({ scan: { label: 'Scan', types: ['probe'], done: 500, total: 100 } }, {})
		);

		expect(one.done).toBe('100 of 100');
	});
});

describe('the estimate, as a range in words', () => {
	const priced = (over: Partial<Family>) =>
		passes(
			page(
				{
					identify: {
						label: 'Identify',
						types: ['face_scan'],
						done: 9000,
						total: 100000,
						...over
					}
				},
				{ face_scan: kind({ waiting: 91000, outstanding: 4, total: 100000 }) }
			)
		)[0];

	it('quotes both ends of what the server measured', () => {
		expect(priced({ quick_seconds: 64800, slow_seconds: 93600 }).when).toBe('About 18 to 27 hours');
	});

	it('says the total is not known while a folder waits to be counted, never a floor', () => {
		const unknown = 'Not known until every folder is counted.';
		const row = priced({ quick_seconds: null, slow_seconds: null, time_unknown: unknown });
		expect(row.when).toBe(unknown);
		expect(row.now).toBe('In progress');
	});

	it('does not call a pass up to date while files may still be coming to it', () => {
		const unknown = 'Not known until every folder is counted.';
		const [row] = passes(page({ generate: { label: 'Generate', time_unknown: unknown } }));
		expect([row.now, row.when]).toEqual(['Not started', unknown]);
		const [said] = passes(page({ semantic: { reason: NOTHING_WAITING, time_unknown: unknown } }));
		expect([said.now, said.tone]).toEqual(['Not started', 'plain']);
	});

	it('says a time priced from the benchmark as the least it takes', () => {
		const floor = { quick_seconds: 7.5 * 3600, slow_seconds: 7.5 * 3600, at_least: true };
		expect(priced(floor).when).toBe('At least 6 hours');
		expect(priced({ ...floor, quick_seconds: 200, slow_seconds: 200 }).when).toBe(NOT_ENOUGH);
		const unknown = 'Not known until every folder is counted.';
		expect(priced({ ...floor, time_unknown: unknown }).when).toBe(unknown);
	});

	it('says what sets the pace after the time left', () => {
		const pace = 'Reading is limited by the network share that holds Films.';
		expect(priced({ quick_seconds: 64800, slow_seconds: 93600, pace }).when).toBe(
			`About 18 to 27 hours. ${pace}`
		);
	});

	it("says what waits for its task after the arriving files' time, never in it", () => {
		const for_task = '2,691 more wait for their task.';
		expect(priced({ quick_seconds: 65, slow_seconds: 89, for_task }).when).toBe(
			`A few minutes. ${for_task}`
		);
		expect(priced({ quick_seconds: null, slow_seconds: null, for_task }).when).toBe(
			`${NOT_ENOUGH}. ${for_task}`
		);
	});

	it('says it cannot say rather than guessing from too small a sample', () => {
		/* The number is not the single newest run of the family with no minimum sample: a run of
		   ONE arriving file would set the price of a hundred thousand, and an unchanging backlog
		   would read days apart from one hour to the next. The server sends nothing at all when the sample is under twenty
		   items, and this is what the column says instead. */
		expect(priced({ quick_seconds: null, slow_seconds: null }).when).toBe(NOT_ENOUGH);
	});
});

describe('a pass whose last run failed', () => {
	const why = "A folder stopped answering partway through the scan. Scan it again once it's back.";
	const scan = (over: Partial<Family>) =>
		passes(
			page({
				scan: {
					label: 'Scan',
					reason: NOTHING_WAITING,
					done: 10,
					total: 10,
					failed: 2,
					last_error: why,
					...over
				}
			})
		)[0];

	it('says so on its own row with why, never up to date', () => {
		expect([scan({}).now, scan({}).tone, scan({}).why]).toEqual(['Failed 2 times', 'warn', why]);
	});

	it('says the later run while one is going', () => {
		expect([scan({ outstanding: 1 }).now, scan({ outstanding: 1 }).why]).toEqual([
			'In progress',
			null
		]);
	});

	it('is up to date once no failure stands', () => {
		const row = scan({ failed: 0, last_error: null });
		expect([row.now, row.tone, row.why]).toEqual(['Up to date', 'good', null]);
	});
});

describe('a pass whose work cannot run', () => {
	const library = () =>
		page(
			{
				identify: {
					label: 'Identify',
					types: ['face_scan'],
					quick_seconds: 64800,
					slow_seconds: 93600,
					done: 9000,
					total: 100000
				},
				semantic: { label: 'Smart Search', types: ['semantic_describe'], total: 100000 }
			},
			{ face_scan: kind({ waiting: 92169 }), semantic_describe: kind({ waiting: 83817 }) }
		);

	const held = (over: Record<string, Partial<Family>>) => {
		const built = library();
		for (const [key, one] of Object.entries(over)) {
			built.families = { ...built.families, [key]: { ...built.families![key], ...one } };
		}
		return built;
	};

	it('says what it is waiting for instead of a time it will never take', () => {
		/* The ledger's pace has memory, so a pass that cannot start still has one: "Identify, 14
		   days, 91,000 to do" would be said with the card's runtime not installed, every scan failing. */
		const [identify, meaning] = passes(
			held({
				identify: { ready: false, problem: 'The graphics card runtime is not installed.' },
				semantic: { ready: false, problem: null }
			})
		);

		expect(identify.when).toBe('The graphics card runtime is not installed.');
		expect(meaning.when).toBe(WAITING_FOR_RUNTIME);
		expect(identify.now).toBe("Can't run yet");
		expect(identify.allowed).toBe(false);
	});

	it('says a pass is switched off before it says anything about a runtime', () => {
		/* Somebody who turned a pass off is owed that answer, not a sentence about model files.
		   Both are false here on purpose: the switch has to win. */
		const [identify] = passes(
			held({ identify: { on: false, ready: false, problem: 'No runtime.' } })
		);

		expect(identify.now).toBe('Turned off');
		expect(identify.when).toBe('Turn on in Import tasks');
		expect(identify.settingLink, 'the way back on is a pointer, not a sentence').toBe(true);
		expect(identify.allowed).toBe(false);
	});

	it('says the night window in the server sentence and still prices the work', () => {
		const [identify] = passes(held({ identify: { reason: "Waiting for tonight's window." } }));

		expect(identify.now).toBe("Waiting for tonight's window");
		expect(identify.tone).toBe('warn');
		// The estimate is still worth reading: it is what somebody deciding to wait needs.
		expect(identify.when).toBe('About 18 to 27 hours');
	});

	it('says a pass whose queued work waits for quiet hours is waiting, not running', () => {
		/* Generate set to "In quiet hours" and a file arriving in the afternoon: three jobs queued,
		   none running, and the bar must not read as running in the running colour. */
		const [identify] = passes(
			held({ identify: { outstanding: 3, waiting: 3, reason: 'Waiting for quiet hours.' } })
		);

		expect(identify.now).toBe('Waiting for quiet hours');
		expect(identify.tone).toBe('warn');
	});

	it('leaves a pass alone when the server says nothing about it', () => {
		const [identify] = passes(library());

		expect(identify.when).toBe('About 18 to 27 hours');
		expect(identify.allowed).toBe(true);
	});
});

/* Nothing is started from Activity: each bar links to the row on Tasks where its work is run, and
 * which row is the server's answer (`task` on the wire), never a map kept here. */
describe('where a pass is run', () => {
	it('names the task the server sent', () => {
		const [scan] = passes(page({ scan: { label: 'Scan', types: ['probe'], task: 'scan' } }, {}));

		expect(scan.task).toBe('scan');
	});

	it('names none for a pass that is more than one task', () => {
		const [identify] = passes(page({ identify: { label: 'Identify', types: ['face_scan'] } }, {}));

		expect(identify.task, 'Identify is Faces and Watermarks, so no one row').toBe(null);
	});
});

describe('the housekeeping', () => {
	const chore = (over: Partial<NonNullable<JobsPage['housekeeping']>[number]> = {}) => ({
		job_type: 'shoots_look',
		label: 'Shoots',
		running: 0,
		outstanding: 0,
		failed: 0,
		quick_seconds: null,
		slow_seconds: null,
		last_started_at: null,
		last_seconds: null,
		last_state: null,
		last_error: null,
		last_job: null,
		task: null,
		reason: null,
		...over
	});

	it('draws the work that is not a pass as well', () => {
		/* THE HOUSEKEEPING IS ON THE SCREEN, beside the five long passes: a folder pass that
		   never succeeds and a duplicate sweep can take most of a machine's job time, and the
		   screen that answers "what is Sift doing" must say so. */
		const drawn = chores(
			{
				...page({}, {}),
				housekeeping: [chore({ failed: 3, last_started_at: 900, last_state: 'failed' })]
			},
			1000
		);

		expect(drawn).toHaveLength(1);
		expect(drawn[0].title).toBe('Shoots');
		expect(drawn[0].now).toBe('Failed 3 times');
		expect(drawn[0].tone).toBe('warn');
		expect(drawn[0].last).toBe('2 minutes ago, failed');
	});

	it('says when a chore last ran and how long it took', () => {
		const drawn = chores(
			{
				...page({}, {}),
				housekeeping: [
					chore({
						job_type: 'dedup_scan',
						label: 'Near duplicates',
						last_started_at: 0,
						last_seconds: 180,
						last_state: 'done',
						task: 'duplicates'
					})
				]
			},
			3 * 60 * 60
		);

		expect(drawn[0].now).toBe('Up to date');
		expect(drawn[0].last).toBe('3 hours ago, 3 min');
		expect(drawn[0].task, 'the row on Tasks it links to').toBe('duplicates');
	});

	it('starts a last run with a capital, as Never and Started beside it do', () => {
		// A local NOON as now: the words past a day are CALENDAR days in the reader's zone, so a
		// base at the epoch (00:16 UTC) would put 26 hours back on a different day in one zone and
		// not another, and two machines in different zones would disagree.
		const noon = new Date(2026, 0, 15, 12, 0, 0).getTime() / 1000;
		const at = (last_started_at: number, last_state: string) =>
			chores(
				{
					...page({}, {}),
					housekeeping: [chore({ last_started_at, last_seconds: 20, last_state })]
				},
				noon
			)[0].last;

		expect(at(noon, 'done')).toBe('Just now, under a minute');
		expect(at(noon - 26 * 60 * 60, 'failed')).toBe('Yesterday, failed');
	});

	/* "Yesterday, failed" alone says nothing of what failed or where to look. The row carries its
	   job row's own first line, and only for a run that failed. */
	it('carries why the last run failed, and nothing for a run that did not', () => {
		const why = (last_state: string, last_error: string | null) =>
			chores(
				{
					...page({}, {}),
					housekeeping: [chore({ last_started_at: 900, last_state, last_error })]
				},
				1000
			)[0].why;

		expect(why('failed', 'Invalid data found when processing input')).toBe(
			'Invalid data found when processing input'
		);
		expect(why('done', 'left over from an earlier try')).toBeNull();
		expect(why('failed', null)).toBeNull();
	});

	it('says a chore is waiting for quiet hours when the server says so, as a held pass does', () => {
		/* Find duplicate files set to "In quiet hours": the sweep a new file queued must read as
		   waiting for quiet hours in the afternoon, not "Waiting, about 1 to 2 min", under
		   Generate held the same way and saying so. */
		const held = chores(
			{
				...page({}, {}),
				housekeeping: [
					chore({
						job_type: 'dedup_scan',
						label: 'Near duplicates',
						outstanding: 1,
						reason: 'Waiting for quiet hours.'
					})
				]
			},
			1000
		);
		expect(held[0].now).toBe('Waiting for quiet hours');
		expect(held[0].tone).toBe('warn');

		const going = chores(
			{
				...page({}, {}),
				housekeeping: [chore({ outstanding: 1, running: 1, reason: 'Waiting for quiet hours.' })]
			},
			1000
		);
		expect(going[0].now, 'running is in progress, whatever the sentence').toBe('In progress');
		expect(nowState(going[0].tone)).toBe('running');
		const several = chores(
			{ ...page({}, {}), housekeeping: [chore({ outstanding: 3, running: 2 })] },
			1000
		);
		expect(several[0].now).toBe('2 in progress');
		const plain = chores({ ...page({}, {}), housekeeping: [chore({ outstanding: 1 })] }, 1000);
		expect(plain[0].now).toBe('Waiting');
	});

	it("says a running swap is waiting for them rather than a time, then its own rate's time", () => {
		/* A swap's line must not say "about 3 minutes" before anybody has joined, a time taken from
		   the swaps before it. Waiting on a person, the server sends its words and no time; once the
		   files move, its own rate's time and no words. */
		const swaps = (over: Partial<NonNullable<JobsPage['housekeeping']>[number]>) =>
			chores(
				{
					...page({}, {}),
					housekeeping: [
						chore({ job_type: 'swap_session', label: 'Swaps', outstanding: 1, running: 1, ...over })
					]
				},
				1000
			)[0];
		const waiting = swaps({ reason: 'Waiting for them' });
		expect(waiting.when).toBe('Waiting for them');
		expect(waiting.now).toBe('In progress');
		const moving = swaps({ quick_seconds: 120, slow_seconds: 120 });
		expect(moving.when).not.toBe('Waiting for them');
		expect(moving.when).toBe('A few minutes');
	});

	it('says never for a chore with no record of a run', () => {
		const drawn = chores({ ...page({}, {}), housekeeping: [chore()] }, 1000);

		expect(drawn[0].last).toBe('Never');
		expect(drawn[0].when).toBe(NOTHING_WAITING);
	});
});

describe('a pile on Options', () => {
	it('names the tasks the list shows and the rows it acts on, where they differ', () => {
		expect(pile(101, 77, 'failed')).toBe('77 failed, 101 with their steps');
		expect(pile(12, 12, 'failed')).toBe('12 failed');
		expect(pile(40, 5)).toBe('5, 40 with their steps');
		expect(pile(3, undefined, 'canceled')).toBe('3 canceled');
	});
});
