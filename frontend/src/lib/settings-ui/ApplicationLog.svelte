<script lang="ts">
	/* LIVE: nothing moves it (log lines are written many times a second and announced by nothing; the pane reads them again whenever the narrowing changes) */
	/* The end of the log, on the Logs tab, under the settings that decide what goes into it. */
	import { onMount } from 'svelte';
	import {
		Button,
		Empty,
		NarrowBox,
		Note,
		Problem,
		Scroller,
		SectionHeading,
		Select,
		Spinner
	} from '$lib/components/common';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import { api, ApiError } from '$lib/api/client';
	import { bridge } from '$lib/bridge';
	import { triggerDownload } from '$lib/capture/copy-out';
	import { copyText } from '$lib/shell/clipboard';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { dayStarts, logTime, stampForAFileName } from '$lib/shell/when';
	import type { components } from '$lib/api/schema';
	import { COPY, LOG_LEVELS, type LogLevel } from './Logs.search';
	import LogLine, { record, rest } from './LogLine.svelte';
	import { mergeLogs, momentOf, type LogSource, type MarkedLine } from './merged-log';
	import {
		offersServer,
		readServerAppLog,
		readServerDesktop,
		type ServerDesktop
	} from '$lib/desktop/server-shell';

	type LogPage = components['schemas']['LogPage'];
	type LogRecord = components['schemas']['LogLine'];

	/** A screenful and some scrollback, matching the route's own default. */
	const LINES = 200;
	/** What the app's own log is narrowed FROM, since the bridge cannot narrow. The route's ceiling. */
	const APP_LINES = 500;

	/* Every level, loudest first and named by the level, each keeping itself and everything
	   worse. */
	type Level = LogLevel;
	const LEVEL_CHOICES = [...LOG_LEVELS]
		.reverse()
		.map((value) => ({ value, label: COPY.levels[value] }));

	/* How long typing waits before the log is asked again. */
	const TYPING_MS = 300;

	/* How long the tick stays on the copy button. */
	const COPIED_FOR = 2000;

	/** What one read of both logs holds: the list, and each log's page for its size and place. */
	interface Shown {
		lines: MarkedLine[];
		library: LogPage;
		app: LogPage | null;
	}

	let level = $state<Level>('debug');
	let words = $state('');
	let shown = $state<Shown | null>(null);
	let loading = $state(false);
	let refused = $state<string | null>(null);
	let copied = $state(false);
	let copyRefused = $state(false);
	let downloading = $state(false);

	/* Whether the app's own log can be asked for at all. */
	const readsHere = bridge.canReadShellLog();
	/* The computer running Sift, where this window cannot read an app's log itself. */
	let desk = $state<ServerDesktop | null>(null);
	const readsThere = $derived(!readsHere && offersServer(desk));
	const canReadApp = $derived(readsHere || readsThere);
	/* The mark the app's lines wear: this device's app, or the one on the computer running Sift. */
	const appName = $derived(readsThere ? COPY.appThere : COPY.app);

	const narrowed = $derived(level !== 'debug' || words.trim() !== '');
	const lines = $derived(shown?.lines ?? []);
	/* The log whose older lines the one span of time left out, if either. */
	const leftOut = $derived.by((): LogSource | null => {
		if (!shown?.app) return null;
		const kept = (from: LogSource) => lines.filter((one) => one.from === from).length;
		if (kept('app') < (shown.app.lines ?? []).length) return 'app';
		if (kept('library') < (shown.library.lines ?? []).length) return 'library';
		return null;
	});

	/** One raw record, in the shape the server's route hands over. */
	const UNPARSED = (raw: string): LogRecord => ({ raw, at: null, level: null, event: null });

	function parsed(raw: string): LogRecord {
		const one = record(raw);
		if (!one) return UNPARSED(raw);
		return {
			raw,
			at: typeof one.timestamp === 'string' ? one.timestamp : null,
			level: typeof one.level === 'string' ? one.level : null,
			event: typeof one.event === 'string' ? one.event : null
		};
	}

	/** The server's `_keeps`, for the one source it cannot reach. See the note at the top. */
	function keeps(raw: string): boolean {
		const floor = LOG_LEVELS.indexOf(level);
		const one = record(raw);
		if (floor > 0) {
			const said = one && typeof one.level === 'string' ? one.level : null;
			const rank = said ? LOG_LEVELS.indexOf(said as LogLevel) : -1;
			if (rank < floor) return false;
		}
		const needle = words.trim().toLocaleLowerCase();
		if (!needle) return true;
		const text = one ? [String(one.event ?? ''), rest(raw)].join('  ') : raw;
		return text.toLocaleLowerCase().includes(needle);
	}

	/** Both logs, narrowed as the screen says, as one list. `most` is how many of each to keep. */
	async function readBoth(most: number): Promise<Shown> {
		const [library, app] = await Promise.all([
			fromTheLibrary(most),
			canReadApp ? fromTheApp(most) : Promise.resolve(null)
		]);
		return {
			library,
			app,
			lines: mergeLogs(
				{ lines: library.lines ?? [], cut: (library.lines ?? []).length >= most },
				app && { lines: app.lines ?? [], cut: (app.lines ?? []).length >= most }
			)
		};
	}

	async function show() {
		loading = true;
		refused = null;
		try {
			shown = await readBoth(LINES);
		} catch (caught) {
			refused = (caught instanceof ApiError && caught.detail) || COPY.cannotLoad;
		} finally {
			loading = false;
		}
	}

	function fromTheLibrary(most: number): Promise<LogPage> {
		return api.get<LogPage>('/logs', {
			query: {
				lines: String(most),
				// Absent rather than "debug": the quietest level narrows nothing, and a request
				// that says so is the unnarrowed read the route answers without a budget.
				level: level === 'debug' ? undefined : level,
				search: words.trim() || undefined
			}
		});
	}

	/** The app's own log, or null where it did not answer: its lines are then simply not there. */
	async function fromTheApp(most: number): Promise<LogPage | null> {
		const found = readsHere ? await bridge.shellLog(APP_LINES) : await readServerAppLog(APP_LINES);
		if (found === null) return null;
		const kept = found.lines.filter(keeps).slice(-most);
		return {
			lines: kept.map(parsed),
			path: found.path,
			size_bytes: found.size,
			present: found.present,
			searched_bytes: 0,
			whole: true
		};
	}

	onMount(() => {
		if (readsHere) {
			void show();
			return;
		}
		// Asked first, so the first read already knows whether there is a Sift app's log to merge.
		void readServerDesktop()
			.then((answer) => (desk = answer))
			.finally(() => void show());
	});

	/* Asked again when the narrowing changes: immediately for the level, after a pause for the
	   words. */
	let typing: ReturnType<typeof setTimeout> | undefined;
	function narrowAgain(now = false) {
		clearTimeout(typing);
		if (now) void show();
		else typing = setTimeout(() => void show(), TYPING_MS);
	}

	/* The lines on screen, as the files hold them, for pasting into a bug report. */
	async function copyShown() {
		copyRefused = false;
		const text = lines.map(({ line }) => line.raw).join('\n');
		if (await copyText(text)) {
			copied = true;
			setTimeout(() => (copied = false), COPIED_FOR);
		} else {
			copyRefused = true;
		}
	}

	function markOf(from: LogSource): string {
		return from === 'library' ? COPY.library : appName;
	}

	/* The app's archive where this shell makes one, else the library's from the server. */
	async function download() {
		downloading = true;
		const name = COPY.fileName(stampForAFileName(new Date()));
		try {
			if (bridge.canSaveLogArchive()) {
				const made = await bridge.saveLogArchive(name);
				if ('reason' in made) {
					toasts.show(COPY.cannotCreate(made.reason), { tone: 'error' });
					return;
				}
				toasts.show(COPY.savedTo(made.file), { tone: 'success' });
				return;
			}
			const archive = await api.get<Blob>('/logs/archive', { asBlob: true });
			const url = URL.createObjectURL(archive);
			triggerDownload(url, name);
			setTimeout(() => URL.revokeObjectURL(url), 0);
			const folder = await bridge.downloadFolder();
			toasts.show(folder ? COPY.savedTo(folder.path) : COPY.savedInBrowser, { tone: 'success' });
		} catch {
			toasts.show(COPY.cannotDownload, { tone: 'error' });
		} finally {
			downloading = false;
		}
	}

	/** The clock time alone, in this device's zone, under the heading of its day (`days`). */
	function at(line: LogRecord): string {
		const moment = momentOf(line);
		return moment === null ? (line.at ?? '') : logTime(moment);
	}

	/* THE DAY, as a heading wherever it changes: "Today", "Yesterday", "Sep 12, 2026". */
	const days = $derived(dayStarts(lines.map(({ line }) => momentOf(line))));

	function size(bytes: number): string {
		if (bytes < 1024) return `${bytes} bytes`;
		if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
		return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
	}
</script>

<div class="log">
	<!-- Said only where there are two logs: what each mark stands for, which machine's file it is.
	     A browser has the library's alone, and its lines wear no mark. -->
	{#if canReadApp}
		<p class="covers">
			{readsThere ? COPY.bothThereCovers(desk?.machine ?? null) : COPY.bothCovers}
		</p>
	{/if}

	<!-- The level and the words are one question asked two ways, so both stand in the row's control
	     column, the words box filling what the level leaves. -->
	<LabelledRow label={COPY.level} help={COPY.levelHelp} wide>
		<div class="asks">
			<Select
				label={COPY.level}
				value={level}
				options={LEVEL_CHOICES}
				onValueChange={(next) => {
					level = next as Level;
					narrowAgain(true);
				}}
			/>
			<div class="words">
				<NarrowBox bind:value={words} label={COPY.narrow} oninput={() => narrowAgain()} />
			</div>
		</div>
	</LabelledRow>

	<div class="doing">
		<Button
			size="small"
			tone="secondary"
			icon="cached"
			onclick={() => narrowAgain(true)}
			disabled={loading}
		>
			{COPY.refresh}
		</Button>
		<Button
			size="small"
			tone="secondary"
			icon="content_copy"
			onclick={() => void copyShown()}
			disabled={lines.length === 0}
		>
			{copied ? COPY.copied : COPY.copy}
		</Button>
		<!-- The button and the line under it are one control: the line says what the file is. -->
		<div class="download">
			<Button
				size="small"
				tone="secondary"
				icon="download"
				onclick={() => void download()}
				busy={downloading}
			>
				{downloading ? COPY.creating : COPY.download}
			</Button>
			<span class="trailer">{COPY.downloadTrailer}</span>
		</div>
		{#if loading}<Spinner size={16} />{/if}
	</div>

	{#if shown}
		<p class="where">
			<span>{COPY.where(COPY.library, size(shown.library.size_bytes), shown.library.path)}</span>
			{#if shown.app}
				<span>{COPY.where(appName, size(shown.app.size_bytes), shown.app.path)}</span>
			{/if}
		</p>
	{/if}

	{#if copyRefused}
		<Note tone="caution">{COPY.blocked}</Note>
	{/if}
	{#if shown && narrowed && !shown.library.whole && (shown.library.lines ?? []).length < LINES}
		<!-- Said, because silence would read as "there are no more". The read stopped looking. -->
		<p class="covers">{COPY.partly(size(shown.library.searched_bytes ?? 0))}</p>
	{/if}
	{#if leftOut}
		<p class="covers">{COPY.olderLeftOut(markOf(leftOut))}</p>
	{/if}
	{#if shown?.app && narrowed}
		<p class="covers">{COPY.appNarrowed(appName)}</p>
	{/if}

	{#if refused}
		<Problem message={refused} />
	{:else if shown && lines.length === 0}
		{#if narrowed}
			<Empty scope="block" icon="checklist">{COPY.noMatch}</Empty>
		{:else}
			<Empty scope="block" icon="checklist">{COPY.empty}</Empty>
		{/if}
	{:else if shown}
		<!-- Bounded and scrolling, because a screenful of log is longer than a settings pane. -->
		<div class="lines">
			<Scroller>
				{#each lines as { line, from }, index (index)}
					{#if days[index]}
						<div class="day"><SectionHeading band level={3}>{days[index]}</SectionHeading></div>
					{/if}
					<LogLine {line} at={at(line)} from={canReadApp ? markOf(from) : undefined} />
				{/each}
			</Scroller>
		</div>
	{/if}
</div>

<style>
	.log {
		display: grid;
		gap: var(--space-3);
		margin-block-end: var(--space-4);
	}

	/* What the list holds, in a sentence: which machine's file each mark stands for, which is
	   the thing that actually confuses people. */
	.covers {
		margin: 0;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	.asks {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	.words {
		flex: 1 1 auto;
		min-inline-size: 0;
	}

	/* The download's line under it makes that control taller than the rest, so the row lines its
	   presses up along their tops and the buttons stay level. */
	.doing {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-start;
		gap: var(--space-3);
	}

	/* The button over the line that says what its file is. */
	.download {
		display: grid;
		justify-items: start;
		gap: var(--space-1);
	}

	.trailer,
	.where {
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* One line for each log's file. */
	.where {
		display: grid;
		gap: var(--space-1);
		margin: 0;
	}

	/* A stated height, because the pane it sits in is itself a scrolling column: a region that
	   grew with its contents would push everything under it off the bottom. */
	.lines {
		/* The band the pinned day takes at the top of the list. */
		--day-band: 2rem;
		block-size: 22rem;
		border: 1px solid var(--sift-line);
		border-radius: var(--radius-md);
		background: var(--sift-surface-2);
	}

	/* A line comes to rest whole, under the pinned day rather than half behind it: the list
	   snaps each line's top to the edge of the band. */
	.lines :global([data-scroll-area-viewport]) {
		scroll-snap-type: y proximity;
		scroll-padding-block-start: var(--day-band);
	}

	/* The day the lines under it were written on: the shared group heading, held at the top
	   while its lines scroll past so the time on every line in view can be read with its day. */
	.day {
		position: sticky;
		z-index: 1;
		inset-block-start: 0;
		display: flex;
		align-items: flex-end;
		box-sizing: border-box;
		min-block-size: var(--day-band);
		padding: var(--space-2) var(--space-3) var(--space-1);
		background: var(--sift-surface-2);
	}
</style>
