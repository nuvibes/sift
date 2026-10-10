<script lang="ts">
	/* Performance: this device, and how Sift is doing on it. */
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
	import { COPY, MEASURE_ANCHOR } from './Performance.search';
	import Concurrency from './Concurrency.svelte';
	import KeepingUp from './KeepingUp.svelte';
	import SectionHeading from '$lib/components/common/SectionHeading.svelte';

	/* The graphics card, read once for the block that describes it and the row that deletes it. */
	const gpu = new GraphicsCardState();
	gpu.follow();
	/* Read only on a phone (`read-only.ts`): the rows hold themselves; the one press outside a
	   row asks the same pane. */
	const held = paneHeld();

	/* How long the tick stays before the button goes back to offering the copy. */
	const COPIED_FOR = 2000;

	/* The value of every setting there is, by key, for the two the readout needs: what face
	   recognition and Smart Search run on. */
	let elsewhere = $state(new Map<string, unknown>());
	let health = $state<ServerHealth | null>(null);

	/* What this device turned out to be, from the check the server runs once at startup. */
	type Hardware = components['schemas']['HardwareView'];
	let hardware = $state<Hardware | null>(null);

	/* THE OTHER COMPUTER, when there is one. */
	let here = $state<LocalMachine | null>(null);

	/* WHOSE MACHINE THE FIRST BLOCK IS ABOUT. "This machine" is only ever true of the desktop
	 * application running the library itself. */
	const serverBlockName = $derived(
		here === null && bridge.isDesktop() ? COPY.hardware.thisDevice : COPY.hardware.sifts
	);

	/** Bytes as something a person reads. Whole gigabytes: nobody tunes a machine to a decimal. */
	let machineEl = $state<HTMLElement | null>(null);
	let copied = $state(false);

	/* The table as words, read off the table itself so the copy cannot drift from it. */
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

	/* The installed memory, as a Windows dialog names it, not the smaller addressable figure
	   every sizing uses. */
	const memory = $derived(
		readableMemory(hardware?.installed_ram_bytes ?? hardware?.total_ram_bytes ?? null)
	);

	onMount(load);

	/* And again when a setting moves somewhere else: this account in a browser, a second window,
	 * or another admin changing one the installation shares. */
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

	async function load() {
		try {
			const sections = await fetchSettings();
			/* Every section: the device settings the readout reads belong to Smart Search and Faces. */
			elsewhere = new Map(
				sections.flatMap((one) => one.settings).map((entry) => [entry.key, entry.value])
			);
		} catch {
			// Left as it was: a row the readout cannot vouch for is not drawn (see `deviceWord`).
		}
	}

	let selfTest = $state<SelfTest | null>(null);
	/* What Sift's own run set, each key with the value it left; read again on the settings bell. */
	type FirstBenchmark = components['schemas']['FirstBenchmarkView'];
	let setBySift = $state(new Map<string, number>());
	async function readFirstRun(): Promise<void> {
		try {
			const run = await api.get<FirstBenchmark>('/performance/benchmark');
			setBySift = new Map(run.changes.map((one) => [one.key, one.after]));
		} catch {
			setBySift = new Map();
		}
	}
	/* Only a row Sift set and nobody has moved since stands under the sentence that says so. */
	function wasSet(one: Recommendation): boolean {
		return !one.changes_anything && setBySift.get(one.key) === one.current;
	}
	onMount(readFirstRun);
	whenChanged(settingChanges, () => void readFirstRun());
	/* How many files Sift reads at the same time from a share, as the server resolves it (0 is
	   automatic). */
	const shareReads = $derived(
		selfTest?.measurement?.storages?.some((one) => one.remote) ? (selfTest.share_reads_now ?? 0) : 0
	);
	let testing = $state(false);
	let testProblem = $state<string | undefined>(undefined);
	let applying = $state(false);

	/* The run going's rungs (`progress`, never the result), and whether another can start. */
	const laddered = $derived(selfTest?.progress?.levels.length ?? 0);
	const rounds = $derived(selfTest?.rounds ?? 0);
	const timeLeft = $derived(COPY.measure.left(selfTest?.seconds_left, selfTest?.left_timed));
	const climbing = $derived.by(() => {
		const levels = selfTest?.progress?.levels ?? [];
		if (laddered >= rounds || (selfTest?.step ?? 'encoding') !== 'encoding') return false;
		if (selfTest?.progress?.decode) return false;
		return levels.every((one) => one.responsive);
	});

	onMount(async () => {
		/* A run may still be going from before this screen was opened (the test lives on the
		   server, not in this tab), so the first thing to do is ask, not offer. */
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

	/* Poll until the server says it has finished; the run is the server's, so a page left finds it. */
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

	/* Through the ordinary settings save: the same validation and rollback as a typed number. */
	async function applyAll(recommendations: Recommendation[]) {
		const changes = recommendations.filter((one) => one.changes_anything);
		if (changes.length === 0) return;
		applying = true;
		try {
			await saveSettings(Object.fromEntries(changes.map((one) => [one.key, one.suggested])));
			await load();
			/* Read back: the server compares against the CURRENT setting, so the apply button goes. */
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

	/* The device as a word, or null where nothing says: a missing key is never read as the CPU. */
	function deviceWord(value: unknown): string | null {
		if (value === 'nvidia') return 'GPU';
		if (value === 'cpu') return 'CPU';
		return null;
	}
</script>

<section class="performance">
	<p class="lede">{COPY.lede}</p>

	<!-- What the machine is, before anything that tunes it. -->
	{#if hardware}
		<div class="machine" data-testid="hardware" bind:this={machineEl}>
			<!-- WHOSE MACHINE. -->
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
				<!--
					A ROW PER ADAPTER, because a machine can have more than one and naming only one is
					a description of somebody's computer that is quietly wrong.
				-->
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
			<!--
				What Sift noticed about this machine, not a failure of anything anybody did, so it is
				not a `Problem` and is deliberately not announced as one.
			-->
			{#each hardware.warnings as note, at (`${at}:${note}`)}
				<p class="note">{note}</p>
			{/each}
		</div>
	{/if}

	{#if here}
		<!--
			THE COMPUTER THE PERSON IS ACTUALLY SITTING AT, and only when it is a different one.
		-->
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
				<!-- Named, though none of it does work for Sift. -->
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

	<!-- Straight after the table that names the card: this says what to do when it isn't used. -->
	<GraphicsCard card={gpu} />

	<!-- The benchmark, under the device it measures; its advice goes through the settings save. -->
	{#if selfTest}
		<div class="block" data-testid="self-test">
			<SectionHeading id={MEASURE_ANCHOR}>{COPY.measure.name}</SectionHeading>
			<div class="self-test">
				<!-- The benchmark as a row; what it measures is the row's More about this. -->
				<!-- What waits on the benchmark; `measured`, not `finished`, as rates outlive a restart. -->
				{#snippet unmeasured()}
					<span data-testid="self-test-unmeasured">{COPY.measure.unmeasured}</span>
				{/snippet}
				{#snippet suggestions(rows: Recommendation[], testid: string)}
					{#if rows.length > 0}
						<dl class="suggestions" data-testid={testid}>
							{#each rows as one (one.key)}
								<div class="suggestion" class:unchanged={!one.changes_anything}>
									<dt>{one.label}</dt>
									<dd>
										<!-- The arrow character, not "-&gt;": what is on screen is typeset. -->
										<span class="numbers">
											{one.current === 0 ? COPY.measure.automatic : one.current} &rarr;
											<strong>{one.suggested === 0 ? COPY.measure.automatic : one.suggested}</strong
											>
										</span>
										<p class="reason">{one.reason}</p>
									</dd>
								</div>
							{/each}
						</dl>
					{/if}
				{/snippet}
				<LabelledRow
					id="performance.benchmark"
					label={COPY.measure.row}
					help={COPY.measure.help}
					disclosure={COPY.measure.about(selfTest.whole_seconds, selfTest.whole_timed)}
					foot={selfTest.running || testing || selfTest.measured ? undefined : unmeasured}
				>
					<Button
						tone="secondary"
						icon="play_arrow"
						onclick={measure}
						busy={selfTest.running || testing}
						disabled={applying || selfTest.running || testing}
					>
						{selfTest.finished || selfTest.measured ? COPY.measure.again : COPY.measure.run}
					</Button>
				</LabelledRow>

				<Problem message={testProblem} />

				{#if selfTest.running || testing}
					<!-- The rounds move as each level is published; "up to" because a run can stop early. -->
					<div data-testid="self-test-running">
						<Empty scope="block" busy>{COPY.measure.busy}</Empty>
					</div>
					{#if selfTest.rounds > 0}
						<div class="rounds">
							<ProgressBar value={laddered} max={rounds} label={COPY.measure.name} />
							<p class="verdict" data-testid="self-test-rounds">
								{climbing
									? COPY.measure.round(laddered + 1, rounds)
									: COPY.measure.roundsDone(laddered, rounds, selfTest.step ?? null)}
								<span data-testid="self-test-left">{timeLeft}</span>
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
					{@const set = selfTest.recommendations.filter(wasSet)}
					{@const rest = selfTest.recommendations.filter((one) => !wasSet(one))}
					{#if set.length > 0}
						<p class="verdict" data-testid="self-test-set-by-sift">
							{COPY.measure.setBySift(set.length)}
						</p>
						{@render suggestions(set, 'self-test-set')}
						{#if rest.length > 0}
							<p class="verdict apart" data-testid="self-test-not-set">
								{COPY.measure.notSet(rest.length)}
							</p>
						{/if}
					{/if}
					{@render suggestions(rest, 'self-test-suggested')}
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

				{#each selfTest.finished ? selfTest.notes : [] as note (note)}
					<p class="verdict" data-testid="self-test-note">{note}</p>
				{/each}
				{#if selfTest.whole_to_come && !selfTest.running}
					<p class="verdict" data-testid="self-test-whole">
						{COPY.measure.wholeToCome(selfTest.whole_due)}
					</p>
				{/if}

				{#if selfTest.finished && selfTest.measurement?.decode}
					<!-- The two rates Generate and Identify read every file by. -->
					<p class="verdict" data-testid="self-test-decode">
						{COPY.measure.decode(
							Math.round(selfTest.measurement.decode.frames_per_second),
							Math.round(selfTest.measurement.decode.seek_seconds * 1000),
							selfTest.measurement.storages.some((one) => one.remote)
						)}
					</p>
				{/if}

				{#if selfTest.finished && (selfTest.measurement?.storages?.length ?? 0) > 0}
					<!-- Each storage's curve, so the number above is read off what it came from. -->
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
								{#if curve.folders}
									<p class="storage-name">{COPY.measure.folders(curve.folders)}</p>
								{/if}
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
											{COPY.measure.quickest(curve.best_at_once)}{curve.remote &&
											shareReads > 0 &&
											curve.best_at_once !== shareReads
												? COPY.measure.readingAt(shareReads)
												: ''}
										</p>
									{/if}
								{/if}
								<!-- What the storage can give beside what Sift's own reads get from it. -->
								<div data-testid="self-test-storage-rates">
									{#if curve.ceiling_mb_per_second}
										<p class="storage-name">{COPY.measure.canGive(curve.ceiling_mb_per_second)}</p>
									{/if}
									{#if curve.small_files_per_second && curve.small_mb_per_second}
										<p class="storage-name">
											{COPY.measure.small(curve.small_files_per_second, curve.small_mb_per_second)}
										</p>
									{/if}
									{#if curve.listed_per_second}
										<p class="storage-name">{COPY.measure.listed(curve.listed_per_second)}</p>
									{/if}
									{#if curve.achieved_mb_per_second}
										<p class="storage-name">
											{COPY.measure.achieved(curve.achieved_mb_per_second, curve.achieved_percent)}
										</p>
									{/if}
									{#if curve.read_megabytes}
										<p class="storage-name">{COPY.measure.readSince(curve.read_megabytes)}</p>
									{:else if curve.remote}
										<p class="storage-name">{COPY.measure.notReadYet}</p>
									{/if}
								</div>
							</div>
						{/each}
					</div>
				{/if}

				{#if selfTest.finished && selfTest.card && !selfTest.card.failed}
					{@const card = selfTest.card}
					<div class="storages" data-testid="self-test-card">
						<SectionHeading level={3}>{COPY.measure.gpu}</SectionHeading>
						<ul class="storage-levels">
							{#each card.levels as level (level.at_once)}
								<li class:best={level.at_once === card.best_at_once}>
									{COPY.measure.previews(level.at_once, level.per_second)}
								</li>
							{/each}
						</ul>
					</div>
				{/if}

				{#if selfTest.finished && selfTest.models.some((one) => !one.failed)}
					<!-- A model not measured is said in the notes above, so only the measured are listed. -->
					<div class="storages" data-testid="self-test-models">
						<SectionHeading level={3}>{COPY.measure.models}</SectionHeading>
						{#each selfTest.models.filter((one) => !one.failed) as model (model.name)}
							<div class="storage">
								<p class="storage-name">{COPY.measure.modelOn(model.name, model.device)}</p>
								<ul class="storage-levels">
									{#each model.levels as level (level.at_once)}
										<li class:best={level.at_once === model.best_at_once}>
											{COPY.measure.files(level.at_once, level.files_per_second)}
										</li>
									{/each}
								</ul>
								{#if model.seconds_per_file !== null && model.seconds_per_file !== undefined}
									<p class="storage-name">
										{COPY.measure.perFile(
											model.seconds_per_file,
											model.megabytes,
											model.card_megabytes
										)}
									</p>
								{/if}
							</div>
						{/each}
					</div>
				{/if}
			</div>
		</div>
	{/if}

	<!--
		Read-only, and below the controls on purpose: it is the answer to "did that change help", which
		is a question you ask after touching something above.
	-->
	{#if health}
		<KeepingUp {health} {selfTest} />
	{/if}

	<!-- How much Sift does at the same time, and how much less while somebody works. -->
	<Concurrency />

	<!-- LAST, whatever else the pane holds: the one act here that takes something away sits where
	     nobody reaches it on the way to something else. -->
	<GraphicsCardRemove card={gpu} />
</section>

<style>
	/* The measurement's own column, under its heading. */
	.self-test {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

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

	/* Each share's curve, under the suggestions it produced. */
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

	/* No width of its own: the shell caps the content once, for every pane. */
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

	/* A block of this page that is not a `SettingGroup` but reads as one: its heading, then what
	 * it holds. */
	.machine,
	.block {
		margin-block-end: var(--space-8);
	}

	/* The one control on this pane that is an icon and nothing else. */
	/* One row per fact, in the shape every fact on a settings pane has (`FactRow`): the name in
	 * semibold full ink on the left, the value in quieter ink against the right edge, both on
	 * one baseline. */
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

	/* A line only BETWEEN two facts, drawn by the lower one: the last fact draws none, so the
	   next group's heading keeps the only line between the two groups. */
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

	/* At a phone's width a fact stacks as a settings row does: the value under its name,
	   starting where the name starts. */
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

	.verdict.apart {
		margin-block-start: var(--space-4);
	}
</style>
