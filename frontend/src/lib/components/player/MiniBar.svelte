<script lang="ts">
	/*
	 * The Audio player's strip, in the shape every player bar has: the scrub
	 * line along the top with the time so far at its start and the length at its end; under it the
	 * small picture all the way to the left with the name beside it (the picture is the panel's own,
	 * so the sound never stops between the two), the transport in the middle, the sound and the ways
	 * out on the right. Docked on a phone it carries what fits a thumb, in the same order: the
	 * picture, the name, Play, back to full size and close.
	 */
	import { untrack } from 'svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Separator from '$lib/components/common/Separator.svelte';
	import { Button, Slider } from '$lib/components/common';
	import { loudness } from '$lib/player/loudness.svelte';
	import { run } from '$lib/player/run.svelte';
	import { toggleShuffle } from '$lib/player/asset-view';
	import { nextLoopMode } from '$lib/player/loop-modes';
	import { dwell } from '$lib/player/dwell.svelte';
	import { mini } from '$lib/player/mini.svelte';
	import { spriteUrl } from '$lib/entity/art';
	import { api } from '$lib/api/client';
	import type { components } from '$lib/api/schema';
	import { ACTS, keyOf } from '$lib/player/acts';
	import type Player from './Player.svelte';
	import ScrubLine from './ScrubLine.svelte';
	import Transport from './Transport.svelte';

	interface Props {
		asset: NonNullable<typeof mini.asset>;
		docked: boolean;
		/** Whether the transport has anything in it; an empty one is not drawn. */
		transportHolds: boolean;
		concealed: boolean;
		showsPicture: boolean;
		/** The word the panel's strip uses for what it holds, said for a Hidden file. */
		holding: string;
		playing: boolean;
		player: ReturnType<typeof Player> | null;
		around: { back: boolean; forward: boolean };
		onwalk: (forward: boolean) => void;
		onexpand: () => void;
		playedTo: number;
		length: number;
	}

	let {
		asset,
		docked,
		transportHolds,
		concealed,
		showsPicture,
		holding,
		playing,
		player,
		around,
		onwalk,
		onexpand,
		playedTo,
		length
	}: Props = $props();

	/* The strip keeps one shape: a control that cannot act is dimmed with its reason in the tooltip,
	   rather than left out, so nothing slides under the pointer that pressed it. */
	const HIDDEN_REASON = 'This one is hidden';

	/* Where the clip is, on the scrub line: a clip whose length is known. */
	const timed = $derived(!concealed && !showsPicture && length > 0);

	/* The name in the middle. The panel holds an id rather than a record, so it is asked for here;
	   never for a Hidden file, which is named only as hidden. */
	let named = $state('');
	$effect(() => {
		const id = concealed ? null : asset.id;
		untrack(() => {
			named = '';
			if (id === null) return;
			void api
				.get<components['schemas']['AssetDetail']>(`/assets/${id}`)
				.then((file) => {
					if (mini.asset?.id === id) named = file.filename ?? '';
				})
				.catch(() => {});
		});
	});
</script>

<!-- The scrub line along the top, as every player bar draws it: the time so far at its start, the
     length at its end. Dimmed, never removed, where there is no playhead. First in the markup, so
     Tab reaches it before the presses, as it does on the other bars. -->
<div class="bar-timeline">
	<ScrubLine
		position={playedTo}
		duration={length}
		sheet={asset.sprite}
		sheetUrl={spriteUrl({ id: asset.id, art: asset.art })}
		disabled={concealed || showsPicture}
		onseek={(seconds) => player?.goTo(seconds)}
		{timed}
	/>
</div>
<span class="named" class:docked>
	<span class="title">{concealed ? holding : named}</span>
</span>
{#if transportHolds}
	<!-- The transport every player bar draws, in its order: Shuffle, Previous, Play, Next, Repeat.
	     Docked on a phone, Play alone: a thumb's strip has room for one. The order and what happens
	     at the end are written where the player's bar writes them (`toggleShuffle`, `dwell.choose`). -->
	<div class="transport" class:docked>
		{#if docked}
			<Transport
				{playing}
				onplay={() => player?.togglePlayback()}
				keyboard="mini"
				placement="top"
			/>
		{:else}
			<Transport
				{playing}
				onplay={() => player?.togglePlayback()}
				playable={!concealed}
				playWhy={concealed ? HIDDEN_REASON : undefined}
				onback={around.back ? () => onwalk(false) : undefined}
				onforward={around.forward ? () => onwalk(true) : undefined}
				backWhy="Nothing before this"
				forwardWhy="Nothing after this"
				shuffle={{
					on: run.shuffle,
					onpress: () => toggleShuffle(asset.id),
					why: concealed ? HIDDEN_REASON : undefined
				}}
				repeat={{
					mode: dwell.mode,
					onpress: () => void dwell.choose(nextLoopMode(dwell.mode)),
					why: concealed ? HIDDEN_REASON : undefined
				}}
				keyboard="mini"
				placement="top"
			/>
		{/if}
	</div>
{/if}
<div class="ends" class:docked>
	{#if !docked}
		<!-- How loud, as the player's own bar says it; dimmed on a Hidden clip, so the strip keeps
		     its shape. -->
		{@const muted = player?.isMuted() ?? false}
		{@const muteWords = concealed ? HIDDEN_REASON : muted ? ACTS.unmute : ACTS.mute}
		<div class="volume" role="group" aria-label="Volume">
			<Tooltip
				label={muteWords}
				shortcut={player ? keyOf(muted ? 'unmute' : 'mute', 'mini') : undefined}
			>
				<Button
					tone="ghost"
					icon={muted || loudness.level === 0 ? 'volume_off' : 'volume_up'}
					aria-label={muteWords}
					disabled={!player}
					onclick={() => player?.toggleSound()}
				/>
			</Tooltip>
			<Slider
				class="bar-level"
				label="Volume"
				valueText="{loudness.level}%"
				value={loudness.level}
				disabled={!player}
				oninput={(level: number) => loudness.set(level)}
			/>
		</div>
		<Separator vertical />
	{/if}
	{#if !docked}
		<Tooltip label={ACTS.miniPlayer} shortcut={keyOf('miniPlayer', 'mini')}>
			<Button
				tone="ghost"
				icon="picture_in_picture"
				aria-label={ACTS.miniPlayer}
				onclick={() => mini.toPanel()}
			/>
		</Tooltip>
	{/if}
	<!-- I takes the docked strip to full size and the floating strip to the mini player, so the key
	     is shown only where it does what the button does. -->
	<Tooltip label={ACTS.fullSize} shortcut={docked ? keyOf('fullSize', 'mini') : undefined}>
		<Button tone="ghost" icon="open_in_full" aria-label={ACTS.fullSize} onclick={onexpand} />
	</Tooltip>
	<Tooltip label={ACTS.close}>
		<Button tone="ghost" icon="close" aria-label={ACTS.close} onclick={() => mini.close()} />
	</Tooltip>
</div>

<style>
	/* `--bar-band` and `--bar-picture` are MiniPlayer's, set on the frame around this. Each part
	   stands in a named area of the frame's grid ('line' along the top; 'start', 'transport' and
	   'ends' under it; docked, 'picture' and 'title' for the start). */
	.volume {
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}

	.volume :global(.bar-level) {
		inline-size: var(--space-16);
	}

	.transport.docked :global(button),
	.ends.docked :global(button) {
		inline-size: var(--touch-target);
		block-size: var(--touch-target);
	}

	.transport {
		grid-area: transport;
		display: flex;
		justify-content: center;
	}

	.ends {
		grid-area: ends;
		justify-self: end;
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}

	/* The name beside the picture, which shares the start of the row with it: clear of the picture
	   by its width and the row's gap. The name is the one part that gives way. */
	.named {
		grid-area: start;
		display: flex;
		flex-direction: column;
		min-inline-size: 0;
		padding-inline-start: calc(var(--bar-picture, 80px) + var(--space-3));
	}

	.title {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font: var(--text-body-sm);
		color: var(--sift-ink);
	}

	/* Docked, the name in its own place on the strip's grid, after the picture. */
	.named.docked {
		grid-area: title;
		padding-inline-start: 0;
	}

	/*
	 * The scrub line along the top of the strip, the strip's whole width inside its padding. Its
	 * target is the slider's own box: a mouse's 16 pixels, a finger's 44 on a phone, the band the
	 * strip keeps for it. The frame under the pointer rises above the strip's top edge with nothing
	 * of the strip under it.
	 */
	.bar-timeline {
		grid-area: line;
		align-self: center;
		min-inline-size: 0;
	}
</style>
