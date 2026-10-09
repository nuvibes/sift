// SPDX-License-Identifier: AGPL-3.0-or-later
/* The Performance pane's words, and what somebody can type to find them. */
import type { Searchable } from './search';
import { sayDuration } from '$lib/shell/duration';
import { counted } from '$lib/entity/entity-counts';

/* The two hand-drawn blocks' addresses, so a search result and a link can ring them the way a
   setting's row is rung. */
export const MEASURE_ANCHOR = 'performance.measure';
export const KEEPING_UP_ANCHOR = 'performance.keeping_up';
/* The benchmark's own row (its Run press), where a toast about the run Sift started by itself
   lands: the row is rung on arrival, and the results are drawn under it. */
export const MEASURE_ROW = 'performance.benchmark';
/* The Concurrency row, whose Edit opens the page of numbers for how much Sift does at the same time. */
export const CONCURRENCY_ANCHOR = 'performance.concurrency';

/* What a run is doing past its encoding rounds, by the server's name for each step. */
const NOW: Record<string, string> = {
	encoding: 'one more round, between the two quickest',
	decoder: 'timing how quickly this device decodes video and seeks into a file',
	storage: 'reading a few large files from each drive and share',
	previews: 'building previews on your GPU',
	models: 'timing each installed model',
	together: 'running everything together'
};

export const COPY = {
	lede: 'What this device is, how much Sift does on it at the same time, and how Sift is doing on it.',
	hardware: {
		thisDevice: 'This device',
		sifts: 'Device running Sift',
		copy: 'Copy',
		copied: 'Copied',
		copyDetails: "Copy this device's details",
		cpu: 'CPU',
		threads: 'Threads',
		memory: 'Memory',
		gpu: 'GPU',
		alsoInstalled: 'Also installed',
		unnamedGpu: "A GPU that doesn't say which",
		usesThisOne: ' (Sift uses this one)',
		unnamedPresent: "Present, and it doesn't say which",
		noGpu: 'None Sift can reach',
		driver: 'Driver',
		encoders: 'Video encoders',
		noEncoders: 'None \u2014 converting runs on the CPU',
		unknown: 'Unknown',
		elsewhere:
			'Sift is showing a library on another device, so nothing here does the work. This device only plays the video and shows this window.',
		faces: 'Recognizing faces',
		smartSearch: 'Smart Search',
		converting: 'Converting video'
	},
	measure: {
		name: 'Benchmarking this device',
		row: 'Benchmark this device',
		help: 'Find out what this device can do, and get suggested numbers for how many things Sift does at the same time.',
		lede: 'Sift estimates how much to do at the same time from your CPU. The benchmark measures it instead. It encodes the same short clip one at a time, then several at the same time. It times how fast this device decodes video and seeks into a file. It builds previews on your GPU the way Sift does, and times each installed model. Then it reads a few large files from each drive and network share your library is on. Each step is timed once, and a step stops early rather than run past its share of the time.',
		/* How long, from the server's figure: this device's last run where it has one, else the limit. */
		about: (seconds = 0, timed = false): string =>
			seconds ? `${COPY.measure.lede} ${COPY.measure.takes(seconds, timed)}` : COPY.measure.lede,
		takes: (seconds: number, timed: boolean) =>
			timed
				? `On this device it takes about ${sayDuration(seconds) ?? 'a moment'}, and keeps it busy while it runs.`
				: `It takes up to ${sayDuration(Math.ceil(seconds / 60) * 60) ?? 'a moment'} and keeps this device busy while it runs.`,
		left: (seconds?: number | null, timed = false) =>
			seconds == null
				? ''
				: seconds < 5
					? 'Almost done.'
					: `${timed ? 'About' : 'Up to'} ${sayDuration(timed ? seconds : Math.ceil(seconds / 60) * 60) ?? 'a moment'} left.`,
		busy: 'Benchmarking\u2026 this device is busy until it finishes.',
		round: (at: number, of: number) =>
			`Round ${at} of up to ${of}. Each one encodes more clips at the same time than the last, then one steps back between the two quickest. It stops early if this device stops keeping up.`,
		roundsDone: (done: number, of: number, step: string | null) =>
			`Encoding rounds done: ${done} of up to ${of}.${step && step in NOW ? ` Now ${NOW[step]}.` : ''}`,
		run: 'Run the benchmark',
		again: 'Run it again',
		unmeasured:
			'Adding your first folder benchmarks this device. Until it has been benchmarked, Generate and Identify read a file on a network share by seeking into it. The quicker way there, one decode of the whole file, needs the benchmark first.',
		/* Over the rows Sift's own run set; what it only suggested stands apart, with Apply. */
		setBySift: (n: number) =>
			`Sift ran this benchmark by itself and set ${n === 1 ? 'this number' : 'these numbers'} from it. History has the line, with Undo.`,
		notSet: (n: number) => `Sift didn't set ${n === 1 ? 'this number' : 'these numbers'}.`,
		wholeToCome: (byItself: boolean) =>
			`This is a first measure, taken so Sift could start importing your first folder's files. ${byItself ? "The full benchmark runs by itself once Sift has nothing else to do and nobody's using this device. Press Run it again to run it now." : "The full benchmark hasn't run yet. Press Run it again to run it."}`,
		/* The two presses on the toasts about that run: the row while it runs or after a
		   failure, the row and its results once it set something. */
		open: 'Open',
		review: 'Review',
		failed: (why: string) =>
			`The benchmark didn't finish \u2014 ${why}. Your settings are unchanged.`,
		automatic: 'automatic',
		applying: 'Applying\u2026',
		apply: (n: number) => (n === 1 ? 'Apply this number' : `Apply these ${n} numbers`),
		nothingYet:
			'Nothing is changed until you press that. You can change any of them later under Import tasks.',
		agrees: 'Your settings already match what this device can do. Nothing to change.',
		notEnough: 'The benchmark finished without enough results. Running it again usually helps.',
		decode: (fps: number, ms: number, share: boolean) =>
			`Decodes about ${counted(fps)} frames a second at 720p, and one seek into a file costs about ${ms} ms.${share ? " On a network share, Generate and Identify weigh these against the share's speed to choose how to read each file." : ''}`,
		shares: 'Your drives and shares',
		shareReads: (n: number) =>
			`Sift opens ${counted(n)} ${n === 1 ? 'file' : 'files'} at the same time from every share. The marked line is where each share stopped getting faster. On a share, reading is the slow part. Sift imports files at the pace the share can serve, however busy the rest of this device is.`,
		notMeasured: (why: string) => `Not measured \u2014 ${why}.`,
		level: (n: number, mbps: number) => `${n} at a time: ${mbps} MB/s`,
		quickest: (n: number) => `Quickest at ${n} at a time.`,
		folders: (names: string) => `Library folders on it: ${names}.`,
		readingAt: (n: number) =>
			` Files read per share is set to ${n}, so ${n} are read at a time here.`,
		gpu: 'Previews on your GPU',
		previews: (n: number, each: number) =>
			`${n} at the same time: ${each.toFixed(2)} previews a second`,
		models: 'Installed models',
		modelOn: (name: string, device: string) => `${name} on the ${device === 'cpu' ? 'CPU' : 'GPU'}`,
		files: (n: number, each: number) => `${n} at the same time: ${each.toFixed(2)} files a second`,
		perFile: (seconds: number, memory?: number | null, gpu?: number | null) =>
			`About ${seconds.toFixed(1)} seconds of one task for each file.${memory ? ` Loading it took ${counted(memory)} MB of memory${gpu ? ` and ${counted(gpu)} MB on the GPU` : ''}.` : ''}`,
		cannotStart: "Couldn't start the benchmark.",
		stopped: 'The benchmark stopped responding.',
		cannotSave: "Couldn't save those numbers."
	},
	pause: {
		tiny: 'under a tenth of a second',
		seconds: (s: string) => `${s} seconds`
	},
	/* ONE BLOCK, ONE QUESTION. A person asking "is Sift slow" should not have to read four
	   tables to find out. */
	keepingUp: {
		name: 'Is Sift keeping up?',
		searchHelp:
			"Whether Sift has stopped responding or had to wait since it started, and whether it's waiting right now.",
		yes: 'Yes. Sift has answered straight away since it started.',
		times: (n: number) => `${n.toLocaleString()} ${n === 1 ? 'time' : 'times'}`,
		measuring: (times: string) =>
			`Not counted: ${times} the benchmark pushed Sift until it fell behind, which is how it measures.`,
		/* The worst of the four since Sift started, in the words of the thing that was slow. */
		was: {
			loop: (times: string, pause: string) =>
				`Not always. Since it started, Sift stopped responding ${times}; the longest pause was ${pause}.`,
			threads: (times: string, pause: string) =>
				`Not always. Since Sift started, video and file work waited for a free worker ${times}; the longest wait was ${pause}.`,
			database: (times: string, pause: string) =>
				`Not always. Since Sift started, screens waited for the database ${times}; the longest wait was ${pause}.`,
			queue: (times: string, pause: string) =>
				`Not always. Since Sift started, it fell behind with its work ${times}; the longest it was behind was ${pause}.`
		},
		/* Happening at this moment, which outranks anything in the past. */
		now: {
			threads: (pause: string) =>
				`Not right now. Video and file work has been waiting ${pause} for a free worker, so video may stutter.`,
			database: (pause: string) =>
				`Not right now. Screens have been waiting ${pause} for the database, so every screen is slow.`,
			queue: (pause: string) =>
				`Not right now. Sift is ${pause} behind with its work, so screens are slow to load.`
		},
		/* What doing less at the same time would change, said once, under the verdict. */
		busyHelp:
			'Something heavy is running, such as a scan or face recognition. To free this device, lower the numbers under Concurrency.',
		details: 'The readings behind this answer',
		what: 'What',
		longest: 'Longest',
		timesHeading: 'Times',
		rightNow: 'Right now',
		clear: 'clear',
		loop: 'Sift itself',
		threads: 'Video and file work',
		database: 'Screens reading the database',
		queue: 'Work waiting for Sift',
		explain:
			'Sift itself: the longest time the whole app stopped responding. Video and file work: how long a task waited for a free worker. Screens reading the database: how long a screen waited for a free database connection. Work waiting for Sift: how long Sift took to get through the work already waiting for it. Times counts each wait of a quarter of a second or more since Sift started.',
		sweeping: (what: string) =>
			`Reading the whole library right now: ${what}. It uses one database connection of its own and stops when it's done.`,
		slowLookups: (microseconds: string) =>
			`When Sift started, one database lookup on this device took ${microseconds} microseconds, which is what a database on a network drive costs. Moving the data folder to a local disk will help.`,
		connections: (n: number) =>
			`Screens share ${n} database ${n === 1 ? 'connection' : 'connections'}, set by Tasks at the same time under Concurrency.`
	},
	/* How much Sift does at the same time: one row on the pane, and a page of its own behind its Edit. */
	much: {
		name: 'Concurrency',
		help: 'How many things Sift works on at the same time, and how much of this device face recognition may use.',
		edit: 'Edit',
		editLabel: 'Edit concurrency',
		atOnce: 'Background work',
		faces: 'Recognizing faces',
		facesHelp:
			'How much of this device face recognition may use, and when. Smart Search shares the tasks above.',
		quietHours: 'Quiet hours are set on Tasks, and apply to every task.',
		quietHoursLink: 'Choose quiet hours'
	},
	/* The two tables whose only use is diagnosis, behind the same fold, with a Copy button: they
	   are what somebody pastes into a bug report, not what anybody reads to decide anything. */
	report: {
		name: 'For a bug report',
		lede: 'What Sift spent its time on and how much each screen read, since it started. Copy these into a bug report.',
		copy: 'Copy',
		copied: 'Copied',
		copyLabel: 'Copy the figures for a bug report',
		work: 'Work',
		times: 'Times',
		total: 'Total',
		slowest: 'Slowest',
		read: 'Read',
		most: 'Most rows in one read'
	}
} as const;

export const SEARCHABLE: Searchable[] = [
	{
		name: COPY.measure.name,
		section: 'performance',
		key: MEASURE_ANCHOR,
		help: COPY.measure.help,
		keywords:
			'self-test self test benchmark measure measuring tune speed cpu encode decode network share'
	},
	{
		name: COPY.keepingUp.name,
		section: 'performance',
		key: KEEPING_UP_ANCHOR,
		help: COPY.keepingUp.searchHelp,
		keywords: 'slow stutter freeze pause health responsive lag queue database workers keeping up'
	},
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: COPY.hardware.thisDevice,
		section: 'performance',
		keywords: 'this device hardware cpu memory ram computer'
	},
	{
		name: COPY.measure.row,
		key: MEASURE_ROW,
		section: 'performance',
		keywords: 'benchmark measure speed test this device disk'
	},
	{
		name: COPY.measure.gpu,
		section: 'performance',
		keywords: 'gpu graphics card nvenc encoder previews benchmark'
	},
	{
		name: COPY.measure.models,
		section: 'performance',
		keywords: 'models faces smart search watermarks recognition benchmark memory'
	},
	{
		name: COPY.much.name,
		key: CONCURRENCY_ANCHOR,
		section: 'performance',
		help: COPY.much.help,
		keywords:
			'concurrency how much at the same time tasks workers previews scans network share reads faces budget share threads gentler faster'
	},
	{
		name: COPY.much.atOnce,
		section: 'performance',
		keywords: 'background work at the same time workers threads cpu'
	},
	{
		name: COPY.much.faces,
		section: 'performance',
		keywords: 'recognizing faces at the same time workers speed'
	}
];
