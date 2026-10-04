<script lang="ts">
	import { onDestroy } from 'svelte';
	import Readout from './Readout.svelte';
	import Button from '$lib/components/common/Button.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Scroller from '$lib/components/common/Scroller.svelte';
	import { copyText } from '$lib/shell/clipboard';
	import { toasts } from '$lib/shell/toasts.svelte';
	/*
	 * The facts, in the corner of the picture. One panel, drawn by the player and by a Theater cell.
	 *
	 * Inside the picture rather than in a tooltip or a dialog, for the reason the repair notice
	 * documents: fullscreen paints the fullscreened element and what is inside it, so anything
	 * portalled to the page is invisible for the whole time somebody is watching full screen.
	 *
	 * What the FILE is, then what is happening to it. The two are different questions and they
	 * disagree often enough to be worth showing together: a 4K file being converted is playing
	 * at 1080, and nothing else on screen would say so.
	 *
	 * ONE component for both surfaces, and that is the point of it existing. A cell with a panel of
	 * its own would drift from the player's: fewer lines, under the bar rather than over the
	 * picture, printing `transcode` where the player says "Converted". Two panels
	 * answering one question in two ways is worse than either of them, because whichever one somebody
	 * learned first is the one that is wrong everywhere else.
	 *
	 * A cell adds two lines of its own at the end (what it is drawing from and what it is doing)
	 * and those are the only lines that are not shared. They are facts about the CELL rather than
	 * about the file, the player has no cell, and they sort last anyway.
	 */
	import {
		bitrate,
		bufferedAhead,
		clock,
		codecs,
		depth,
		dimensions,
		playingAs,
		ratio,
		rate,
		size,
		type FileFacts
	} from '$lib/player/facts';
	import { length } from '$lib/library/facts';
	import type { PlaybackPlan } from '$lib/player/playback';
	import { ACTS } from '$lib/player/acts';

	interface Props {
		/** What the library knows about the file. Absent means every line about the file says so. */
		file?: FileFacts | null;
		/** What the server said about playing it here, which is where "How" comes from. */
		plan?: PlaybackPlan | null;
		/** Where the playhead is and how long the file is, in seconds. */
		position: number;
		duration: number;
		/**
		 * The element the file is playing in.
		 *
		 * Read here rather than handed in as numbers, because what a browser is holding ahead and
		 * what it is actually drawing at are properties of the element and change without anything
		 * telling Svelte they did. This component re-renders every time the position ticks: once a
		 * second, which is the rate those two are worth reading at.
		 */
		media?: HTMLVideoElement | null;
		/** What is on screen, for anything with no video element: a photograph in a cell. */
		playingWide?: number;
		playingTall?: number;
		/** A cell's own two facts. Absent in the player, which has neither. */
		source?: string | null;
		state?: string | null;
		/**
		 * What kind of thing this is, where the answer is not "a video": "Photo" or "GIF".
		 * Absent in the player, where every file is a clip and the line would read the same on
		 * everything.
		 *
		 * "Photo" and not "Photograph", the word the app uses everywhere else (Photo Sets,
		 * photo_library).
		 */
		kind?: string | null;
		/**
		 * Nothing is playing here, so leave out the lines about playback.
		 *
		 * A photograph has no bitrate, no frame rate, no buffer and no playhead, and drawing eight
		 * rows of "Unknown" and "0.0s" over a picture is worse than not drawing them: it reads as a
		 * panel that failed rather than as questions that do not apply.
		 */
		still?: boolean;
		/** What a screen reader calls this panel. A wall has several, so a cell names its number. */
		label?: string;
	}

	let {
		file = null,
		plan = null,
		position,
		duration,
		media = null,
		playingWide = 0,
		playingTall = 0,
		source = null,
		/* Renamed on the way in, and only on the way in. `$state` is a RUNE, and a variable called
		   `state` in this file's scope shadows it: the compiler then reads `$state(false)` as a store
		   subscription on this prop and the file will not build. The prop keeps its name, because it is
		   what a cell calls this fact and two surfaces hand it over. */
		state: cellState = null,
		kind = null,
		still = false,
		label = ACTS.stats
	}: Props = $props();

	/* The length to divide the size by. The element's answer first, because it is measured from the
	   file this browser is actually playing; the server's is the fallback for the moment before the
	   metadata lands. */
	const seconds = $derived(duration > 0 ? duration : (plan?.duration_ms ?? 0) / 1000);

	/*
	 * Every line, in one list: built as data rather than markup, so it is clear the two conditional
	 * rows change the panel's height, and the list can be counted.
	 *
	 * What the picture and the sound are encoded with decides whether a browser can play a file as
	 * it is. The container is the word on the end of the filename and says almost nothing: an mp4
	 * of AV1 and an mp4 of H.264 are the same word and a different answer.
	 */
	const rows = $derived([
		{ name: 'Aspect ratio', value: ratio(file) },
		{ name: 'Bit depth', value: depth(file) },
		...(still ? [] : [{ name: 'Bitrate', value: bitrate(file, seconds) }]),
		...(still
			? []
			: [{ name: 'Buffered ahead', value: `${bufferedAhead(media, position).toFixed(1)}s` }]),
		...(still ? [] : [{ name: 'Codec', value: codecs(file) }]),
		{ name: 'Container', value: file?.container ?? 'Unknown' },
		{ name: 'Dimensions', value: dimensions(file?.width, file?.height) },
		...(still ? [] : [{ name: 'Frame rate', value: rate(file) }]),
		...(kind ? [{ name: 'Kind', value: kind }] : []),
		/*
		 * How long the file is, which is the record's `Length` and must read the same: the clock
		 * below is a playhead and floors, so a 9.842-second clip would read `0:09` here beside
		 * `0:10` on the record. A length rounds; a position never does.
		 */
		...(still ? [] : [{ name: 'Length', value: length(seconds * 1000) ?? 'Unknown' }]),
		...(still ? [] : [{ name: 'Playback', value: playingAs(plan?.route, plan?.copy_kind) }]),
		...(still
			? []
			: [
					{
						name: 'Playing at',
						value: media?.videoWidth
							? `${media.videoWidth} x ${media.videoHeight}`
							: playingWide && playingTall
								? `${playingWide} x ${playingTall}`
								: 'Not yet'
					}
				]),
		...(still ? [] : [{ name: 'Position', value: clock(position) }]),
		{ name: 'Size on disk', value: size(file?.size_bytes) },
		...(source !== null ? [{ name: 'Source', value: source }] : []),
		...(cellState !== null ? [{ name: 'State', value: cellState }] : [])
	]);

	/*
	 * The whole panel, as text, in one press.
	 *
	 * This panel is opened when a file will not play properly, and the next thing is being asked
	 * what it is, so the answer would otherwise be typed out by hand, which is how a wrong codec or
	 * a transposed resolution gets passed on. Settings has the same Copy beside the machine's own
	 * table.
	 *
	 * Off `rows` rather than the drawn markup: the lines are data, already worded and in the order
	 * shown, so reading the DOM would ask the screen what this file was told to say. What is copied
	 * is exactly what is drawn, including the lines a still leaves out.
	 *
	 * Through `copyText` and never `navigator.clipboard`: that object does not exist on a
	 * plain-http address, which is how a self-hosted Sift is normally reached. See
	 * `$lib/shell/clipboard`.
	 *
	 * The tooltip says "Copied" and then goes back to being the control's name, as the filename
	 * copy on the file's own screen does: left reading Copied, the next person to hover it is told
	 * what happened rather than what pressing would do.
	 */
	let copied = $state(false);
	let copiedFor: ReturnType<typeof setTimeout> | null = null;
	/** Long enough to be read, short enough that the label is a name again before it is next used. */
	const COPIED_MS = 1600;

	async function copyReadings(): Promise<void> {
		const landed = await copyText(rows.map((row) => `${row.name}: ${row.value}`).join('\n'));
		if (!landed) {
			// The one case worth a sentence: nothing is on the clipboard, and a tooltip reading
			// "Copied" would be a lie about the only thing this control does.
			toasts.show("These readings couldn't be copied", { tone: 'error' });
			return;
		}
		if (copiedFor) clearTimeout(copiedFor);
		copied = true;
		copiedFor = setTimeout(() => (copied = false), COPIED_MS);
	}

	onDestroy(() => {
		if (copiedFor) clearTimeout(copiedFor);
	});
</script>

<aside class="stats" aria-label={label}>
	<!--
		The rows are `Readout`'s: the quiet names, the loud tabular values, the aligned column.
		What stays here is which facts to show and how to word them, which is this panel's own
		business, and the corner it sits in, which belongs to the picture behind it.

		In alphabetical order, which is the only order that needs no explanation. Grouped by meaning
		it would read as arbitrary to everybody but whoever grouped it, and the panel is a reference:
		somebody opens it looking for one line.
	-->
	<!-- In the shared scrolling region, because a panel over a narrow portrait cell can be shorter
	     than its own readings. See `.stats` for the ceiling this scrolls under. -->
	<Scroller>
		<Readout facts={rows} />
	</Scroller>
	<!--
		The one control on the panel, under the readings and at the start of its own line.

		A named exception to "an action sits on the right of a row or card": a card's actions sit at
		its end because the eye has finished with the card by then, but this is a reference column
		read top to bottom down a left-hand edge of names, so a control at the top right would be
		the first thing and the furthest from that edge. At the foot on the same edge it is the last
		thing, where something you do about what you just read belongs.

		A line of its own rather than a hand-positioned corner: the panel is already positioned
		against the picture, and a second absolutely placed thing inside it would overlap a reading
		at the panel's clamped width.
	-->
	<div class="foot">
		<Tooltip label={copied ? 'Copied' : 'Copy'} staysOnPress>
			<Button
				tone="ghost"
				size="small"
				icon={copied ? 'check' : 'content_copy'}
				aria-label="Copy these readings"
				onclick={() => void copyReadings()}
			/>
		</Tooltip>
	</div>
</aside>

<style>
	/*
	 * The panel of facts, in the top corner of the picture.
	 *
	 * Machine facts, so tabular figures: the position ticks once a second, and a proportional set
	 * would make the column jitter. Opposite corner from the repair notice, the only other thing up
	 * there.
	 *
	 * Positioned against whatever frame it is dropped into (the stage around the player, inside a
	 * cell, around a photograph), so all three surfaces put it in the same corner without saying
	 * so.
	 *
	 * An edge and a blur, so the panel reads as a pane over the picture rather than a hole punched
	 * in it and stays legible over a bright frame. One panel for the player, a cell and a still.
	 *
	 * Capped to the picture it sits on, both ways. The stage clips (for its corners), and in a
	 * portrait cell a fixed 280px panel as tall as its readings would be cut off at the cell's
	 * edges. So the ceiling is the smaller of 280 and the picture less its inset on both sides, and
	 * the readings wrap rather than run past it (`overflow-wrap: anywhere`: a codec string or a
	 * file name has nowhere else to break). Down the page it is the picture less the same inset,
	 * and the readings scroll inside that through the shared region while the copy control stays on
	 * its line at the foot. A grid with a `minmax(0, 1fr)` row makes the region obey the ceiling
	 * rather than paint through it, the shape `check_capped_scroller.js` holds every capped box to.
	 */
	.stats {
		position: absolute;
		inset: var(--space-3) auto auto var(--space-3);
		z-index: 4;
		display: grid;
		grid-template-rows: minmax(0, 1fr) auto;
		max-inline-size: min(280px, calc(100% - 2 * var(--space-3)));
		max-block-size: calc(100% - 2 * var(--space-3));
		overflow-wrap: anywhere;
		padding: var(--space-3);
		border-radius: var(--radius-md);
		border: 1px solid var(--sift-line);
		background: var(--sift-scrim);
		backdrop-filter: blur(var(--blur-glass));
		font: var(--text-body-sm);
		color: var(--sift-ink);
	}

	/* The control's line, under the readings and at the START of it. See the markup, where the
	   exception to "actions on the right" is argued. Pushed to the start rather than given a width,
	   so the panel keeps whatever width the longest reading asks for. */
	.foot {
		display: flex;
		justify-content: flex-start;
	}
</style>
