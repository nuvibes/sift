<script module lang="ts">
	/* WHY NOT BITS-UI: bits-ui has no badge, and there is nothing for it to have. This is a word, a
	colour and a mark, with no focus or state to announce. */
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Badge',
		category: 'primitive',
		role: 'a word that states a status, in the color that status has',
		basis: 'own',
		states: ['ok', 'warn', 'bad', 'info', 'neutral', 'busy', 'wordless']
	} satisfies DesignEntry;

	/* A piece of work's state, said one way on every screen that shows it. */
	import type { IconName } from '$lib/design/icons';

	export type BadgeState =
		'queued' | 'running' | 'paused' | 'done' | 'blocked' | 'quarantined' | 'canceled' | 'failed';

	// Amber wants a person, red is failure only, grey asks nothing: canceled is grey on purpose.
	// Exported for the Activity state chips, so a chip and a badge use one word per state.
	export const LABELS: Record<BadgeState, string> = {
		queued: 'Queued',
		running: 'In progress',
		paused: 'Paused',
		done: 'Done',
		blocked: 'Blocked',
		quarantined: 'Quarantined',
		canceled: 'Canceled',
		failed: 'Failed'
	};

	/* A glyph per state, so the shape says what the colour says; endings are rings, filled is final.
	`running` has no entry: its mark is the spinner. */
	const MARKS: Record<Exclude<BadgeState, 'running'>, { name: IconName; filled: boolean }> = {
		queued: { name: 'playlist_add_check', filled: false },
		/* Grey and outlined: nothing went wrong, and a pause is still in motion. */
		paused: { name: 'pause', filled: false },
		done: { name: 'check_circle', filled: true },
		blocked: { name: 'do_not_disturb_on', filled: true },
		/*
		 * The questioning shield: this file's state is unresolved, unlike the board's kept place.
		 */
		quarantined: { name: 'gpp_maybe', filled: true },
		/*
		 * Outlined, alone among endings: filled it would read as a failure, and a cancel is
		 * nobody's.
		 */
		canceled: { name: 'stop_circle', filled: false },
		failed: { name: 'cancel', filled: true }
	};
</script>

<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Spinner from './Spinner.svelte';

	interface Props {
		state: BadgeState;
		/**
		 * A more exact word for the state ("Waiting for cookies"); the colour stays the state's.
		 */
		label?: string;
		/** A screen's own mark where it knows more than the state; never a restyle. */
		icon?: IconName;
		/** Whether that mark is filled. See `MARKS`: filled is a state, outlined is one still moving. */
		iconFilled?: boolean;
		/** Spin the mark whatever the state; opt-in, since a list of spinners is unreadable. */
		busy?: boolean;
		/** Drop the drawn word and keep the mark; a screen reader still hears it. */
		wordless?: boolean;
		/** Drop the ground, for a badge inside a pill already in this state's colour. */
		plain?: boolean;
	}

	let {
		state,
		label,
		icon,
		iconFilled,
		busy = false,
		wordless = false,
		plain = false
	}: Props = $props();

	const word = $derived(label ?? LABELS[state]);

	/* The badge's mark; null while running, whose mark is the turning arc. */
	const mark = $derived.by(() => {
		if (busy || state === 'running') return null;
		const fallback = MARKS[state];
		return { name: icon ?? fallback.name, filled: iconFilled ?? (icon ? false : fallback.filled) };
	});
</script>

<span class="badge state-{state}" class:wordless class:plain>
	<!-- 10, not 16: the arc is ink to its edge while a glyph's disc stops short of its box. -->
	{#if mark}
		<Icon name={mark.name} size={16} filled={mark.filled} />
	{:else}
		<Spinner size={10} />
	{/if}{#if wordless}<span class="said">{word}</span>{:else}<span class="word">{word}</span>{/if}
</span>

<style>
	/* Chip's centring, in the same order, so a badge beside a chip sits at one height. */
	.badge {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: var(--space-1);
		line-height: 1;
		/* A small chip's height, so the two line up on a job row (pills.test.ts). */
		block-size: var(--chip-height-sm);
		padding-inline: var(--space-2);
		border-radius: var(--radius-full);
		font: var(--text-label);
		white-space: nowrap;
		flex: none;
		/* Never wider than its column: the words are cut with an ellipsis. */
		max-inline-size: 100%;
	}

	/* The words, as the part of the pill that gives way. The mark never does. */
	.word {
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	/* No word, so the mark is the whole badge and the padding that made room for words comes off. */
	.badge.wordless {
		padding-inline: var(--space-1);
		gap: 0;
	}

	/* Read out, never drawn. Not `display: none`, which takes it from a screen reader as well. */
	.said {
		position: absolute;
		inline-size: 1px;
		block-size: 1px;
		overflow: hidden;
		clip-path: inset(50%);
		white-space: nowrap;
	}

	/* The colour is the state's, from the `.state-*` properties in app.css. */
	.badge {
		background: var(--state-bg);
		color: var(--state-ink);
	}

	/* Inside something already painted in this state's colour. See `plain`. */
	.badge.plain {
		background: none;
		padding-inline: 0;
	}
</style>
