<script lang="ts">
	/* LIVE: nothing moves it (the pictures to choose from, read each time the sheet opens and dropped when it closes) */
	/*
	 * Choosing the picture a person, a site or a tag is shown with.
	 *
	 * ## Why this exists when the cover could already be set
	 *
	 * It could, by right-clicking a file on that entity's Files tab and finding "Use as the
	 * cover" in the menu. That is a real way to do it and it is not a way anybody finds. The
	 * picture is on the page, at the top, and the gesture for changing a thing you are looking at
	 * is to press it. So the pencil on the cover opens this, and this offers the same files the
	 * menu would have.
	 *
	 * ## One page of them, newest first
	 *
	 * A cover is nearly always something recent, and a chooser that pages is a chooser somebody
	 * browses instead of choosing from. If the right still is not here, the menu on the Files tab
	 * still reaches every one of them: that path is not replaced, and both write the same field
	 * through the same route.
	 *
	 * ## Two steps, and only for a clip
	 *
	 * A photograph has one frame, so choosing the file finishes it. A video does not: the picture a
	 * file is drawn as is cut from its FIRST FRAME (`media_jobs.jobs.thumbnail` passes
	 * `timestamp_ms=0` and says why) so a clip that opens on black or on a title card is drawn as
	 * black, or as the title card. That is the whole reason this second step exists, and the answer
	 * is stored on the entity rather than on the file: two things can be drawn as two different
	 * moments of one clip.
	 *
	 * ## Which moment it opens on, and why it is zero
	 *
	 * The moment the tile you just pressed was taken at, not halfway through. The middle is past
	 * the titles, which is true and is the wrong thing to optimise: pressing a picture and being
	 * shown a DIFFERENT picture reads as the control being
	 * broken, and the frames come from a sprite sheet whose tiles are small, so the substitute
	 * arrives blurry as well as unexpected. Starting where the tile started means the first thing
	 * you see is the thing you pressed, and moving is then a deliberate act rather than an undo.
	 *
	 * The frames come from the strip the player already scrubs against: one picture holding every
	 * frame, built when the file was imported. So dragging costs no requests at all and nothing is
	 * seeked, and the same two functions decide which tile to show here as decide it under the
	 * player's own scrubber, rather than a second reading of the same sheet.
	 *
	 * ## And a last step for every picture: how it sits in the box
	 *
	 * Whatever was pressed (a photograph, a clip's own picture, a moment of it), the sheet then
	 * shows it whole with the cover's window over it (`CoverFramer`), so the zoom and position are
	 * chosen before Save rather than discovered on the card afterwards. The window is the picture
	 * editor's own crop rectangle (`edit/CropStage`), held to the cover's shape: the same control
	 * Modify draws over a photograph, so framing a cover is a thing already learned. The window opens in the
	 * middle at its largest, which is exactly what the box would show with no frame, and a window
	 * left there is saved as none: pressing straight through saves the cover with no frame at
	 * all. The same editor reframes the cover already chosen, from the
	 * page header (`EntityHeader`), so there is one way to frame a cover.
	 *
	 * ## What it does NOT offer
	 *
	 * A picture from a stash-box. That is a different kind of thing (fetched from somewhere else
	 * rather than pointed at a file in this library) and it is offered where it comes from, in
	 * the look-up sheet, beside the fields from the same entry. Two ways in from two places, and
	 * neither can overwrite the other, because they are not the same field.
	 */
	import {
		Button,
		ChooseFile,
		Empty,
		Modal,
		Pressable,
		Problem,
		Slider
	} from '$lib/components/common';
	import { api, ApiError } from '$lib/api/client';
	import { spriteUrl, thumbUrl } from '$lib/entity/art';
	import { COVER_RATIO, isCentred, type Frame } from '$lib/entity/cover-frame';
	import CoverFramer from '$lib/components/entity/CoverFramer.svelte';
	import { frameAt, frameBox, usable, type SpriteSheet } from '$lib/player/trickplay';
	import { clock } from '$lib/edit/edit.svelte';
	import type { components } from '$lib/api/schema';

	interface Props {
		open?: boolean;
		/** What this entity is called, for the sheet's own heading. */
		name: string;
		/** The query naming this entity's files: the same one its Files tab uses. */
		query: Record<string, unknown>;
		/** The file currently used, so it can be marked rather than offered again. */
		current?: string | null;
		/**
		 * Chosen. The page writes it, because the page knows which route sets its own cover.
		 *
		 * The moment is null for a photograph and for "just use the file's own picture", which are
		 * the same instruction as far as the server is concerned: no moment stored, so the still it
		 * already has is the one that is served.
		 */
		onpick: (assetId: string, atMs: number | null, frame: Frame | null) => Promise<void>;
		/**
		 * A picture from OUTSIDE the library, chosen from the disk.
		 *
		 * The other half of the question. This sheet answers "which of my files", and this answers
		 * "this photograph of her, which is not one of my files". The page writes
		 * it, because the page knows which route its own kind of thing posts to.
		 *
		 * Optional, so a caller that has not been given a route simply does not offer the control,
		 * rather than offering one that fails.
		 */
		onupload?: (file: File) => Promise<void>;
	}

	let { open = $bindable(false), name, query, current = null, onpick, onupload }: Props = $props();

	/** How many are offered. Enough to recognise the one you meant; not a wall to browse. */
	const HOW_MANY = 60;

	type Row = Pick<components['schemas']['AssetSummary'], 'id' | 'media_type' | 'duration_ms'>;

	/** The clip a moment is being picked from, once one has been pressed. */
	type Framing = { id: string; durationMs: number; sheet: SpriteSheet | null; art?: string | null };

	let items = $state<Row[]>([]);
	let loading = $state(false);
	let problem = $state<string | undefined>();
	let saving = $state('');

	/** The clip a moment is being chosen from, or null while the wall of files is showing. */
	let framing = $state<Framing | null>(null);

	/**
	 * The last step: the picture chosen, and the window of it the cover will be drawn as.
	 *
	 * `src` is the picture by address: a photograph's or a clip's own still. A MOVED moment has
	 * no address of its own yet (its still is rendered after the choice is saved), so it is drawn
	 * as its tile of the clip's strip instead, and `aspect` is that tile's shape.
	 */
	type Fitting = {
		id: string;
		atMs: number | null;
		src?: string;
		tile?: { sheet: SpriteSheet; art?: string | null; index: number; aspect: number };
	};
	let fitting = $state<Fitting | null>(null);
	let fitFrame = $state<Frame | null>(null);
	let fitAspect = $state<number | null>(null);

	function fit(next: Fitting) {
		fitFrame = null;
		fitAspect = next.tile?.aspect ?? null;
		fitting = next;
	}

	/** What is saved: the window, or none where it was left in the middle at its largest. */
	function chosenFrame(): Frame | null {
		if (!fitFrame || !fitAspect) return null;
		return isCentred(fitFrame, fitAspect, COVER_RATIO) ? null : fitFrame;
	}
	/** Which moment, in milliseconds. Starts where the file's own picture was taken, which is zero. */
	let atMs = $state(0);
	/**
	 * Whether the moment has been moved yet.
	 *
	 * Until it has, the picture shown is the file's OWN still: the exact image on the tile that
	 * was pressed, at full thumbnail resolution. A sprite tile is a small cell of a large sheet and
	 * is meant for scrubbing, so opening on one shows a soft version of a picture that already
	 * exists sharp. Once somebody drags, the sheet is the only thing that can answer without a
	 * request per frame, and it is what the player's own scrubber shows.
	 */
	let moved = $state(false);

	/* The frame to draw, worked out by the SAME two functions that decide it under the player's
	   scrubber. A second reading of one sheet is how two views of one file come to disagree. */
	const FRAME_WIDTH = 320;
	let sheetSize = $state({ width: 0, height: 0 });
	const box = $derived.by(() => {
		const sheet = framing?.sheet;
		if (!sheet || sheetSize.width === 0) return null;
		const seconds = atMs / 1000;
		const duration = (framing?.durationMs ?? 0) / 1000;
		return frameBox(sheet, frameAt(sheet, seconds, duration), FRAME_WIDTH, sheetSize);
	});

	function measure(event: Event) {
		const image = event.currentTarget as HTMLImageElement;
		sheetSize = { width: image.naturalWidth, height: image.naturalHeight };
	}

	/* Closing the sheet leaves the wall of files showing, not a half-answered question about a clip
	   nobody picked. Without this, re-opening it would land straight back in the second step. */
	$effect(() => {
		if (!open) {
			framing = null;
			fitting = null;
		}
	});

	/* Loaded when the sheet OPENS, not when the component mounts.
	 *
	 * The header holds one of these on every entity page, so mounting-time loading would be a
	 * request for sixty files on every person anybody looks at, for a sheet almost nobody opens.
	 */
	$effect(() => {
		if (open) void load();
	});

	async function load() {
		loading = true;
		problem = undefined;
		try {
			/*
			 * The listing answers with the assets themselves, not a wrapper carrying one; reading
			 * `one.asset` would find nothing on every row and draw an empty chooser.
			 */
			const answer = await api.get<components['schemas']['AssetPageResponse']>('/assets', {
				query: { ...query, limit: HOW_MANY, sort: 'newest' }
			});
			items = (answer.items ?? []).filter((one) => one?.id);
		} catch {
			problem = "Those couldn't be read.";
			items = [];
		} finally {
			loading = false;
		}
	}

	/*
	 * Pressing a file: a photograph is the answer, a clip is a question.
	 *
	 * The strip of frames lives on the file's own record rather than in the listing, so it is
	 * fetched here: one request, when somebody actually picks a clip, rather than sixty on the
	 * chance that they will. A clip whose strip has not been built yet still works: the moment is
	 * chosen against the clock with no frames to look at, which is worse than the picture and much
	 * better than refusing.
	 */
	async function press(one: Row) {
		if (saving || framing) return;
		if (one.media_type !== 'video' || !one.duration_ms) {
			fit({ id: one.id, atMs: null, src: `/api/assets/${encodeURIComponent(one.id)}/thumb` });
			return;
		}
		saving = one.id;
		problem = undefined;
		try {
			const detail = await api.get<components['schemas']['AssetDetail']>(
				`/assets/${encodeURIComponent(one.id)}`
			);
			framing = {
				id: one.id,
				durationMs: one.duration_ms,
				sheet: usable(detail.sprite) ? detail.sprite : null,
				art: detail.art
			};
			// Where the file's own picture was taken. See the note at the top of this file.
			atMs = 0;
			moved = false;
		} catch {
			problem = "That clip couldn't be read.";
		} finally {
			saving = '';
		}
	}

	/* A picture from the disk. The same three outcomes the pick has, said the same way.
	 *
	 * The server's OWN words on a refusal, which is the one place in this sheet that is true: it is
	 * the half that knows why (too big, or not readable as a picture) and "that could not be
	 * used" over a file somebody just chose is the sentence that leaves them pressing it again.
	 */
	async function upload(file: File) {
		if (saving || !onupload) return;
		saving = 'upload';
		problem = undefined;
		try {
			await onupload(file);
			open = false;
			framing = null;
		} catch (error) {
			problem =
				error instanceof ApiError && error.detail ? error.detail : "That picture couldn't be used.";
		} finally {
			saving = '';
		}
	}

	/* From the moment step to the framing step: the clip's own still where the moment was not moved
	   (that IS the still: see the note on the unmoved slider below), its tile of the strip where it
	   was. */
	function fitClip(clip: Framing, moment: number | null) {
		if (moment === null || !clip.sheet || sheetSize.width === 0) {
			fit({ id: clip.id, atMs: moment, src: thumbUrl({ id: clip.id, art: clip.art }) });
			return;
		}
		const tileWidth = sheetSize.width / clip.sheet.columns;
		const tileHeight = sheetSize.height / clip.sheet.rows;
		fit({
			id: clip.id,
			atMs: moment,
			tile: {
				sheet: clip.sheet,
				art: clip.art,
				index: frameAt(clip.sheet, moment / 1000, clip.durationMs / 1000),
				aspect: tileWidth / tileHeight
			}
		});
	}

	async function choose(assetId: string, moment: number | null, frame: Frame | null = null) {
		if (saving) return;
		saving = assetId;
		problem = undefined;
		try {
			await onpick(assetId, moment, frame);
			open = false;
			framing = null;
			fitting = null;
		} catch (error) {
			/* A 404 here is not the same event as a refusal, and one sentence for both would make
			   this undiagnosable. The chooser lists what the library held a moment ago; a file
			   removed since is offered, drawn as a blank tile, and answers 404 to every write. Say
			   which it was, and drop the row so it cannot be pressed a second time. */
			if (error instanceof ApiError && error.status === 404) {
				problem = "That file isn't in your library any more.";
				items = items.filter((one) => one.id !== assetId);
				framing = null;
				fitting = null;
			} else {
				problem = "That couldn't be used as the picture.";
			}
		} finally {
			saving = '';
		}
	}
</script>

<Modal
	bind:open
	title="The picture for {name}"
	description="Pick one of their own files. The one in use is marked."
	onback={fitting ? () => (fitting = null) : framing ? () => (framing = null) : undefined}
>
	{#snippet children()}
		<Problem message={problem} />
		{#if fitting}
			{@const chosen = fitting}
			<!-- The picture whole, and the window of it the cover will be drawn as. Keyed on the
			     picture, so choosing another starts a new window rather than carrying this one over. -->
			{#key `${chosen.id}:${chosen.atMs}`}
				{#if chosen.tile}
					{@const tile = chosen.tile}
					<CoverFramer bind:frame={fitFrame} aspect={tile.aspect} ratio={COVER_RATIO}>
						{#snippet picture(size)}
							{@const cell = frameBox(tile.sheet, tile.index, size.width, sheetSize)}
							<figure
								class="tile"
								style:width="{cell.width}px"
								style:height="{cell.height}px"
								style:background-image="url({spriteUrl({ id: chosen.id, art: tile.art })})"
								style:background-size={cell.backgroundSize}
								style:background-position={cell.backgroundPosition}
							></figure>
						{/snippet}
					</CoverFramer>
				{:else}
					<CoverFramer
						bind:frame={fitFrame}
						src={chosen.src}
						ratio={COVER_RATIO}
						onmeasured={(aspect) => (fitAspect = aspect)}
					/>
				{/if}
			{/key}
		{:else if framing}
			<!--
				Which moment of the clip. One picture holds every frame, so this seeks nothing and
				fetches nothing per frame: the strip is loaded once, measured, and moved behind a
				window one tile wide. The same thing the player's scrubber does, through the same two
				functions rather than a second reading of the sheet.
			-->
			<div class="moment">
				{#if framing.sheet}
					<!-- Fetched to be measured rather than looked at: what is shown is one tile of it. -->
					<img
						class="strip"
						src={spriteUrl({ id: framing.id, art: framing.art })}
						alt=""
						aria-hidden="true"
						onload={measure}
					/>
				{/if}

				{#if !moved}
					<!-- The picture that was pressed, unchanged. Already fetched by the tile, so this
					     costs no request at all. -->
					<img class="still" src={thumbUrl({ id: framing.id, art: framing.art })} alt="" />
				{:else if box}
					<figure
						class="frame"
						style:width="{box.width}px"
						style:height="{box.height}px"
						style:background-image="url({spriteUrl({ id: framing.id, art: framing.art })})"
						style:background-size={box.backgroundSize}
						style:background-position={box.backgroundPosition}
					></figure>
				{:else}
					<!-- No strip built for this clip yet. The moment is still choosable against the
					     clock, which is worse than seeing it and much better than being refused. -->
					<p class="quiet">
						Sift hasn't built the frames for this clip yet. The moment can still be chosen.
					</p>
				{/if}

				<Slider
					min={0}
					max={framing.durationMs}
					step={100}
					value={atMs}
					label="Which moment of the clip"
					valueText={clock(atMs)}
					oninput={(next) => {
						atMs = next;
						moved = true;
					}}
				/>
				<p class="at">{clock(atMs)} of {clock(framing.durationMs)}</p>
			</div>
		{:else if loading}
			<Empty scope="block" busy>Reading their files.</Empty>
		{:else if items.length === 0}
			<Empty scope="block">There are no files here to take a picture from yet.</Empty>
		{:else}
			<ul class="grid">
				{#each items as one (one.id)}
					<li>
						<!--
							The app's own pressable surface rather than a bare `<button>`: a bare
							one would give this sheet its own reset, its own focus ring and its own
							idea of what pressing something looks like, three rules that exist once
							and are meant to. The gate counts them.

							The picture is a span inside it rather than the surface itself: a shared
							component's own element carries that component's scoping, so a rule
							written here for it loses on specificity and does nothing, silently. An
							element declared in this file is styled by a rule with nothing to argue
							with.
						-->
						<Pressable
							feedback="lift"
							radius="md"
							class="frame"
							picked={one.id === current}
							disabled={one.id === current || saving === one.id}
							onclick={() => void press(one)}
							aria-label={one.id === current ? 'This is the picture' : 'Use this as the picture'}
						>
							<span class="shot">
								<img src="/api/assets/{encodeURIComponent(one.id)}/thumb" alt="" loading="lazy" />
							</span>
						</Pressable>
					</li>
				{/each}
			</ul>
		{/if}
	{/snippet}

	<!-- Out of the scroll, so it is in the same place whether there are four files or sixty. -->
	{#snippet footer()}
		<div class="buttons">
			{#if fitting}
				{@const chosen = fitting}
				<Button type="button" onclick={() => (fitting = null)}>Back</Button>
				<Button
					type="button"
					tone="primary"
					disabled={saving !== ''}
					onclick={() => void choose(chosen.id, chosen.atMs, chosenFrame())}
				>
					Use this picture
				</Button>
			{:else if framing}
				{@const clip = framing}
				<Button type="button" onclick={() => (framing = null)}>Back</Button>
				<!-- The way out for somebody who wanted the file and not a frame of it. It sends no
				     moment at all, which is the same instruction a photograph sends: use the picture
				     the file already has. -->
				<Button type="button" onclick={() => fitClip(clip, null)}>
					Use the file's own picture
				</Button>
				<!--
					An unmoved slider sends no moment, deliberately.

					The picture on screen then is the file's own still (`thumbnail` cuts it at
					`timestamp_ms=0`, and this step opens there so the first thing seen is the tile
					pressed). Sending `at_ms: 0` would ask for a second rendering of the same frame
					under a different key, queue an ffmpeg run, and leave the cover a letter until
					it landed. What is shown is the same; sending nothing makes it instant.
				-->
				<Button
					type="button"
					tone="primary"
					disabled={saving !== ''}
					onclick={() => fitClip(clip, moved ? atMs : null)}
				>
					Use this frame
				</Button>
			{:else}
				<!-- The way out of the library entirely, offered beside the wall of files rather than
				     inside the frame step: it is an answer to the same question the wall is asking,
				     not to the question about which moment of a clip. The app's own file control, so
				     this sheet is not the third screen to dress a file input as a button. -->
				{#if onupload}
					<ChooseFile
						accept="image/*"
						icon="upload"
						disabled={saving !== ''}
						busy={saving === 'upload'}
						label="Choose a picture for {name}"
						onchoose={(file) => void upload(file)}
					>
						Upload a picture
					</ChooseFile>
				{/if}
				<Button type="button" onclick={() => (open = false)}>Close</Button>
			{/if}
		</div>
	{/snippet}
</Modal>

<style>
	/* The second step: the frame, the timeline under it, and the clock under that. */
	.moment {
		display: grid;
		justify-items: center;
		gap: var(--space-3);
	}

	/* Fetched, never shown. Out of the layout and out of the way of the pointer: the same two
	   lines the player's own preview uses, for the same reason. */
	.strip {
		position: absolute;
		inline-size: 0;
		block-size: 0;
		opacity: 0;
		pointer-events: none;
	}

	/* The same box the sprite tile fills, so the picture does not resize under the pointer at the
	   moment somebody starts dragging. */
	.still {
		inline-size: 320px;
		max-inline-size: 100%;
		block-size: auto;
		border-radius: var(--radius-md);
	}

	/* A moment's tile of the strip, drawn whole under the cover's window. */
	.tile {
		margin: 0;
		background-repeat: no-repeat;
	}

	.frame {
		margin: 0;
		overflow: hidden;
		border-radius: var(--radius-lg);
		background-color: var(--sift-bg);
		background-repeat: no-repeat;
		box-shadow: var(--elev-2);
	}

	.at {
		margin: 0;
		color: var(--sift-ink-3);
		font: var(--text-data);
		font-variant-numeric: tabular-nums;
	}

	/* Fills whatever width the sheet has, at a size a face is recognisable at. Portrait, because
	   every cover Sift draws is portrait and a landscape chooser would preview the wrong crop. */
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(6rem, 1fr));
		gap: var(--space-2);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	/* `:global` INSIDE a scoped parent, and both halves matter.
	 *
	 * `:global` because the element wearing this is the shared pressable's own, so a plain rule
	 * aimed at it matches nothing at all. The scoped `.grid` in front of it because the component's
	 * own rule is TWO classes (`.pressable.svelte-hash`) and a bare `:global(.frame)` is one: it
	 * would lose the specificity contest silently, which looks identical to matching. */
	.grid :global(.frame) {
		inline-size: 100%;
		padding: 0;
		overflow: hidden;
	}

	/* The picture, in an element this file owns. See the note where it is written. */
	.shot {
		display: block;
		inline-size: 100%;
		aspect-ratio: 3 / 4;
		border-radius: var(--radius-md);
		overflow: hidden;
		background: var(--sift-surface-3);
	}

	.shot img {
		inline-size: 100%;
		block-size: 100%;
		object-fit: cover;
	}
</style>
