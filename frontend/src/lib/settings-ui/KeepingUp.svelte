<script lang="ts">
	/* Is Sift keeping up: the four `/health` readings, and the figures behind them. */
	import type { ServerHealth } from '$lib/shell/health';
	import type { SelfTest } from '$lib/shell/selftest';
	import Button from '$lib/components/common/Button.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import SectionHeading from '$lib/components/common/SectionHeading.svelte';
	import Fold from '$lib/components/common/Fold.svelte';
	import { copyText } from '$lib/shell/clipboard';
	import { COPY, KEEPING_UP_ANCHOR } from './Performance.search';

	let { health, selfTest }: { health: ServerHealth; selfTest: SelfTest | null } = $props();

	/* How long the tick stays before the button goes back to offering the copy. */
	const COPIED_FOR = 2000;

	/* Above this many rows in one read, the number is drawn as a problem. */
	const WIDE_READ_ROWS = 500;
	/* Above this, a pause is long enough that somebody watching a video would see it. */
	const NOTICEABLE_SECONDS = 0.25;

	/* Tenths at most: this answers "did Sift stop", not by how many milliseconds. */
	function readablePause(seconds: number): string {
		if (seconds < 0.1) return COPY.pause.tiny;
		return COPY.pause.seconds(seconds.toFixed(1));
	}

	/* Whole numbers: the figure only has to read as far too large. */
	function roundedMicroseconds(measured: number): string {
		return Math.round(measured).toLocaleString();
	}

	/* IS SIFT KEEPING UP: one sentence from the four `/health` readings (`kernel/diagnostics`),
	 * which are behind Details. */
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
				times: Math.max(0, health.loop.heldCount - (selfTest?.held_while_measuring ?? 0)),
				now: null
			}
		];
		if (health.threads) {
			found.push({
				kind: 'threads',
				name: COPY.keepingUp.threads,
				longest: health.threads.worstWaitSeconds,
				times: Math.max(0, health.threads.fullCount - (selfTest?.full_while_measuring ?? 0)),
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
		const caused = (selfTest?.held_while_measuring ?? 0) + (selfTest?.full_while_measuring ?? 0);
		const measuring =
			caused > 0 ? ` ${COPY.keepingUp.measuring(COPY.keepingUp.times(caused))}` : '';
		if (felt.length === 0) return `${COPY.keepingUp.yes}${measuring}`;
		const worst = felt.reduce((most, one) => (one.longest > most.longest ? one : most));
		return `${COPY.keepingUp.was[worst.kind](
			COPY.keepingUp.times(worst.times),
			readablePause(worst.longest)
		)}${measuring}`;
	});

	/* The figures behind the fold, as text somebody pastes into a bug report. */
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
								<td class:bad={one.longest >= NOTICEABLE_SECONDS}>{readablePause(one.longest)}</td>
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
						<!-- Not a wait: the reason the database waits are the shape they are. -->
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

	/* The machine notes above. Quieter than a problem and not red: nothing is wrong. */
	.note {
		margin: 0;
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
	}

	/* The answer and what is folded under it, as one column under the block's heading. */
	.keeping-up {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.block {
		margin-block-end: var(--space-8);
	}

	.verdict {
		margin: 0;
		color: var(--sift-ink-2);
		font: var(--text-body);
	}

	/* The answer, marked when something is waiting at this moment. */
	.verdict.bad {
		color: var(--sift-warn);
	}

	/* The column inside the fold, rather than a flex `<details>`: a disclosure laid out as a
	   flex box is drawn differently from one browser to the next. */
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
