<script lang="ts">
	import { counted as grouped } from '$lib/entity/entity-counts';
	/*
	 * What to compress to, asked before any of it is encoded.
	 *
	 * Two things make this different from the other sheets on the bar, and both come from the same
	 * fact: an encode is minutes of somebody's machine per file, and a selection can be forty of
	 * them.
	 *
	 * **It asks the server before it offers a button.** From a file's running time, picture size and
	 * current weight it is arithmetic to know when a target cannot be met at any quality worth
	 * having, and the honest moment to say so is now, not four minutes in, per file. So changing
	 * the target re-asks, and what comes back is per file rather than a summary: the interesting
	 * cases are the three that will not fit and the one that needs nothing done, and a count cannot
	 * point at them.
	 *
	 * **The warning does not refuse.** It says what about the file makes the target impossible and
	 * offers the smallest one that is reachable, and then there is a box to go ahead regardless.
	 * Unticked, always: the person insisting is the point of it existing.
	 *
	 * There is no choice about where the copy goes and no choice about overwriting, because there
	 * is no overwriting: every run writes a new file beside the original, named for the target so
	 * the two are told apart in a file manager. Nothing here can change that, which is why nothing
	 * here asks about it.
	 */
	import {
		Button,
		Checkbox,
		ConfirmDialog,
		Field,
		Empty,
		NumberInput,
		Panel,
		Pressable,
		Problem,
		Scroller,
		Select
	} from '$lib/components/common';
	import Icon from '$lib/components/Icon.svelte';
	import {
		megabytes,
		preflight as askServer,
		sample as askForSample,
		sampleUrl,
		start as startCompressing,
		waitForSample,
		type Preflight,
		type Preset
	} from '$lib/library/compress.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';

	interface Props {
		open?: boolean;
		/** The files the verb was pressed over. */
		assetIds: string[];
		/** Called once the work is queued, so the surface can say so and drop its selection. */
		onqueued?: (started: number) => void;
	}

	let { open = $bindable(false), assetIds, onqueued }: Props = $props();

	/* The named targets, and the one that carries its own number. The names are the server's; the
	   words beside them are this screen's, because the registry does not carry a pretty name. What
	   each name currently MEANS is a setting, which is why no number appears in this list. */
	const PRESETS: { value: Preset; label: string }[] = [
		{ value: 'small', label: 'Small' },
		{ value: 'standard', label: 'Standard' },
		{ value: 'large', label: 'Large' },
		{ value: 'very_large', label: 'Very large' },
		{ value: 'custom', label: 'A size I type' }
	];

	let preset = $state<Preset>('standard');

	/* The chooser hands back a plain string; the request takes one of five. Filtered here rather
	   than cast, so a preset added to the list and forgotten here is caught rather than sent. */
	function choose(value: string): void {
		const known = PRESETS.find((one) => one.value === value);
		if (known) preset = known.value;
	}

	let customMb = $state(25);
	let compatibility = $state(false);
	let force = $state(false);
	let answer = $state<Preflight | null>(null);
	let asking = $state(false);
	let starting = $state(false);
	let failed = $state<string | null>(null);

	/* The sample: a few seconds encoded exactly as the whole file would be.
	 *
	 * A prediction alone is a guess and a sample alone is slow, which is why there are both: the
	 * number is there the moment the sheet opens, and this is for the moment somebody wants to see
	 * what they are about to commit four minutes a file to.
	 *
	 * Offered for one file only. A sample of forty is a sample of whichever one happened to be
	 * first, presented as though it said something about the other thirty-nine. */
	let sampling = $state(false);
	let sampleAt = $state<string | null>(null);
	let sampleFailed = $state(false);

	async function showASample(): Promise<void> {
		sampling = true;
		sampleAt = null;
		sampleFailed = false;
		try {
			const { job_id } = await askForSample(assetIds[0], request);
			sampleFailed = !(await waitForSample(job_id));
			if (!sampleFailed) sampleAt = sampleUrl(job_id);
		} catch {
			sampleFailed = true;
		} finally {
			sampling = false;
		}
	}

	/* The question, as asked of the preflight and of the sample. Never forced: both of those are
	   asking what WOULD happen, and the answer to that is the same either way: the flag only
	   decides whether files Sift has said cannot meet the target are skipped when it runs. The one
	   call that runs it puts the switch's own value over the top of this. */
	const request = $derived({
		asset_ids: assetIds,
		preset,
		custom_target_mb: preset === 'custom' ? customMb : null,
		compatibility,
		force: false
	});

	/* Re-asked whenever the question changes, and only while the sheet is open. Every answer here
	   is the server's, so a stale one is a screen describing a target nobody chose. */
	$effect(() => {
		if (!open || assetIds.length === 0) return;
		const asked = { ...request };
		asking = true;
		failed = null;
		// The sample was of the previous target, so it does not answer the question on screen.
		sampleAt = null;
		sampleFailed = false;
		void askServer(asked)
			.then((result) => {
				answer = result;
			})
			.catch(() => {
				answer = null;
				failed = "Sift couldn't work out what this would do.";
			})
			.finally(() => {
				asking = false;
			});
	});

	/* Reset every time it opens. A sheet that remembers "go ahead anyway" from last time is how
	   somebody forces forty files meaning to force one. */
	$effect(() => {
		if (open) force = false;
	});

	const eligible = $derived(answer?.eligible_count ?? 0);
	const unreachable = $derived(answer?.unreachable_count ?? 0);
	const nothingToDo = $derived(answer?.copy_only_count ?? 0);
	const blocked = $derived(answer?.files.filter((file) => file.skip_reason) ?? []);

	/* How many files pressing the button actually encodes, which is not how many were selected and
	   is not how many are eligible either.
	 *
	 * A file that already meets the target is eligible and is still not going to be touched: the
	 * server skips it rather than spending four minutes making it worse, so counting it here puts
	 * a number on the button that the toast afterwards contradicts. Unreachable files come off too
	 * unless somebody has ticked the box that says go ahead regardless. The two sets never overlap:
	 * a file that already fits is by definition one the target was reached for. */
	const willRun = $derived(eligible - nothingToDo - (force ? 0 : unreachable));
	const ready = $derived(!asking && willRun > 0);

	function counted(many: number): string {
		return many === 1 ? '1 file' : `${grouped(many)} files`;
	}

	async function confirm(): Promise<void> {
		starting = true;
		try {
			const result = await startCompressing({ ...request, force });
			toasts.show(
				result.started === 0
					? 'Nothing was queued'
					: `Compressing ${counted(result.started)} — it runs in the background`
			);
			onqueued?.(result.started);
		} catch {
			toasts.show("That couldn't be started", { tone: 'error' });
		} finally {
			starting = false;
		}
	}
</script>

<ConfirmDialog
	bind:open
	title="Compress {counted(assetIds.length)}?"
	consequence="Each one is copied smaller, beside the original. Nothing you already have is changed or replaced."
	confirmLabel={starting
		? 'Starting'
		: willRun === 0
			? 'Nothing to compress'
			: `Compress ${counted(willRun)}`}
	confirmDisabled={!ready || starting}
	destructive={false}
	onconfirm={() => void confirm()}
>
	{#snippet extra()}
		<div class="panel">
			<Field label="Target size">
				{#snippet control({ id, describedBy })}
					<Select {id} {describedBy} value={preset} options={PRESETS} onValueChange={choose} />
				{/snippet}
			</Field>

			{#if preset === 'custom'}
				<Field label="Megabytes" help="The size each copy is worked down to.">
					{#snippet control({ id, describedBy })}
						<!-- `NumberInput`, not a bare `type="number"`: that draws the operating system's
						     own stepper arrows, in its look, at a size the page has no say over. -->
						<NumberInput
							{id}
							{describedBy}
							value={customMb}
							min={1}
							unit="MB"
							onchange={(next) => (customMb = next)}
						/>
					{/snippet}
				</Field>
			{/if}

			<!-- The whole row is the control: a 16-pixel box beside two lines of text is a thing to
			     aim at rather than press. So `Checkbox` is drawn here as a mark (a picture of the
			     state, hidden from a screen reader) inside the row that really is the button, and
			     the words name it once. -->
			<Pressable
				class="tick"
				feedback="wash"
				radius="md"
				aria-pressed={compatibility}
				onclick={() => (compatibility = !compatibility)}
			>
				<Checkbox state={compatibility ? 'on' : 'off'} mark />
				<span>
					<strong>Play it anywhere</strong>
					<small>
						A widely-supported format. Where the file is already one, this only rewraps it — quick,
						and nothing is re-encoded.
					</small>
				</span>
			</Pressable>

			{#if assetIds.length === 1 && !asking && answer && willRun > 0}
				<div class="sample">
					<Button onclick={() => void showASample()} disabled={sampling}>
						{sampling ? 'Encoding a few seconds' : 'Show me a few seconds'}
					</Button>
					{#if sampleAt}
						<!-- svelte-ignore a11y_media_has_caption -->
						<video src={sampleAt} controls autoplay muted></video>
					{:else if sampleFailed}
						<Problem message="That sample couldn't be made." />
					{/if}
				</div>
			{/if}

			{#if asking}
				<Empty scope="block" busy>Working out what this would do</Empty>
			{:else if failed}
				<!-- A failure, so `Problem`: the one line a screen shows when something went wrong,
				     announced once wherever it is drawn, and a different object from the caution
				     box below. -->
				<Problem message={failed} />
			{:else if answer}
				{#if answer.copy_only_count > 0}
					<p class="quiet">
						{counted(answer.copy_only_count)} already meet this, so nothing will be done to them.
					</p>
				{/if}

				{#if answer.audio_conversion_count > 0}
					<p class="quiet">
						The sound on {counted(answer.audio_conversion_count)} can't travel in that format, so it's
						converted. Everywhere else the sound is copied untouched.
					</p>
				{/if}

				{#if unreachable > 0}
					<!-- The box is `Panel`'s caution tone, and the sentence keeps the announcement:
					     this arrives on its own when the estimate lands, so somebody who cannot see it
					     has to be told. The ROLE is on the sentence rather than on the box, because
					     what is announced is the sentence: a region role on the panel would have a
					     screen reader read the list of files out with it. -->
					<Panel tone="caution" gap="sm">
						<div class="advisory">
							<p role="alert">
								<Icon name="warning" />
								<strong>{counted(unreachable)} can't be made that small.</strong>
							</p>
							<Scroller>
								<ul>
									{#each answer.files.filter((file) => !file.reachable && !file.skip_reason) as file (file.asset_id)}
										<li>
											<span class="name">{file.filename ?? 'This file'}</span>
											<span>{file.reason}</span>
										</li>
									{/each}
								</ul>
							</Scroller>
							{#if answer.suggested_target_bytes}
								<p>
									The smallest every one of them could meet is about
									{megabytes(answer.suggested_target_bytes)}.
								</p>
							{/if}
							<!-- The row is the control, for the reason given at the first tick above. -->
							<Pressable
								class="tick"
								feedback="wash"
								radius="md"
								aria-pressed={force}
								onclick={() => (force = !force)}
							>
								<Checkbox state={force ? 'on' : 'off'} mark />
								<span>Compress anyway, as small as Sift can make each file</span>
							</Pressable>
						</div>
					</Panel>
				{/if}

				{#if blocked.length > 0}
					<div class="quiet capped-list">
						<p>{counted(blocked.length)} will be left alone:</p>
						<Scroller>
							<ul>
								{#each blocked as file (file.asset_id)}
									<li>
										<span class="name">{file.filename ?? 'A file'}</span>
										<span>{file.skip_reason}</span>
									</li>
								{/each}
							</ul>
						</Scroller>
					</div>
				{/if}

				{#if willRun > 0}
					<p class="quiet">
						{#if answer.files.length === 1 && answer.files[0].predicted_bytes}
							About {megabytes(answer.files[0].predicted_bytes)}, estimated before encoding.
						{:else}
							{counted(willRun)} will be compressed.
						{/if}
					</p>
				{/if}
			{/if}
		</div>
	{/snippet}
</ConfirmDialog>

<style>
	.panel {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	/* `:global`, because the class is handed to `Pressable` and lands on its element. From `.panel`,
	   so it reaches this sheet's rows and no other file's `.tick` (a chart's axis label is one), and
	   so it outranks `Pressable`'s own `display: block`. */
	.panel :global(.tick) {
		display: flex;
		gap: var(--space-2);
		align-items: flex-start;
		text-align: start;
	}

	.panel :global(.tick small) {
		display: block;
		color: var(--sift-ink-2);
	}

	.sample {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.sample video {
		width: 100%;
		max-height: 14rem;
		border-radius: var(--radius-lg);
		background: var(--sift-surface-3);
	}

	/* What is inside the caution panel, and nothing about the panel itself: the ground, the edge,
	   the corner and the inset are `Panel tone="caution"`'s. This is the column the parts stand
	   in and the size they are read at. */
	.advisory {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		font: var(--text-body-sm);
	}

	/* The cap belongs on the box that SCROLLS, not on the list inside it: a list told it may not
	   grow simply clips, and the shared region has no height to be bounded by. Named for what it
	   does rather than left on `.quiet`, whose ink and face are the utility's. */
	.capped-list :global(.scroll-root) {
		max-block-size: 12rem;
	}

	ul {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		margin: 0;
		padding-left: var(--space-5);
	}

	li {
		display: flex;
		flex-direction: column;
	}

	.name {
		font-weight: 600;
	}
</style>
