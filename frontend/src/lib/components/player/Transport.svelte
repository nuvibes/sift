<script lang="ts">
	/*
	 * THE TRANSPORT: Shuffle, Previous, Play or Pause, Next, Repeat, in that order, on every bar.
	 *
	 * One component for the popout player's bar, every Theater bar and the Audio player, so the five
	 * presses stand in the same order with the same glyphs, words and keys wherever somebody meets
	 * them: the step pair and Play in the middle of the bar, Shuffle left of Previous, Repeat right
	 * of Next. Where the five stand on a bar is the bar's; what they are is here.
	 *
	 * What each press DOES is the caller's: a player shuffles its run, a Theater bar every cell it is
	 * addressing, the Audio player the run it carries. So each arrives as a state and a callback, and
	 * this file draws the button.
	 *
	 * A press with nothing to act on is either not drawn or dimmed with its reason, and the caller
	 * says which: no handler and no reason is not drawn (a list of one has no next to wait for);
	 * a reason is drawn dimmed with that reason as its words (the Audio player keeps one shape).
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
		/** Whether there is anything to play: false draws no Play unless `playWhy` says why not. */
		playable?: boolean;
		/** Why Play cannot act, drawn dimmed with these words. */
		playWhy?: string;
		onback?: () => void;
		onforward?: () => void;
		backLabel?: string;
		forwardLabel?: string;
		/** Why Previous or Next cannot act, drawn dimmed with these words where there is no handler. */
		backWhy?: string;
		forwardWhy?: string;
		shuffle?: ShuffleControl;
		repeat?: RepeatControl;
		/** Whose keys this bar answers, so each tooltip shows the key the act table gives it. */
		keyboard?: Keyboard;
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
		backLabel = ACTS.previous,
		forwardLabel = ACTS.next,
		backWhy,
		forwardWhy,
		shuffle,
		repeat,
		keyboard = 'player',
		aims = {},
		placement
	}: Props = $props();

	const playWords = $derived(playing ? ACTS.pause : ACTS.play);
	const repeatWords = $derived(repeat ? (repeat.why ?? loopModeLabel(repeat.mode)) : '');
	const shuffleWords = $derived(shuffle?.why ?? ACTS.shuffle);
</script>

<div class="transport">
	{#if shuffle}
		<!-- `{...aims}` FIRST on every one of these, so anything written after it wins: a spread placed
		     last is how a caller's object comes to replace a `class` the call site set. -->
		<Tooltip
			label={shuffleWords}
			{placement}
			shortcut={shuffle.why ? undefined : keyOf('shuffle', keyboard)}
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

	{#if onback}
		<Tooltip label={backLabel} {placement} shortcut={keyOf('previous', keyboard)}>
			<Button {...aims} tone="ghost" icon="skip_previous" aria-label={backLabel} onclick={onback} />
		</Tooltip>
	{:else if backWhy}
		<Tooltip label={backWhy} {placement}>
			<Button {...aims} tone="ghost" icon="skip_previous" aria-label={backWhy} disabled />
		</Tooltip>
	{/if}

	<!-- Not drawn at all where there is nothing to play, unless the caller says why it cannot. -->
	{#if playable}
		<Tooltip label={playWords} {placement} shortcut={keyOf(playing ? 'pause' : 'play', keyboard)}>
			<Button
				{...aims}
				tone="ghost"
				class="play"
				iconSize={28}
				icon={playing ? 'pause' : 'play_arrow'}
				aria-label={playWords}
				onclick={onplay}
			/>
		</Tooltip>
	{:else if playWhy}
		<Tooltip label={playWhy} {placement}>
			<Button
				{...aims}
				tone="ghost"
				class="play"
				iconSize={28}
				icon="play_arrow"
				aria-label={playWhy}
				disabled
			/>
		</Tooltip>
	{/if}

	{#if onforward}
		<Tooltip label={forwardLabel} {placement} shortcut={keyOf('next', keyboard)}>
			<Button
				{...aims}
				tone="ghost"
				icon="skip_next"
				aria-label={forwardLabel}
				onclick={onforward}
			/>
		</Tooltip>
	{:else if forwardWhy}
		<Tooltip label={forwardWhy} {placement}>
			<Button {...aims} tone="ghost" icon="skip_next" aria-label={forwardWhy} disabled />
		</Tooltip>
	{/if}

	{#if repeat}
		<!-- No key on the tooltip: R answers it on the keyboard, and the act table names no key for
		     it, which is how every bar has shown it. -->
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
</div>

<style>
	.transport {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: var(--space-1);
		flex: none;
	}

	/*
	 * The one control on the bar somebody aims at without looking: bigger, and at full ink.
	 *
	 * The class twice: the shared button sizes an icon-only control with `.btn.icon-only.medium`,
	 * which carries its own file's scope class and counts four, so a rule counting three loses
	 * silently and Play comes out the size of every other glyph with every test still green.
	 */
	.transport :global(.btn.play.play) {
		inline-size: 44px;
		block-size: 44px;
		color: var(--sift-ink);
	}
</style>
