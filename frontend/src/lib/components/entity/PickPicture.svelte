<script lang="ts">
	/* LIVE: nothing moves it (the pictures to choose from, read each time the sheet opens and dropped when it closes) */
	/* Choosing the picture a person, a site or a tag is shown with, from the pencil on the cover: one
	 * page of its files newest first; a clip then picks a moment (opening on the frame pressed, off
	 * the player's strip), and every picture is framed with CoverFramer before Save. */
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
		/** Chosen; the page writes it. A null moment means the file's own still. */
		onpick: (assetId: string, atMs: number | null, frame: Frame | null) => Promise<void>;
		/** A picture from the disk instead; absent where the caller has no route for it. */
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

	/** The last step: the picture and its window; a moved moment is drawn as its strip tile. */
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
	/** Whether the moment moved; until then the file's own sharp still is shown. */
	let moved = $state(false);

	/* The frame drawn by the player scrubber's own two functions. */
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

	/* Closing returns to the wall of files. */
	$effect(() => {
		if (!open) {
			framing = null;
			fitting = null;
		}
	});

	/* Loaded when the sheet opens, not on mount: every entity page holds one. */
	$effect(() => {
		if (open) void load();
	});

	async function load() {
		loading = true;
		problem = undefined;
		try {
			/* The listing answers with the assets themselves, not a wrapper. */
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

	/* A photograph is the answer, a clip a question; its strip is fetched on the press, and a clip
	   without one is still chosen against the clock. */
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

	/* A picture from the disk; a refusal is said in the server's own words. */
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

	/* To the framing step: the clip's own still, or its strip tile where the moment moved. */
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
			/* A 404 is a file removed since: said apart from a refusal, and the row dropped. */
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
			<!-- Keyed on the picture, so another starts a new window. -->
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
			<!-- Which moment: the strip loaded once and moved behind a one-tile window. -->
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
					<!-- The pressed picture, already fetched. -->
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
					<!-- No strip yet: the moment is chosen against the clock. -->
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
						Pressable, not a bare button; the picture is a span this file styles.
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
				<!-- The file's own picture, with no moment, as a photograph sends. -->
				<Button type="button" onclick={() => fitClip(clip, null)}>
					Use the file's own picture
				</Button>
				<!--
				An unmoved slider sends no moment: `at_ms: 0` would render the same frame again.
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
				<!-- A picture from outside the library, beside the wall, through ChooseFile. -->
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

	/* Fetched, never shown, as the player's preview. */
	.strip {
		position: absolute;
		inline-size: 0;
		block-size: 0;
		opacity: 0;
		pointer-events: none;
	}

	/* The tile's box, so nothing resizes when dragging starts. */
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

	/* Portrait, as every cover is. */
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(6rem, 1fr));
		gap: var(--space-2);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	/* Global inside the scoped `.grid`: Pressable's own element, and two classes to win. */
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
