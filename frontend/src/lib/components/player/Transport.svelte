<script module lang="ts">
	/** The words a step with nothing beside it is dimmed with. */
	export const NOTHING_BEFORE = 'Nothing before this';
	export const NOTHING_AFTER = 'Nothing after this';
</script>

<script lang="ts">
	/*
	 * THE TRANSPORT: Repeat, Previous, Play or Pause, Next, Shuffle, in that order, on every bar.
	 *
	 * What each press does is the caller's; this file draws the five. Every press is always drawn:
	 * one with nothing to act on is dimmed with its reason as its words, so a bar keeps one shape.
	 */
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { Button } from '$lib/components/common';
	import { ACTS, keyOf, type Keyboard } from '$lib/player/acts';
	import { loopModeIcon, loopModeLabel, loopRepeats, type LoopMode } from '$lib/player/loop-modes';

	/** Shuffle on the bar: whether the order is shuffled, and the press that turns it over. */
	export interface ShuffleControl {
		on: boolean;
		onpress: () => void;
		/** Why it cannot act, drawn dimmed with these words; absent, it is live. */
		why?: string;
	}

	/** What happens at the end of a file: the one control with three answers (`loop-modes`). */
	export interface RepeatControl {
		mode: LoopMode;
		onpress: () => void;
		/** Why it cannot act, drawn dimmed with these words; absent, it is live. */
		why?: string;
	}

	interface Props {
		playing: boolean;
		onplay?: () => void;
		/** Whether there is anything to play: false dims Play with `playWhy`. */
		playable?: boolean;
		playWhy?: string;
		onback?: () => void;
		onforward?: () => void;
		backLabel?: string;
		forwardLabel?: string;
		/** Why Previous or Next cannot act, where there is no handler. */
		backWhy?: string;
		forwardWhy?: string;
		shuffle: ShuffleControl;
		repeat: RepeatControl;
		/** Play alone, for the docked phone strip: a thumb's strip has room for one press. */
		playOnly?: boolean;
		/** False where a swipe steps instead (a phone in the hand), so the step pair stands down. */
		steps?: boolean;
		/** Whose keys this bar answers, so each tooltip shows its key; null where it answers none. */
		keyboard?: Keyboard | null;
		/** The handlers that say a verb is being pointed at, spread on every press. See `PlayerBar`. */
		aims?: Record<string, (() => void) | undefined>;
		/** The tooltips open above the bar where it stands along the foot of a window. */
		placement?: 'top' | 'bottom';
	}

	let {
		playing,
		onplay,
		playable = true,
		playWhy,
		onback,
		onforward,
		backLabel,
		forwardLabel,
		backWhy,
		forwardWhy,
		shuffle,
		repeat,
		playOnly = false,
		steps = true,
		keyboard = 'player',
		aims = {},
		placement
	}: Props = $props();

	const playWords = $derived(
		playable ? (playing ? ACTS.pause : ACTS.play) : (playWhy ?? NOTHING_AFTER)
	);
	const repeatWords = $derived(repeat.why ?? loopModeLabel(repeat.mode));
	const shuffleWords = $derived(shuffle.why ?? ACTS.shuffle);
	const backWords = $derived(onback ? (backLabel ?? ACTS.previous) : (backWhy ?? NOTHING_BEFORE));
	const forwardWords = $derived(
		onforward ? (forwardLabel ?? ACTS.next) : (forwardWhy ?? NOTHING_AFTER)
	);
</script>

<div class="transport">
	{#if !playOnly}
		<!-- `{...aims}` FIRST on every one of these, so anything written after it wins. -->
		<!-- No key on the tooltip: R answers it, and the act table names no key for it. -->
		<Tooltip label={repeatWords} {placement}>
			<Button
				{...aims}
				tone="ghost"
				icon={loopModeIcon(repeat.mode)}
				aria-label={repeatWords}
				pressed={loopRepeats(repeat.mode)}
				disabled={repeat.why !== undefined}
				onclick={repeat.onpress}
			/>
		</Tooltip>
	{/if}

	{#if !playOnly && steps}
		<Tooltip
			label={backWords}
			{placement}
			shortcut={onback && keyboard ? keyOf('previous', keyboard) : undefined}
		>
			<Button
				{...aims}
				tone="ghost"
				icon="skip_previous"
				aria-label={backWords}
				disabled={!onback}
				onclick={onback}
			/>
		</Tooltip>
	{/if}

	<Tooltip
		label={playWords}
		{placement}
		shortcut={playable && keyboard ? keyOf(playing ? 'pause' : 'play', keyboard) : undefined}
	>
		<Button
			{...aims}
			tone="ghost"
			class="play"
			iconSize={28}
			icon={playable && playing ? 'pause' : 'play_arrow'}
			aria-label={playWords}
			disabled={!playable}
			onclick={onplay}
		/>
	</Tooltip>

	{#if !playOnly && steps}
		<Tooltip
			label={forwardWords}
			{placement}
			shortcut={onforward && keyboard ? keyOf('next', keyboard) : undefined}
		>
			<Button
				{...aims}
				tone="ghost"
				icon="skip_next"
				aria-label={forwardWords}
				disabled={!onforward}
				onclick={onforward}
			/>
		</Tooltip>
	{/if}

	{#if !playOnly}
		<Tooltip
			label={shuffleWords}
			{placement}
			shortcut={shuffle.why || !keyboard ? undefined : keyOf('shuffle', keyboard)}
		>
			<Button
				{...aims}
				tone="ghost"
				icon="shuffle"
				aria-label={shuffleWords}
				pressed={shuffle.on}
				disabled={shuffle.why !== undefined}
				onclick={shuffle.onpress}
			/>
		</Tooltip>
	{/if}
</div>

<style>
	.transport {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: var(--space-1);
		flex: none;
	}

	/* The press aimed at without looking: bigger, at full ink. The class twice to outrank the
	   shared button's own four-class size rule. */
	.transport :global(.btn.play.play) {
		inline-size: 44px;
		block-size: 44px;
		color: var(--sift-ink);
	}
</style>
