<script lang="ts">
	/*
	 * The editor, a panel over the file: crop and turn a photograph, or cut a clip, ending in one
	 * name and one Save that carries every step. Every rule is the server's, asked on every change.
	 * Each edit writes a NEW file beside the original.
	 */
	import { ConfirmDialog, Empty, Field, Panel, Problem, TextInput } from '$lib/components/common';
	import ClipPanel from '$lib/components/edit/ClipPanel.svelte';
	import PicturePanel from '$lib/components/edit/PicturePanel.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { spriteUrl } from '$lib/entity/art';
	import {
		frame as askFrame,
		preflight as askServer,
		start as startEditing,
		type EditableAsset,
		type EditRequest,
		type EditStep,
		type EditVerdict
	} from '$lib/edit/edit.svelte';
	import {
		FACING_FORWARD,
		boxMirroredAcross,
		boxMirroredDown,
		boxTurnedLeft,
		boxTurnedRight,
		framed,
		isWhole,
		mirroredAcross,
		mirroredDown,
		turnSteps,
		turnedLeft,
		turnedRight,
		wholeOf,
		type Box,
		type Facing,
		type Frame
	} from '$lib/edit/geometry';
	import { toasts } from '$lib/shell/toasts.svelte';

	interface Props {
		open?: boolean;
		asset: EditableAsset;
		/** `gif` is Create GIF's own door; ignored for a photograph. */
		mode?: 'trim' | 'gif';
		onqueued?: () => void;
	}

	let { open = $bindable(false), asset, mode = 'trim', onqueued }: Props = $props();

	const isStill = $derived(asset.media_type === 'image');
	const runningMs = $derived(asset.duration_ms ?? 0);

	/*
	 * The picture as SEEN, asked when the panel opens: a camera-turned photo is the other way
	 * round.
	 */
	let source = $state<Frame>({ width: 0, height: 0 });
	let facing = $state<Facing>(FACING_FORWARD);
	let box = $state<Box>({ left: 0, top: 0, width: 0, height: 0 });
	let startMs = $state(0);
	let endMs = $state(0);
	let kind = $state<'trim' | 'clip' | 'gif'>('trim');
	/* Null until somebody types, so a name nobody chose is not sent as theirs. */
	let typedName = $state<string | null>(null);

	let answer = $state<EditVerdict | null>(null);
	let asking = $state(false);
	let starting = $state(false);
	let failed = $state<string | null>(null);

	const frame = $derived(framed(source, facing));
	const cropped = $derived(!isWhole(box, frame));

	/* Back to the whole file on every open, never the last photograph's rectangle. */
	$effect(() => {
		if (!open) return;
		const id = asset.id;
		settle({ width: asset.width ?? 0, height: asset.height ?? 0 });
		void askFrame(id)
			.then((seen) => {
				if (id !== asset.id || !seen.width || !seen.height) return;
				// Only when the size differs: settling again would throw away a drawn rectangle.
				if (seen.width === source.width && seen.height === source.height) return;
				settle({ width: seen.width, height: seen.height });
			})
			.catch(() => {
				// The server refuses a rectangle that does not fit, whatever the panel believes.
			});
	});

	function settle(seen: Frame): void {
		source = seen;
		facing = FACING_FORWARD;
		box = wholeOf(seen);
		startMs = 0;
		endMs = runningMs;
		/* `ClipPanel` reads the kind (`animating`). */
		kind = mode;
		typedName = null;
		answer = null;
	}

	/* The rectangle turns with the picture, so it still means the same part. */
	function turn(which: 'left' | 'right' | 'across' | 'down'): void {
		const was = frame;
		if (which === 'right') {
			box = boxTurnedRight(box, was);
			facing = turnedRight(facing);
		} else if (which === 'left') {
			box = boxTurnedLeft(box, was);
			facing = turnedLeft(facing);
		} else if (which === 'across') {
			box = boxMirroredAcross(box, was);
			facing = mirroredAcross(facing);
		} else {
			box = boxMirroredDown(box, was);
			facing = mirroredDown(facing);
		}
	}

	const steps = $derived<EditStep[]>(
		isStill
			? [
					...turnSteps(facing).map((one) => ({ operation: 'rotate' as const, turn: one })),
					...(cropped
						? [
								{
									operation: 'crop' as const,
									left: box.left,
									top: box.top,
									/*
									 * At least one pixel: the shape check refuses a zero before
									 * anything could explain itself.
									 */
									width: Math.max(1, box.width),
									height: Math.max(1, box.height)
								}
							]
						: [])
				]
			: [{ operation: kind, start_ms: startMs, duration_ms: Math.max(1, endMs - startMs) }]
	);

	/* Long enough to cover a drag. */
	const ASK_AFTER = 150;

	/* Nothing done yet: nothing to ask and nothing to save. */
	const nothingYet = $derived(steps.length === 0);

	const request = $derived<EditRequest>({
		steps,
		filename: typedName?.trim() ? typedName.trim() : null,
		// Never from here: saving as a Loop is the player's gesture.
		as_loop: false
	});

	/*
	 * Asked once the question stops changing, or the sheet's height shifts under the drag; the
	 * button dies the moment it changes.
	 */
	$effect(() => {
		if (!open || nothingYet) return;
		const asked = { ...request, steps: [...request.steps] };
		asking = true;
		failed = null;
		const timer = setTimeout(() => {
			void askServer(asset.id, asked)
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
		}, ASK_AFTER);
		return () => clearTimeout(timer);
	});

	const ready = $derived(!asking && !starting && !nothingYet && (answer?.allowed ?? false));

	/* The stem is the person's, the extension the encoding's. */
	const original = $derived(asset.filename ?? '');
	const originalExtension = $derived(
		original.includes('.') ? original.slice(original.lastIndexOf('.')) : ''
	);
	const originalStem = $derived(
		originalExtension ? original.slice(0, -originalExtension.length) : original
	);

	const derivedName = $derived(answer?.output_filename ?? '');
	const extension = $derived(
		derivedName.includes('.') ? derivedName.slice(derivedName.lastIndexOf('.')) : originalExtension
	);
	const suggested = $derived(
		derivedName ? (extension ? derivedName.slice(0, -extension.length) : derivedName) : originalStem
	);
	const stem = $derived(typedName ?? suggested);

	async function confirm(): Promise<void> {
		starting = true;
		try {
			const result = await startEditing(asset.id, request);
			toasts.show(`Saving ${result.output_filename} — it runs in the background`);
			onqueued?.();
			open = false;
		} catch {
			toasts.show("That couldn't be started", { tone: 'error' });
		} finally {
			starting = false;
		}
	}
</script>

<!-- One name box, drawn from the start, so nothing appears under the picture as it is dragged. -->
{#snippet nameField()}
	<Field label="Rename to">
		{#snippet control({ id, describedBy })}
			<div class="naming">
				<TextInput
					{id}
					{describedBy}
					type="text"
					value={stem}
					oninput={(event) => (typedName = event.currentTarget.value)}
				/>
				<span class="extension">{extension}</span>
			</div>
		{/snippet}
	</Field>
{/snippet}

<ConfirmDialog
	bind:open
	title={isStill ? 'Modify this picture' : 'Trim this video'}
	consequence="A new file is saved in the same location. Nothing you already have is changed or replaced."
	confirmLabel={starting ? 'Saving' : 'Save a copy'}
	confirmDisabled={!ready}
	destructive={false}
	onconfirm={() => void confirm()}
>
	{#snippet extra()}
		<div class="panel">
			{#if isStill}
				<PicturePanel
					src={`/api/assets/${asset.id}/stream`}
					{frame}
					{facing}
					bind:box
					onturn={turn}
					under={nameField}
				/>
			{:else}
				<ClipPanel
					src={`/api/assets/${asset.id}/stream`}
					durationMs={runningMs}
					bind:startMs
					bind:endMs
					bind:kind
					under={nameField}
				/>
			{/if}

			<!-- The last answer stays while the next is fetched, at a held height. -->
			<div class="says">
				{#if nothingYet}
					<!-- Nothing yet: the box stays quiet until a change is made. -->
				{:else if asking && !answer}
					<Empty scope="block" busy>Working out what this would do</Empty>
				{:else if failed}
					<!-- A failure, so `Problem`, as in `CompressDialog`. -->
					<Problem message={failed} />
				{:else if answer}
					{#if !answer.allowed && answer.reason}
						<Panel tone="caution" gap="sm">
							<p class="advisory" role="alert">
								<Icon name="warning" />
								{answer.reason}
							</p>
						</Panel>
					{/if}
					{#if answer.allowed && answer.result_width && answer.result_height}
						<p class="quiet">The copy comes out {answer.result_width} by {answer.result_height}.</p>
					{/if}
					{#if answer.converted_from}
						<p class="quiet">
							Sift can read {answer.converted_from.toUpperCase()} and can't write it, so the copy is saved
							as a JPEG.
						</p>
					{/if}
					{#if answer.approximate_start}
						<p class="quiet">
							Nothing is re-encoded, so Sift creates the copy in seconds. It may begin up to a
							moment earlier than you asked, at the last full frame before it. Never later.
						</p>
					{:else if answer.allowed && !isStill}
						<p class="quiet">Nothing is re-encoded, so the copy is made in seconds.</p>
					{:else if answer.allowed && answer.lossy}
						<p class="quiet">Saving in this format compresses the picture again.</p>
					{/if}
				{/if}
			</div>
		</div>
	{/snippet}
</ConfirmDialog>

<style>
	.panel {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	.naming {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	/* `:global`: the box is `TextInput`'s element. */
	.naming :global(.text-input) {
		flex: 1 1 auto;
		min-inline-size: 0;
	}

	.extension {
		color: var(--sift-ink-2);
		font: var(--text-data);
	}

	/* Room for two wrapped lines, held from the start. */
	.says {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		min-block-size: 4.5rem;
	}

	/* Only the sentence, as in `CompressDialog`. */
	.advisory {
		display: flex;
		gap: var(--space-2);
		align-items: flex-start;
		margin: 0;
		font: var(--text-body-sm);
	}
</style>
