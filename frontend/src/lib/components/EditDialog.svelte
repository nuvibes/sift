<script lang="ts">
	/*
	 * The editor: a panel over the file being looked at, not a screen of its own.
	 *
	 * Two panels and no tab strip, because it is one picture and a few tools. A photograph is
	 * cropped by dragging the picture itself, and turned or mirrored by four buttons. A
	 * video has one timeline with two handles. Both end at the same place: a name for the copy, and
	 * one Save.
	 *
	 * **One Save may carry several things.** The steps go to the server together, are refused or
	 * allowed together, and produce one file. Sent one at a time, cropping and then turning would
	 * leave two copies on the disk and the first of them is one nobody wanted.
	 *
	 * **Nothing here decides anything.** Whether the rectangle fits, whether that width is bigger
	 * than the picture, whether the clip runs past the end, what the copy will be called and what
	 * format it comes back in are all asked of the server every time something changes. The browser
	 * holds a copy of the answer and draws it; it does not work one out. A second implementation of
	 * the rules would be a second answer waiting to disagree with the first, and the disagreement
	 * would surface as a button that does nothing.
	 *
	 * There is no choice about where the copy goes and none about overwriting, because there is no
	 * overwriting: every edit writes a NEW file beside the original.
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
		/**
		 * Which answer the panel opens on, for a clip.
		 *
		 * `gif` is what the Create GIF row hands in, so making a GIF has its own door. The
		 * panel is the same either way; this only decides which answer it is already on when it
		 * opens. Ignored for a photograph, which has no stretch of time to animate.
		 */
		mode?: 'trim' | 'gif';
		/** Called once the work is queued, so the surface can say so. */
		onqueued?: () => void;
	}

	let { open = $bindable(false), asset, mode = 'trim', onqueued }: Props = $props();

	const isStill = $derived(asset.media_type === 'image');
	const runningMs = $derived(asset.duration_ms ?? 0);

	/*
	 * The picture as it is SEEN, which is the size Sift recorded for almost every file and the
	 * other way round for a photograph a camera turned. Asked of the server when the panel opens:
	 * the browser draws the turned picture, so the two have to agree or the rectangle is dragged
	 * over one picture and cut out of another.
	 */
	let source = $state<Frame>({ width: 0, height: 0 });
	let facing = $state<Facing>(FACING_FORWARD);
	let box = $state<Box>({ left: 0, top: 0, width: 0, height: 0 });
	/* The cut, in milliseconds. Two moments rather than a start and a length, because two handles
	   is what is on screen: the length is worked out from them on the way out. */
	let startMs = $state(0);
	let endMs = $state(0);
	let kind = $state<'trim' | 'clip' | 'gif'>('trim');
	/* What was typed over the derived name, or null while nobody has. Null rather than the derived
	   name copied in, so a name nobody chose is not sent as though they had. */
	let typedName = $state<string | null>(null);

	let answer = $state<EditVerdict | null>(null);
	let asking = $state(false);
	let starting = $state(false);
	let failed = $state<string | null>(null);

	/** The frame the rectangle lives in, which is the source put whichever way round it is now. */
	const frame = $derived(framed(source, facing));
	const cropped = $derived(!isWhole(box, frame));

	/* Everything goes back to the whole file each time it opens. A panel that remembers the last
	   rectangle is one that crops the NEXT photograph to a rectangle drawn on a different one. */
	$effect(() => {
		if (!open) return;
		const id = asset.id;
		// The recorded size to begin with, so there is something to draw, and the real one the
		// moment it arrives. They differ only for a photograph a camera turned.
		settle({ width: asset.width ?? 0, height: asset.height ?? 0 });
		void askFrame(id)
			.then((seen) => {
				if (id !== asset.id || !seen.width || !seen.height) return;
				// Only when it is a different size, which is the case this exists for. Settling
				// again on the same numbers would throw away a rectangle somebody had already
				// drawn, and reading a big photograph can take long enough for that to be one.
				if (seen.width === source.width && seen.height === source.height) return;
				settle({ width: seen.width, height: seen.height });
			})
			.catch(() => {
				// Nothing to do about it here. The recorded size is what the rest of Sift uses, and
				// the server refuses a rectangle that does not fit whatever the panel believes.
			});
	});

	/** Back to the whole picture, at this size. */
	function settle(seen: Frame): void {
		source = seen;
		facing = FACING_FORWARD;
		box = wholeOf(seen);
		startMs = 0;
		endMs = runningMs;
		/* Whatever the caller opened it on. `ClipPanel` reads the kind rather than holding a second
		   answer beside it (see `animating` there) so setting it here is the whole of what
		   opening straight into the GIF takes. */
		kind = mode;
		typedName = null;
		answer = null;
	}

	/*
	 * The four buttons, and the rectangle travelling with the picture.
	 *
	 * Somebody who has drawn a rectangle and then turns the picture means the same part of it,
	 * turned. Left where its numbers were, a rectangle down the left edge of a landscape photograph
	 * ends up across the top of a portrait one, over something else entirely.
	 */
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

	/** Everything that will be done, in the order it will be done in. */
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
									/* At least one pixel, always. The first moment of a drag is a
									   rectangle of nothing, and a request carrying a zero is refused by
									   the shape check before it reaches anything that could explain
									   itself, so the panel would spend every drag collecting rejections
									   it could not read. Sent as one pixel, the server answers the way it
									   answers any other rectangle too small to keep something. */
									width: Math.max(1, box.width),
									height: Math.max(1, box.height)
								}
							]
						: [])
				]
			: [{ operation: kind, start_ms: startMs, duration_ms: Math.max(1, endMs - startMs) }]
	);

	/* How long the rectangle has to hold still before the server is asked about it. Long enough to
	   cover a drag, short enough that letting go feels like an answer arriving at once. */
	const ASK_AFTER = 150;

	/* A photograph with nothing done to it yet. There is nothing to ask about and nothing to save:
	   a request has to name at least one thing to do, and inventing one to ask with would name a
	   file after an edit nobody made. */
	const nothingYet = $derived(steps.length === 0);

	const request = $derived<EditRequest>({
		steps,
		filename: typedName?.trim() ? typedName.trim() : null,
		// Never from here. Saving a copy out of the editor makes a file; putting a row on the Loops
		// screen is the player's gesture, and it says so with its own word.
		as_loop: false
	});

	/*
	 * Re-asked when the question stops changing, rather than on every frame of a drag.
	 *
	 * A drag asks thirty or forty times a second, and every answer rewrites the lines under the
	 * picture, which changes the sheet's height and moves the picture under the dragging pointer.
	 * Waiting for a pause keeps a drag steady and asks the server once for the rectangle.
	 *
	 * The button goes dead the moment the rectangle changes, not when the answer starts being
	 * fetched: between the two, the answer on screen is about a rectangle nobody is looking at.
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

	/* The name, split where the copy's own extension begins. The stem is the person's and the
	   extension is not: it is decided by what the copy is encoded as, and a perfectly good picture
	   named `.txt` cannot be opened by name. */
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
	/* The server's name once there is one, and the file's own until then. Never nothing: a box that
	   is empty until the first drag reads as broken rather than as waiting. */
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

<!-- One name box, wherever the panel puts it. Drawn from the moment the panel opens rather than
     once there is something to name: the sheet is centred, so anything that appears underneath
     the picture moves the picture, and the first thing anybody does is drag it. It opens filled
     in with the file's own name and takes the derived one as soon as there is an edit. -->
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

			<!-- The last answer stays on screen while the next one is being fetched, and only a panel
			     that has never had one says it is working. Swapping these lines out and back would make
			     the sheet grow and shrink on every movement of a drag, moving the picture under the
			     pointer, which is also why the block holds its height whatever is in it. -->
			<div class="says">
				{#if nothingYet}
					<!-- Nothing yet: the box stays quiet until a change is made. -->
				{:else if asking && !answer}
					<Empty scope="block" busy>Working out what this would do</Empty>
				{:else if failed}
					<!-- A failure, so `Problem`. See the same line in `CompressDialog`: a failure and
					     a caution are two different boxes. -->
					<Problem message={failed} />
				{:else if answer}
					{#if !answer.allowed && answer.reason}
						<!-- A caution, so the panel's caution tone. The announcement stays on the
						     sentence: it is what changes as the picture is dragged. -->
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

	/* The extension sits against the box rather than inside it, so it is plainly not something to
	   type over. */
	.naming {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	/* `:global`, because the box is `TextInput`'s element and is compiled in that file's scope. What
	   is set here is only how it shares the row with the extension beside it. */
	.naming :global(.text-input) {
		flex: 1 1 auto;
		min-inline-size: 0;
	}

	.extension {
		color: var(--sift-ink-2);
		font: var(--text-data);
	}

	/*
	 * Room for what the answer will say, held from the start: the tallest ordinary line is two
	 * wrapped lines, and a sheet that changes height when an answer lands moves the picture out
	 * from under whatever is dragging on it.
	 */
	.says {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		min-block-size: 4.5rem;
	}

	/*
	 * The sentence inside the caution panel, and nothing about the panel: see the same rule in
	 * `CompressDialog`. The mark sits on the sentence's own row, as `Note` arranges it.
	 */
	.advisory {
		display: flex;
		gap: var(--space-2);
		align-items: flex-start;
		margin: 0;
		font: var(--text-body-sm);
	}
</style>
