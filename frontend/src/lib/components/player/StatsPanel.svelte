<script lang="ts">
	import { onDestroy } from 'svelte';
	import Readout from './Readout.svelte';
	import Button from '$lib/components/common/Button.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Scroller from '$lib/components/common/Scroller.svelte';
	import { copyText } from '$lib/shell/clipboard';
	import { toasts } from '$lib/shell/toasts.svelte';
	/*
	 * The facts, in the corner of the picture, for the player and a Theater cell alike: inside the
	 * picture because fullscreen paints nothing portalled. What the FILE is, then what is happening
	 * to it. A cell adds its own two lines last.
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
		file?: FileFacts | null;
		plan?: PlaybackPlan | null;
		position: number;
		duration: number;
		/**
		 * Read off the element, which changes without telling Svelte; re-read on each position
		 * tick.
		 */
		media?: HTMLVideoElement | null;
		playingWide?: number;
		playingTall?: number;
		source?: string | null;
		state?: string | null;
		/** "Photo" or "GIF"; absent in the player. */
		kind?: string | null;
		/** Leave out the playback lines, rather than rows of "Unknown" over a picture. */
		still?: boolean;
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
		/* Renamed on the way in: a variable called `state` shadows the `$state` rune. */
		state: cellState = null,
		kind = null,
		still = false,
		label = ACTS.stats
	}: Props = $props();

	/* The element's own length first; the server's until the metadata lands. */
	const seconds = $derived(duration > 0 ? duration : (plan?.duration_ms ?? 0) / 1000);

	/* Every line as data, so the list can be counted and copied. */
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
		/* A length rounds, as the record's does; the clock below is a playhead and floors. */
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
	 * The whole panel as text, off `rows`, through `copyText` (no `navigator.clipboard` over plain
	 * http). The tooltip says "Copied" and then goes back to the control's name.
	 */
	let copied = $state(false);
	let copiedFor: ReturnType<typeof setTimeout> | null = null;
	const COPIED_MS = 1600;

	async function copyReadings(): Promise<void> {
		const landed = await copyText(rows.map((row) => `${row.name}: ${row.value}`).join('\n'));
		if (!landed) {
			// Nothing is on the clipboard, so "Copied" would be a lie.
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
	<!-- `Readout`'s rows, alphabetical: the panel is a reference somebody scans for one line. -->
	<Scroller>
		<Readout facts={rows} />
	</Scroller>
	<!--
	At the foot, at the START of its line: a named exception to actions on the right, because the
	column is read down a left-hand edge of names.
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
	 * Tabular figures, so the ticking position does not jitter. Capped to the picture both ways,
	 * the readings wrapping and scrolling inside (`check_capped_scroller.js`).
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

	.foot {
		display: flex;
		justify-content: flex-start;
	}
</style>
