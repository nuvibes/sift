<script lang="ts">
	/* Performance: this device, and how Sift is doing on it.
	 *
	 * What the device is, the benchmark that suggests how much Sift should do at once, whether Sift
	 * is keeping up, and the figures for a bug report. The numbers themselves (how much at once,
	 * how much of the device face recognition may use) are Concurrency's page, at the foot beside
	 * the step back; the benchmark's Apply writes them through the ordinary settings save.
	 */
	import { onMount } from 'svelte';
	import { fetchSettings, saveSettings } from '$lib/settings-ui/settings';
	import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import GraphicsCard from './GraphicsCard.svelte';
	import GraphicsCardRemove from './GraphicsCardRemove.svelte';
	import { GraphicsCardState } from './graphics-card-state.svelte';
	import { paneHeld } from './read-only';
	import { fetchHealth, type ServerHealth } from '$lib/shell/health';
	import { api } from '$lib/api/client';
	import { bridge, type LocalMachine } from '$lib/bridge';
	import type { components } from '$lib/api/schema';
	import {
		fetchSelfTest,
		startSelfTest,
		POLL_MS,
		type Recommendation,
		type SelfTest
	} from '$lib/shell/selftest';
	import { FACES_DEVICE_KEY } from '$lib/people/faces.svelte';
	import { SEMANTIC_DEVICE_KEY } from '$lib/search/semantic.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Button from '$lib/components/common/Button.svelte';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import ProgressBar from '$lib/components/common/ProgressBar.svelte';
	import Problem from '$lib/components/common/Problem.svelte';
	import Empty from '$lib/components/common/Empty.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { copyText } from '$lib/shell/clipboard';
	import { COPY, KEEPING_UP_ANCHOR, MEASURE_ANCHOR } from './Performance.search';
	import Concurrency from './Concurrency.svelte';
	import SectionHeading from '$lib/components/common/SectionHeading.svelte';
	import Fold from '$lib/components/common/Fold.svelte';

	/* The graphics card, read once for the block that describes it and the row that deletes it. */
	const gpu = new GraphicsCardState();
	gpu.follow();
	/* Read only on a phone (`read-only.ts`): the rows hold themselves; the one press outside a row
	   asks the same pane. */
	const held = paneHeld();

	/* How long the tick stays before the button goes back to offering the copy. */
	const COPIED_FOR = 2000;

	/* Above this, a pause is long enough that somebody watching a video would see it. The server
	   uses the same figure to decide what to write in its log, and the two should agree: a screen
	   calling something fine while the log calls it a problem is worse than either alone. The same
	   number covers both readings below, because to whoever is watching the video the two feel
	   identical: what differs is which one is happening. */
	/* Above this many rows in one read, the number is drawn as a problem. Matches the server's
	   own WIDE_READ_ROWS, which is what decides whether the reading is escalated in the log. */
	const WIDE_READ_ROWS = 500;
	const NOTICEABLE_SECONDS = 0.25;

	/* The value of every setting there is, by key, for the two the readout needs: what face
	   recognition and Smart Search run on. This screen draws no settings of its own. */
	let elsewhere = $state(new Map<string, unknown>());
	let health = $state<ServerHealth | null>(null);

	/* What this device turned out to be, from the check the server runs once at startup.
	 *
	 * Its own admin-only route rather than a wider /health: that one can be reached by whoever
	 * can reach the port, and the make of a processor and the name of a graphics card are exactly
	 * the detail a stranger would use to tell one machine from another.
	 */
	type Hardware = components['schemas']['HardwareView'];
	let hardware = $state<Hardware | null>(null);

	/* THE OTHER COMPUTER, when there is one.
	 *
	 * Everything Sift does with a machine happens where the library is, so the block below
	 * describes the SERVER, and in client mode that is a computer somewhere else, which a
	 * heading saying "This machine" would name wrongly.
	 *
	 * Null except in client mode: on the machine running the library the block below already
	 * describes it, and in a browser there is no shell to ask. So the second block appears
	 * exactly when there is a second computer, and this screen needs no mode flag of its own.
	 */
	let here = $state<LocalMachine | null>(null);

	/* WHOSE MACHINE THE FIRST BLOCK IS ABOUT.
	 *
	 * "This machine" is only ever true of the desktop application running the library itself. In
	 * client mode it is a computer somewhere else, and in a BROWSER there is no machine being
	 * spoken about at all: a browser is not a computer, and a page pointed at a server across the
	 * network calling it "this machine" is naming the wrong thing twice over. */
	const serverBlockName = $derived(
		here === null && bridge.isDesktop() ? COPY.hardware.thisDevice : COPY.hardware.sifts
	);

	/** Bytes as something a person reads. Whole gigabytes: nobody tunes a machine to a decimal. */
	let machineEl = $state<HTMLElement | null>(null);
	let copied = $state(false);

	/*
	 * The table as words, read off the table itself.
	 *
	 * Built from the same fields a second time it would be free to drift: a row added above and
	 * forgotten here, and the copy quietly stops mentioning it. What is on screen is the list.
	 */
	async function copyMachine(): Promise<void> {
		const lines = [...(machineEl?.querySelectorAll('.readings > div') ?? [])].map((row) => {
			const name = row.querySelector('dt')?.textContent?.trim() ?? '';
			const value = (row.querySelector('dd')?.textContent ?? '').replace(/\s+/g, ' ').trim();
			return `${name}: ${value}`;
		});
		copied = await copyText(lines.join('\n'));
		if (copied) setTimeout(() => (copied = false), COPIED_FOR);
	}

	function readableMemory(bytes: number | null): string {
		if (!bytes) return COPY.hardware.unknown;
		return `${Math.round(bytes / 1024 / 1024 / 1024)} GB`;
	}

	/* WHAT THE MACHINE HAS, not what the operating system can address.
	 *
	 * They are different numbers: what the operating system reports as physical memory is the
	 * installed total less whatever the firmware and the hardware reserve, a few gigabytes. Shown
	 * here, the addressable figure would stand beside a Windows dialog naming the installed one,
	 * and read as Sift being unable to count.
	 *
	 * Only the installed figure is SHOWN. The addressable one is still what every sizing decision
	 * is made against (it is the memory that actually exists to be spent), but a description of
	 * somebody's computer should say what is in it and stop there. Two numbers in one row would
	 * invite the question rather than answer it.
	 *
	 * Falls back where nothing will say: Linux has no equivalent that is not a guess.
	 */
	const memory = $derived(
		readableMemory(hardware?.installed_ram_bytes ?? hardware?.total_ram_bytes ?? null)
	);

	onMount(load);

	/* And again when a setting moves somewhere else: this account in a browser, a second window,
	 * or another admin changing one the installation shares. Every control on this pane writes on
	 * the press and holds nothing unsaved, so a re-read can only put the same value back; see
	 * `scripts/check_settings_followed.js`, which holds every pane to this. */
	whenChanged(settingChanges, () => void load());
	onMount(async () => {
		here = await bridge.localHardware();
		health = await fetchHealth();
		try {
			hardware = await api.get<Hardware>('/performance/hardware');
		} catch {
			// A machine that will not describe itself is not a reason to break the rest of the pane.
			hardware = null;
		}
	});

	/* A duration a person can read. Tenths are the smallest unit worth showing: this is here to
	   answer "did Sift stop", and no one cares whether it was 4 or 7 milliseconds. */
	function readablePause(seconds: number): string {
		if (seconds < 0.1) return COPY.pause.tiny;
		return COPY.pause.seconds(seconds.toFixed(1));
	}

	/* The start-up reading, without the decimals nobody reads. Whole numbers here because the
	   figure only has to be recognisable as far too large, and 401 says that as well as 400.5. */
	function roundedMicroseconds(measured: number): string {
		return Math.round(measured).toLocaleString();
	}

	async function load() {
		try {
			const sections = await fetchSettings();
			/* EVERY section, keyed. The readout below says what each expensive feature is running
			 * on, and the two settings that decide that belong to Smart Search and to face
			 * recognition. Looked up in one section only, both would miss, both would fall to the
			 * "not nvidia" side of the test, and the table would report the processor on a machine
			 * set to use the card. */
			elsewhere = new Map(
				sections.flatMap((one) => one.settings).map((entry) => [entry.key, entry.value])
			);
		} catch {
			// Left as it was: a row the readout cannot vouch for is not drawn (see `deviceWord`).
		}
	}

	let selfTest = $state<SelfTest | null>(null);
	/* WHETHER SIFT SET THESE ITSELF: the run it starts on the first library folder saves what it
	   found (`performance/benchmark.py`), so the numbers under it are already in force and the pane
	   says who set them. Read again on the settings bell, which that save rings. */
	type FirstBenchmark = components['schemas']['FirstBenchmarkView'];
	let setBySift = $state(false);
	async function readFirstRun(): Promise<void> {
		try {
			const run = await api.get<FirstBenchmark>('/performance/benchmark');
			setBySift = run.state === 'set';
		} catch {
			setBySift = false;
		}
	}
	onMount(readFirstRun);
	whenChanged(settingChanges, () => void readFirstRun());
	/* HOW MANY FILES SIFT READS AT ONCE FROM A SHARE, as the server resolves it.
	 *
	 * Never worked out here. The stored setting is 0 for "automatic" and the rule that turns that
	 * into the real figure belongs to `kernel/lanes`, so a copy on this side would be a second
	 * rule free to disagree with the one the reads are actually running on.
	 */
	const shareReads = $derived(selfTest?.share_reads_now ?? 0);
	let testing = $state(false);
	let testProblem = $state<string | undefined>(undefined);
	let applying = $state(false);

	/* How many rungs of the encoding ladder have finished, and whether another one can still
	 * start.
	 *
	 * `levels.length + 1` (the rung now running) would count a sixth round "of up to 5": the
	 * ladder is only the FIRST part of a run, and once the last rung has reported the server
	 * carries on measuring the decoder and then every network share, with `running` still true.
	 * For the rest of the run the line would count a sixth round of a ladder with five rungs in
	 * it.
	 *
	 * Three things say the ladder cannot start another rung, and all three are already on the
	 * wire: every reachable rung has run (`rounds` is the reachable count. See
	 * `planned_levels`); the last rung reported that the app stopped keeping up, which is what
	 * breaks the walk; or the decoder's numbers have arrived, which the server only publishes
	 * after the ladder. No new field and no second copy of the ladder on this side.
	 */
	const laddered = $derived(selfTest?.measurement?.levels.length ?? 0);
	const rounds = $derived(selfTest?.rounds ?? 0);
	const climbing = $derived.by(() => {
		const levels = selfTest?.measurement?.levels ?? [];
		if (laddered >= rounds) return false;
		if (selfTest?.measurement?.decode) return false;
		return levels.every((one) => one.responsive);
	});

	onMount(async () => {
		/* A run may still be going from before this screen was opened (the test lives on the server,
		   not in this tab), so the first thing to do is ask, not offer. */
		try {
			selfTest = await fetchSelfTest();
			if (selfTest.running) void watch();
		} catch {
			selfTest = null; // not an admin, or the server did not answer. The panel simply stays away.
		}
	});

	async function measure() {
		testProblem = undefined;
		testing = true;
		try {
			selfTest = await startSelfTest();
			await watch();
		} catch (error) {
			testProblem = error instanceof Error ? error.message : COPY.measure.cannotStart;
			testing = false;
		}
	}

	/* Poll until the server says it has finished. The run belongs to the server, so leaving this page
	   and coming back picks it up again rather than losing it. */
	async function watch() {
		testing = true;
		try {
			for (;;) {
				await new Promise((wake) => setTimeout(wake, POLL_MS));
				selfTest = await fetchSelfTest();
				if (!selfTest.running) return;
			}
		} catch (error) {
			testProblem = error instanceof Error ? error.message : COPY.measure.stopped;
		} finally {
			testing = false;
		}
	}

	/* Applying goes through the ordinary settings save: the same validation, the same rollback if
	   the server refuses. A suggestion gets no privileges a typed number does not have. */
	async function applyAll(recommendations: Recommendation[]) {
		const changes = recommendations.filter((one) => one.changes_anything);
		if (changes.length === 0) return;
		applying = true;
		try {
			await saveSettings(Object.fromEntries(changes.map((one) => [one.key, one.suggested])));
			await load();
			/* Read back rather than assumed. The server compares each suggestion against the CURRENT
			   setting, so what comes back has already stopped calling these changes, which is what
			   takes the apply button away instead of leaving it offering to do it again. */
			selfTest = await fetchSelfTest();
		} catch (error) {
			testProblem = error instanceof Error ? error.message : COPY.measure.cannotSave;
		} finally {
			applying = false;
		}
	}

	/** What each feature is running on right now, in the words the settings use. */
	const runningOn = $derived(
		(
			[
				{ what: COPY.hardware.faces, where: deviceWord(elsewhere.get(FACES_DEVICE_KEY)) },
				{ what: COPY.hardware.smartSearch, where: deviceWord(elsewhere.get(SEMANTIC_DEVICE_KEY)) },
				{
					what: COPY.hardware.converting,
					where: hardware?.transcode_encoders.length ? 'GPU' : 'CPU'
				}
			] as { what: string; where: string | null }[]
		).filter((one): one is { what: string; where: string } => one.where !== null)
	);

	/* The device as a word, or NOTHING when there is no value to read.
	 *
	 * The `null` is the point. Answering "CPU" for anything that is not the string `nvidia` would
	 * turn a key that is missing (a section this account may not see, a request that failed, a
	 * feature renamed) into a confident, wrong reading of the machine. A row that is not drawn
	 * is honest; a row that says the wrong thing is not.
	 */
	function deviceWord(value: unknown): string | null {
		if (value === 'nvidia') return 'GPU';
		if (value === 'cpu') return 'CPU';
		return null;
	}
	/* IS SIFT KEEPING UP: the answer, and the four instruments behind it.
	 *
	 * Somebody asking "is Sift slow" should not have to read four tables and two lists to find
	 * out. One sentence answers it, computed from the same four readings, and the readings
	 * themselves are behind Details for whoever wants the why.
	 *
	 * Each is read from `/health` and means exactly what the row calls it (`kernel/diagnostics`):
	 *   - Sift itself (`loop`): how late the app's once-a-second heartbeat woke. The whole app,
	 *     screens included, was held for that long. No "right now": a held heartbeat cannot report
	 *     until it is free, so the reading only ever exists afterwards.
	 *   - Video and file work (`threads`): how long a do-nothing check, handed in once a second,
	 *     queued for a free worker thread; and how long the one in flight has queued so far.
	 *   - Screens reading the database (`database`): the same check, queued for a free database
	 *     connection.
	 *   - Work waiting for Sift (`loop_queue`): how long the app took to get through the work
	 *     already waiting for it.
	 * "Times" is how many of those checks measured `NOTICEABLE_SECONDS` or more since Sift
	 * started: the same quarter second at which the server writes the event to its log.
	 *
	 * The sentence picks, in order: anything waiting RIGHT NOW (the question is about now, so the
	 * present outranks any past); otherwise, if nothing was ever noticeable, yes; otherwise the
	 * reading with the longest wait since start, in the words of the thing that was slow.
	 */
	type Instrument = 'loop' | 'threads' | 'database' | 'queue';

	interface Reading {
		kind: Instrument;
		name: string;
		/** The longest since Sift started, in seconds. */
		longest: number;
		/** How many readings reached `NOTICEABLE_SECONDS` since Sift started. */
		times: number;
		/** How long the one in flight has waited so far, or null where there is no live reading. */
		now: number | null;
	}

	const readings = $derived.by((): Reading[] => {
		if (!health) return [];
		const found: Reading[] = [
			{
				kind: 'loop',
				name: COPY.keepingUp.loop,
				longest: health.loop.worstLagSeconds,
				times: health.loop.heldCount,
				now: null
			}
		];
		if (health.threads) {
			found.push({
				kind: 'threads',
				name: COPY.keepingUp.threads,
				longest: health.threads.worstWaitSeconds,
				times: health.threads.fullCount,
				now: health.threads.waitingSeconds
			});
		}
		if (health.database) {
			found.push({
				kind: 'database',
				name: COPY.keepingUp.database,
				longest: health.database.worstWaitSeconds,
				times: health.database.fullCount,
				now: health.database.waitingSeconds
			});
		}
		if (health.queue) {
			found.push({
				kind: 'queue',
				name: COPY.keepingUp.queue,
				longest: health.queue.worstSeconds,
				times: health.queue.overCount,
				now: health.queue.latestSeconds
			});
		}
		return found;
	});

	/** The reading that is noticeably waiting at this moment, the longest first, if any is. */
	const waitingNow = $derived(
		readings
			.filter((one) => one.now !== null && one.now >= NOTICEABLE_SECONDS)
			.sort((a, b) => (b.now ?? 0) - (a.now ?? 0))[0]
	);

	const verdict = $derived.by((): string => {
		if (waitingNow && waitingNow.kind !== 'loop' && waitingNow.now !== null) {
			return COPY.keepingUp.now[waitingNow.kind](readablePause(waitingNow.now));
		}
		const felt = readings.filter((one) => one.times > 0);
		if (felt.length === 0) return COPY.keepingUp.yes;
		const worst = felt.reduce((most, one) => (one.longest > most.longest ? one : most));
		return COPY.keepingUp.was[worst.kind](
			COPY.keepingUp.times(worst.times),
			readablePause(worst.longest)
		);
	});

	/* The figures behind the fold, as text somebody pastes into a bug report. Read off the same
	   values the tables draw, in the same order, so the copy cannot say something the screen does
	   not. */
	let reportCopied = $state(false);

	async function copyReport(): Promise<void> {
		if (!health) return;
		const lines = [
			`${COPY.keepingUp.what}\t${COPY.keepingUp.longest}\t${COPY.keepingUp.timesHeading}\t${COPY.keepingUp.rightNow}`,
			...readings.map(
				(one) =>
					`${one.name}\t${readablePause(one.longest)}\t${one.times}\t${one.now === null ? '' : readablePause(one.now)}`
			),
			'',
			`${COPY.report.work}\t${COPY.report.times}\t${COPY.report.total}\t${COPY.report.slowest}`,
			...health.work.map((kind) => `${kind.stage}\t${kind.runs}\t${kind.totalMs}\t${kind.worstMs}`),
			'',
			`${COPY.report.read}\t${COPY.report.most}\t${COPY.report.times}\t${COPY.report.total}`,
			...health.widest.map(
				(read) => `${read.read}\t${read.widestRows}\t${read.runs}\t${read.totalRows}`
			)
		];
		reportCopied = await copyText(lines.join('\n'));
		if (reportCopied) setTimeout(() => (reportCopied = false), COPIED_FOR);
	}
</script>

<section class="performance">
	<p class="lede">{COPY.lede}</p>

	<!-- What the machine is, before anything that tunes it. A full table rather than a line: the
	     numbers below are chosen from these, so reading them first is what makes the choices
	     legible. -->
	{#if hardware}
		<div class="machine" data-testid="hardware" bind:this={machineEl}>
			<!-- WHOSE MACHINE. In client mode every line under this heading is about a computer
			     somewhere else, so the heading has to say so; on the machine running the
			     library the plain words are the true ones and there is nothing else on screen
			     to tell it apart from. -->
			<SectionHeading>
				{serverBlockName}
				{#snippet actions()}
					<!-- Somebody asking for help with Sift is asked what their machine is, and reading a
					     table back by hand is how a wrong answer gets given. -->
					<Tooltip label={copied ? COPY.hardware.copied : COPY.hardware.copy}>
						<Button
							tone="ghost"
							size="small"
							icon={copied ? 'check' : 'content_copy'}
							aria-label={COPY.hardware.copyDetails}
							onclick={() => void copyMachine()}
						/>
					</Tooltip>
				{/snippet}
			</SectionHeading>
			<dl class="readings">
				<div>
					<dt>{COPY.hardware.cpu}</dt>
					<dd>{hardware.cpu_model ?? COPY.hardware.unknown}</dd>
				</div>
				<div>
					<dt>{COPY.hardware.threads}</dt>
					<dd>{hardware.cpu_count}</dd>
				</div>
				<div>
					<dt>{COPY.hardware.memory}</dt>
					<dd>{memory}</dd>
				</div>
				<!-- A ROW PER ADAPTER, because a machine can have more than one and naming only one
				     is a description of somebody's computer that is quietly wrong. A chip built into
				     the processor beside a card in a slot is the ordinary shape.
				     Which one does the WORK is a separate question and is answered by the card
				     itself, not by its position: Windows enumerates them in its own order, and only
				     an NVIDIA card can have a model put on it. -->
				{#if hardware.gpu_cards && hardware.gpu_cards.length > 0}
					{#each hardware.gpu_cards as card, at (`${at}-${card.name}`)}
						<div>
							<dt>{at === 0 ? COPY.hardware.gpu : COPY.hardware.alsoInstalled}</dt>
							<dd>
								{card.name ?? COPY.hardware.unnamedGpu}{card.vram_bytes
									? ` \u2014 ${readableMemory(card.vram_bytes)}`
									: ''}{card.can_compute && hardware.gpu_cards.length > 1
									? COPY.hardware.usesThisOne
									: ''}
							</dd>
						</div>
					{/each}
				{:else}
					<div>
						<dt>{COPY.hardware.gpu}</dt>
						<dd>
							{#if hardware.cuda || hardware.rocm}
								{COPY.hardware.unnamedPresent}
							{:else}
								{COPY.hardware.noGpu}
							{/if}
						</dd>
					</div>
				{/if}
				{#if hardware.gpu_driver}
					<div>
						<dt>{COPY.hardware.driver}</dt>
						<dd>{hardware.gpu_driver}</dd>
					</div>
				{/if}
				<div>
					<dt>{COPY.hardware.encoders}</dt>
					<dd>
						{hardware.transcode_encoders.length > 0
							? hardware.transcode_encoders.join(', ')
							: COPY.hardware.noEncoders}
					</dd>
				</div>
				{#each runningOn as one (one.what)}
					<div>
						<dt>{one.what}</dt>
						<dd>{one.where}</dd>
					</div>
				{/each}
			</dl>
			<!-- What Sift noticed about this machine, not a failure of anything anybody did, so it is
			     not a `Problem` and is deliberately not announced as one. A note saying a graphics card
			     was not found is describing the panel it sits in; interrupting somebody with it every
			     time the panel loads would be the reverse of useful. -->
			{#each hardware.warnings as note, at (`${at}:${note}`)}
				<p class="note">{note}</p>
			{/each}
		</div>
	{/if}

	{#if here}
		<!-- THE COMPUTER THE PERSON IS ACTUALLY SITTING AT, and only when it is a different one.
		     Three facts, and no graphics card among them on purpose: the card in this computer does
		     no work for Sift (every model, transcode and scan runs on the server above), and a row
		     for it would invite the reading that turning one on here would change something. -->
		<div class="machine" data-testid="local-hardware">
			<SectionHeading>{COPY.hardware.thisDevice}</SectionHeading>
			<dl class="readings">
				<div>
					<dt>{COPY.hardware.cpu}</dt>
					<dd>{here.cpu_model ?? COPY.hardware.unknown}</dd>
				</div>
				<div>
					<dt>{COPY.hardware.threads}</dt>
					<dd>{here.thread_count}</dd>
				</div>
				<div>
					<dt>{COPY.hardware.memory}</dt>
					<dd>{readableMemory(here.installed_ram_bytes)}</dd>
				</div>
				<!-- Named, though none of it does work for Sift. A block headed "This computer" that
				     lists a processor and a memory size and then says nothing about the graphics is a
				     description with a hole in it, and the sentence under the block already says what
				     this machine is and is not doing. -->
				{#each here.gpu_cards as card, at (`${at}-${card.name}`)}
					<div>
						<dt>{at === 0 ? COPY.hardware.gpu : COPY.hardware.alsoInstalled}</dt>
						<dd>
							{card.name ?? COPY.hardware.unnamedGpu}{card.vram_bytes
								? ` \u2014 ${readableMemory(card.vram_bytes)}`
								: ''}
						</dd>
					</div>
				{/each}
			</dl>
			<p class="note">{COPY.hardware.elsewhere}</p>
		</div>
	{/if}

	<!-- Straight after the table that says whether there is a card, because "you have a card
	     and it is not being used" is a sentence with two halves and they belong next to each
	     other: the table names the card, the two device settings may refuse it, and this says
	     what to do about that. -->
	<GraphicsCard card={gpu} />

	<!-- "How long jobs take" is a record of what each pass cost, not a live reading, so it is
	     in Activity > History with every other record. -->

	<!-- The benchmark, under the description of the device it measures. What it suggests is
	     applied to the numbers behind Concurrency, through the ordinary settings save. -->
	{#if selfTest}
		<div class="block" data-testid="self-test">
			<SectionHeading id={MEASURE_ANCHOR}>{COPY.measure.name}</SectionHeading>
			<div class="self-test">
				<!-- The benchmark as a row: its name and what it is for on the left, the press on the
				     right; the long account of what it measures is the row's More about this. -->
				<!--
					WHAT IS WAITING ON THE TEST, said on the row whose button runs it.

					It runs by itself once, when the first library folder is added, and otherwise
					only when somebody presses that. So until then every file is read the way that
					needs no measurement (one seek per moment), and the quicker shape, one decode
					of the whole file, is simply not chosen. Without this sentence that is
					invisible: the sentence under a finished run explains the choice, and before the
					first run there would be nothing at all, so the quicker read would look like
					something Sift was already doing.

					`measured` and not `finished`. Rates are stored, so a machine measured last week
					comes back with no run in this process and its rates in hand, and this
					sentence must not reappear for it.
				-->
				{#snippet unmeasured()}
					<span data-testid="self-test-unmeasured">{COPY.measure.unmeasured}</span>
				{/snippet}
				<LabelledRow
					id="performance.benchmark"
					label={COPY.measure.row}
					help={COPY.measure.help}
					disclosure={COPY.measure.lede}
					foot={selfTest.running || testing || selfTest.measured ? undefined : unmeasured}
				>
					<Button
						tone="secondary"
						icon="play_arrow"
						onclick={measure}
						busy={selfTest.running || testing}
						disabled={applying || selfTest.running || testing}
					>
						{selfTest.finished ? COPY.measure.again : COPY.measure.run}
					</Button>
				</LabelledRow>

				<Problem message={testProblem} />

				{#if selfTest.running || testing}
					<!--
						SOMETHING THAT MOVES, AND A NUMBER THAT CHANGES.

						A run is two to four minutes on a slow machine, with the processor pinned. One
						static sentence for all of it is indistinguishable from a test that started and
						died, so people press the button again, which the server correctly ignores,
						which looks like it is broken twice.

						Two signals rather than one, because they answer different doubts. The spinner
						says the page is alive. The rounds say the RUN is: the server publishes each
						level as it finishes, so the count moves every twenty seconds or so and somebody
						can see it working through the ladder.

						"up to" is not hedging. A run genuinely stops early when the app stops keeping
						up (that is the test succeeding), so a total counting down would be wrong on
						exactly the machines it matters most on.

						Only that stops the walk. A level that stops helping does NOT break it: every
						reachable rung runs, and `best` picks the one to recommend afterwards, which is
						the whole reason the wider rungs are measured at all. So the sentence beside it
						says "when the app stops keeping up" and nothing about a level that stops
						helping.
					-->
					<div data-testid="self-test-running">
						<Empty scope="block" busy>{COPY.measure.busy}</Empty>
					</div>
					{#if selfTest.rounds > 0}
						<div class="rounds">
							<ProgressBar value={laddered} max={rounds} label={COPY.measure.name} />
							<p class="verdict" data-testid="self-test-rounds">
								{#if climbing}
									{COPY.measure.round(laddered + 1, rounds)}
								{:else}
									{COPY.measure.roundsDone(laddered, rounds)}
								{/if}
							</p>
						</div>
					{/if}
				{/if}

				{#if selfTest.measurement?.failed}
					<!-- A test that could not run is not a machine that is slow, and the two call for opposite
					     things. Said plainly rather than shown as a result of zero. -->
					<p class="verdict" data-testid="self-test-failed">
						{COPY.measure.failed(selfTest.measurement.failed)}
					</p>
				{:else if selfTest.finished && selfTest.recommendations.length > 0}
					{@const changes = selfTest.recommendations.filter((one) => one.changes_anything)}
					{#if setBySift}
						<p class="verdict" data-testid="self-test-set-by-sift">{COPY.measure.setBySift}</p>
					{/if}
					<dl class="suggestions">
						{#each selfTest.recommendations as one (one.key)}
							<div class="suggestion" class:unchanged={!one.changes_anything}>
								<dt>{one.label}</dt>
								<dd>
									<span class="numbers">
										<!-- The arrow character, not "-&gt;": what is on screen is typeset. -->
										{one.current === 0 ? COPY.measure.automatic : one.current} &rarr;
										<strong>{one.suggested}</strong>
									</span>
									<!-- A paragraph, so the pane's one reading measure reaches it. -->
									<p class="reason">{one.reason}</p>
								</dd>
							</div>
						{/each}
					</dl>
					<!-- Not on a pane held read only: applying is a change to how hard the computer works. -->
					{#if changes.length > 0 && !held()}
						<Button
							tone="primary"
							onclick={() => applyAll(selfTest?.recommendations ?? [])}
							disabled={applying}
						>
							{applying ? COPY.measure.applying : COPY.measure.apply(changes.length)}
						</Button>
						<p class="verdict">
							{COPY.measure.nothingYet}
						</p>
					{:else}
						<p class="verdict" data-testid="self-test-agrees">
							{COPY.measure.agrees}
						</p>
					{/if}
				{:else if selfTest.finished}
					<p class="verdict">
						{COPY.measure.notEnough}
					</p>
				{/if}

				{#if selfTest.finished && selfTest.measurement?.decode}
					<!-- The two rates Generate and Identify read every file by. Shown so the choice made per file
					     can be read off the numbers rather than taken on trust. -->
					<p class="verdict" data-testid="self-test-decode">
						{COPY.measure.decode(
							Math.round(selfTest.measurement.decode.frames_per_second),
							Math.round(selfTest.measurement.decode.seek_seconds * 1000)
						)}
					</p>
				{/if}

				{#if selfTest.finished && (selfTest.measurement?.storages?.length ?? 0) > 0}
					<!-- What each share delivered at each width, so the number recommended above can be
					     read off the curve it came from rather than taken on trust; and beside it the
					     number Sift is running on, which is the fact a person on a share needs and the
					     one the curve alone does not say. -->
					<div class="storages" data-testid="self-test-storages">
						<SectionHeading level={3}>{COPY.measure.shares}</SectionHeading>
						{#if shareReads > 0}
							<p class="verdict" data-testid="self-test-share-reads">
								{COPY.measure.shareReads(shareReads)}
							</p>
						{/if}
						{#each selfTest.measurement?.storages ?? [] as curve (curve.storage)}
							<div class="storage">
								<p class="storage-name">{curve.label}</p>
								{#if curve.failed}
									<p class="verdict">{COPY.measure.notMeasured(curve.failed)}</p>
								{:else}
									<ul class="storage-levels">
										{#each curve.levels as level (level.at_once)}
											<li class:best={level.at_once === curve.best_at_once}>
												{COPY.measure.level(level.at_once, level.megabytes_per_second)}
											</li>
										{/each}
									</ul>
									{#if curve.best_at_once !== null && curve.best_at_once !== undefined}
										<p class="storage-name">
											{COPY.measure.quickest(curve.best_at_once)}{shareReads > 0 &&
											curve.best_at_once !== shareReads
												? COPY.measure.readingAt(shareReads)
												: ''}
										</p>
									{/if}
								{/if}
							</div>
						{/each}
					</div>
				{/if}
			</div>
		</div>
	{/if}

	<!-- Read-only, and below the controls on purpose: it is the answer to "did that change help",
	     which is a question you ask after touching something above. Absent for anyone who is not an
	     admin, because the server does not send it to them. One sentence answers it; the four
	     instruments and the two diagnosis tables are folded under Details. See `readings`. -->
	{#if health}
		<div class="block" data-testid="keeping-up">
			<SectionHeading id={KEEPING_UP_ANCHOR}>{COPY.keepingUp.name}</SectionHeading>
			<div class="keeping-up">
				<p class="verdict" class:bad={waitingNow !== undefined} data-testid="keeping-up-verdict">
					{verdict}
				</p>
				{#if waitingNow}
					<p class="note">{COPY.keepingUp.busyHelp}</p>
				{/if}
				<!-- The pane's fold, named for what it holds. Not `MoreAbout`, whose summary never
				     changes on purpose. -->
				<Fold summary={COPY.keepingUp.details} id="performance.details">
					<div class="details-body">
						<table class="work" data-testid="keeping-up-readings">
							<thead>
								<tr>
									<th scope="col">{COPY.keepingUp.what}</th>
									<th scope="col">{COPY.keepingUp.longest}</th>
									<th scope="col">{COPY.keepingUp.timesHeading}</th>
									<th scope="col">{COPY.keepingUp.rightNow}</th>
								</tr>
							</thead>
							<tbody>
								{#each readings as one (one.kind)}
									<tr data-kind={one.kind}>
										<th scope="row">{one.name}</th>
										<td class:bad={one.longest >= NOTICEABLE_SECONDS}
											>{readablePause(one.longest)}</td
										>
										<td class:bad={one.times > 0}>{one.times.toLocaleString()}</td>
										<td class:bad={(one.now ?? 0) >= NOTICEABLE_SECONDS}>
											{#if one.now !== null}
												{one.now > 0 ? readablePause(one.now) : COPY.keepingUp.clear}
											{/if}
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
						<p class="note">{COPY.keepingUp.explain}</p>
						{#if health.database}
							{#if !health.database.pointReadsInline}
								<!-- Not a wait: the reason the database waits are the shape they are. A lookup
							     answered straight away never queues for a connection at all. -->
								<p class="note" data-testid="point-reads-verdict">
									{COPY.keepingUp.slowLookups(
										roundedMicroseconds(health.database.pointReadMicroseconds)
									)}
								</p>
							{/if}
							{#if health.database.sweeping}
								<p class="note" data-testid="sweeping">
									{COPY.keepingUp.sweeping(health.database.sweeping)}
								</p>
							{/if}
							<p class="note">{COPY.keepingUp.connections(health.database.readers)}</p>
						{/if}
						{#if health.work.length > 0 || health.widest.length > 0}
							<div class="report" data-testid="bug-report">
								<SectionHeading level={3}>
									{COPY.report.name}
									{#snippet actions()}
										<Tooltip label={reportCopied ? COPY.report.copied : COPY.report.copy}>
											<Button
												tone="ghost"
												size="small"
												icon={reportCopied ? 'check' : 'content_copy'}
												aria-label={COPY.report.copyLabel}
												onclick={() => void copyReport()}
											/>
										</Tooltip>
									{/snippet}
								</SectionHeading>
								<p class="note">{COPY.report.lede}</p>
								{#if health.work.length > 0}
									<!-- Ordered by the total, as the server ranked it: the shape worth catching is
								     a cheap thing done far too often, which the worst single run hides. -->
									<table class="work" data-testid="work-health">
										<thead>
											<tr>
												<th scope="col">{COPY.report.work}</th>
												<th scope="col">{COPY.report.times}</th>
												<th scope="col">{COPY.report.total}</th>
												<th scope="col">{COPY.report.slowest}</th>
											</tr>
										</thead>
										<tbody>
											{#each health.work as kind (kind.stage)}
												<tr>
													<th scope="row">{kind.stage}</th>
													<td>{kind.runs.toLocaleString()}</td>
													<td class:bad={kind.totalMs >= NOTICEABLE_SECONDS * 1000}>
														{readablePause(kind.totalMs / 1000)}
													</td>
													<td>{readablePause(kind.worstMs / 1000)}</td>
												</tr>
											{/each}
										</tbody>
									</table>
								{/if}
								{#if health.widest.length > 0}
									<!-- The one figure that is not a stopwatch: how many rows a read handed back,
								     which is the same on a busy device as on an idle one. -->
									<table class="work" data-testid="widest-reads">
										<thead>
											<tr>
												<th scope="col">{COPY.report.read}</th>
												<th scope="col">{COPY.report.most}</th>
												<th scope="col">{COPY.report.times}</th>
												<th scope="col">{COPY.report.total}</th>
											</tr>
										</thead>
										<tbody>
											{#each health.widest as read (read.read)}
												<tr>
													<th scope="row" class="sql">{read.read}</th>
													<td class:bad={read.widestRows >= WIDE_READ_ROWS}>
														{read.widestRows.toLocaleString()}
													</td>
													<td>{read.runs.toLocaleString()}</td>
													<td>{read.totalRows.toLocaleString()}</td>
												</tr>
											{/each}
										</tbody>
									</table>
								{/if}
							</div>
						{/if}
					</div>
				</Fold>
			</div>
		</div>
	{/if}

	<!-- How much Sift does at once, and how much less while somebody works. -->
	<Concurrency />

	<!-- LAST, whatever else the pane holds: the one act here that takes something away sits where
	     nobody reaches it on the way to something else. -->
	<GraphicsCardRemove card={gpu} />
</section>

<style>
	.sql {
		font-family: var(--sift-font-mono, ui-monospace, monospace);
		font-size: 0.78rem;
		font-weight: 400;
		word-break: break-word;
	}

	.work {
		width: 100%;
		border-collapse: collapse;
		font: var(--text-body-sm);
	}

	.work th,
	.work td {
		padding: var(--space-1) var(--space-2);
		text-align: end;
		border-block-end: 1px solid var(--border);
	}

	.work th[scope='col'] {
		color: var(--sift-ink-2);
		font-weight: 500;
	}

	.work th[scope='col']:first-child,
	.work th[scope='row'] {
		text-align: start;
	}

	.work th[scope='row'] {
		font-weight: 400;
	}

	.work td.bad {
		color: var(--sift-warn);
	}

	/* The measurement's own column, under its heading. */
	.self-test {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	/* The spinner sits ON the sentence's line rather than above it: one thing that is working, not
	   a mark and then a caption. */
	/* The bar and the count under it, as one block held off the sentence above. */
	.rounds {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		margin-block-start: var(--space-3);
		max-inline-size: 40rem;
	}

	.suggestions {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		margin: 0;
	}

	.suggestion.unchanged {
		opacity: 0.55;
	}

	.suggestion dt {
		font-weight: 600;
	}

	.suggestion dd {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		margin: 0;
	}

	.numbers {
		font-variant-numeric: tabular-nums;
	}

	.reason {
		margin: 0;
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
	}

	/* Each share's curve, under the suggestions it produced. Read the same way a suggestion is:
	   a name, then the numbers, with the level that was chosen carrying the weight. */
	.storages {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.storage {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}

	.storage-name {
		margin: 0;
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
	}

	.storage-levels {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-3);
		margin: 0;
		padding: 0;
		list-style: none;
		font: var(--text-body-sm);
		font-variant-numeric: tabular-nums;
	}

	.storage-levels .best {
		font-weight: 600;
	}

	/* No width of its own: the shell caps the content once, for every pane. And no gap of its own
	   either: in ordinary block flow the space each group leaves under itself collapses with the
	   room above the next group's heading, as it does on every other pane. */
	.performance > .lede {
		margin-block-end: var(--space-6);
	}

	/* The machine notes above. Quieter than a problem and not red: nothing is wrong. */
	.note {
		margin: 0;
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
	}

	.lede {
		margin: 0;
		color: var(--sift-ink-2);
		font: var(--text-body);
	}

	/* The answer and what is folded under it, as one column under the block's heading. */
	.keeping-up {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	/*
	 * A block of this page that is not a `SettingGroup` but reads as one: its heading, then what it
	 * holds. It leaves the group's own space under itself, and it is ordinary block flow, so that
	 * space collapses with the room above the next heading. What a block lays out inside itself
	 * (a column of readings, a gap between them) sits one element in, under the heading, where a
	 * flex or grid container cannot keep the heading's room from collapsing.
	 */
	.machine,
	.block {
		margin-block-end: var(--space-8);
	}

	/* The one control on this pane that is an icon and nothing else. Round, quiet, and the same
	   shape as every other icon-only button in the app. */
	/* One row per fact, in the shape every fact on a settings pane has (`FactRow`): the name in
	 * semibold full ink on the left, the value in quieter ink against the right edge, both on one
	 * baseline. The ROW draws the line, so it runs unbroken under the name and the value alike.
	 */
	.readings {
		margin: var(--space-3) 0 0;
	}

	.readings > div {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		align-items: baseline;
		column-gap: var(--space-6);
		padding-block: var(--space-4);
	}

	/* A line only BETWEEN two facts, drawn by the lower one: the last fact draws none, so the next
	   group's heading keeps the only line between the two groups. */
	.readings > div + div {
		border-block-start: 1px solid var(--sift-line);
	}

	.readings dt {
		color: var(--sift-ink);
		font: var(--text-body);
		font-weight: 600;
		line-height: 1.3;
	}

	.readings dd {
		margin: 0;
		color: var(--sift-ink-2);
		font: var(--text-body);
		font-variant-numeric: tabular-nums;
		text-align: end;
	}

	/* At a phone's width a fact stacks as a settings row does: the value under its name, starting
	   where the name starts. Side by side, a long value (a graphics card's whole name) takes the
	   line and presses the name against it with no gap at all. */
	@media (max-width: 767px) {
		.readings > div {
			grid-template-columns: minmax(0, 1fr);
			row-gap: var(--space-1);
		}

		.readings dd {
			text-align: start;
		}
	}

	.verdict {
		margin: 0;
		color: var(--sift-ink-2);
		font: var(--text-body);
	}

	/* The answer, marked when something is waiting at this moment. Only the present is marked: a
	   pause an hour ago is history, and colouring it would read as a fault happening now. */
	.verdict.bad {
		color: var(--sift-warn);
	}

	/* The column inside the fold, rather than a flex `<details>`: a disclosure laid out as a flex
	   box is drawn differently from one browser to the next. */
	.details-body {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	.report {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		margin-block-start: var(--space-4);
	}
</style>
