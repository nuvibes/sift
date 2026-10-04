<script module lang="ts">
	/* WHY NOT BITS-UI: bits-ui has no badge, and there is nothing for it to have. This is a word, a
	   colour and a mark: no focus, no keyboard, no state a screen reader has to be told about. */
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Badge',
		category: 'primitive',
		role: 'a word that states a status, in the color that status has',
		basis: 'own',
		states: ['ok', 'warn', 'bad', 'info', 'neutral', 'busy', 'wordless']
	} satisfies DesignEntry;

	/* The state of a piece of work, said the same way everywhere it is said.
	 *
	 * The jobs list and the download queue are two screens showing the same states, so the word, the
	 * colour and the mark live here rather than in either of them. Two screens each deciding what
	 * amber means is how one of them ends up disagreeing.
	 *
	 * The set is the union of what the screens actually render, which is not quite the queue's own
	 * state list: `canceled` is a state a job row can hold and `quarantined` is not. Quarantine
	 * happens at the ingress gate, to the origins whose files get moved on refusal: a download or
	 * a drop, never a scan, which leaves the file where it lies. So it reaches a screen as the state
	 * of a piece of work without ever being the state of a job.
	 */
	import type { IconName } from '$lib/design/icons';

	export type BadgeState =
		'queued' | 'running' | 'paused' | 'done' | 'blocked' | 'quarantined' | 'canceled' | 'failed';

	// Amber twice, deliberately. Blocked is waiting for cookies and quarantined is the ingress
	// check refusing a file: both want a person, neither is a failure. Red is failure and
	// destruction and nothing else.
	//
	// Which is why canceled is grey rather than red, sharing its colour with queued: somebody
	// stopped it on purpose and nothing needs attention. Neither grey asks anything of anyone, so
	// the word separates them.
	//
	// Exported for the state chips above the Activity list: the chips filter the list to a state
	// and the badges say which state each row is in, so both must use one word for one state.
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

	/*
	 * The mark, per state: a glyph rather than a dot, so the shape says what the colour says and
	 * either alone is enough. A dot would leave the two amber states told apart only by their word
	 * and give anybody who does not separate those colours easily a row of identical grey circles. The
	 * colour still steps (amber wants a person, red is failure, green is done, grey asks nothing)
	 * and the shape steps with it.
	 *
	 * Chosen as a family. The four endings are each a ring around something (a tick, a bar, a
	 * square, a cross), so they read as four answers to one question; the three that are not
	 * endings are not rings. `filled` follows the app's rule: filled is "this is the state",
	 * outlined is a state still in motion, which is why the two waiting states are outlined.
	 *
	 * `running` has no entry and cannot have one: its mark is a turning arc, not a glyph. See the
	 * render below.
	 */
	const MARKS: Record<Exclude<BadgeState, 'running'>, { name: IconName; filled: boolean }> = {
		queued: { name: 'playlist_add_check', filled: false },
		/* Held where it stands, by somebody, and it is grey for the same reason `canceled` is:
		   nothing went wrong and nothing is being asked of anyone. Outlined rather than filled,
		   because a pause is a state still in motion: the work has not ended, it is waiting to
		   be let go again, which is the rule the two waiting states already follow. */
		paused: { name: 'pause', filled: false },
		done: { name: 'check_circle', filled: true },
		blocked: { name: 'do_not_disturb_on', filled: true },
		/* The shield with a question in it, not the plain shield the Organize board wears. They are
		   about two different halves of the same pile: the board's card is the PLACE quarantined
		   files are kept, and a plain shield reads as "protected", which is right for a folder Sift
		   is looking after. This is the STATE of one file, and what that state means is that
		   something about it is unresolved. */
		quarantined: { name: 'gpp_maybe', filled: true },
		/*
		 * Outlined, alone among the endings. A stop is the right glyph for a cancel, but filled it
		 * reads as a failure mark beside the red `cancel` ring, and a cancel is nobody's fault. Not
		 * blaming a person for a deliberate act outweighs the filled-means-final rule here.
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
		 * Say more than the state does, when the screen knows more: "Waiting for cookies" rather than
		 * "Blocked". The colour is still the state's: only the word changes.
		 */
		label?: string;
		/**
		 * A mark of this screen's own, for the same reason `label` exists.
		 *
		 * The state decides the mark, exactly as it decides the word, and on the two occasions a
		 * screen genuinely knows more than the state does, it says so here. There are three, all on
		 * the download queue: a download is only ever blocked ON COOKIES, so it draws the waiting
		 * glyph rather than the general stop; and `skipped` and `duplicate` are not really
		 * cancellations at all, they are a step past and a repeat, so they carry their own marks over
		 * the colour that grey state gives them.
		 *
		 * This is deliberately NOT a way to restyle a badge. If two screens want different marks for
		 * the same fact, one of them is wrong, and that is the whole failure this component exists
		 * to prevent.
		 */
		icon?: IconName;
		/** Whether that mark is filled. See `MARKS`: filled is a state, outlined is one still moving. */
		iconFilled?: boolean;
		/**
		 * Turn the mark into a spinner, whatever the state is.
		 *
		 * `running` already does this and needs no help. See the render below. This is for the rare
		 * case of some OTHER state being actively worked on, and it stays opt-in because a list of two
		 * hundred rows all spinning is a screen nobody can read.
		 */
		busy?: boolean;
		/**
		 * Drop the word and keep the mark.
		 *
		 * For a dense row where the state is already named by its column. The word still reaches a
		 * screen reader; only the drawing loses it.
		 */
		wordless?: boolean;
		/**
		 * Drop the ground and keep the mark and the word.
		 *
		 * For a badge that is already sitting inside something painted in this same state's colour:
		 * the Jobs screen's tallies, which are the seven states as pressable filters. A pill inside a
		 * pill in the same green is not a state, it is a mistake, and the outer one is the thing being
		 * pressed.
		 */
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

	/* The mark this badge draws, or nothing at all while it is turning.
	 *
	 * `running` is the state with no glyph: what it wants said is not "this is working" but "this is
	 * working RIGHT NOW", and a static mark beside the words "In progress" looks identical whether
	 * the queue is turning or wedged. So the arc IS its mark rather than an extra shown over one.
	 */
	const mark = $derived.by(() => {
		if (busy || state === 'running') return null;
		const fallback = MARKS[state];
		return { name: icon ?? fallback.name, filled: iconFilled ?? (icon ? false : fallback.filled) };
	});
</script>

<span class="badge state-{state}" class:wordless class:plain>
	<!--
		The arc is 10 against a glyph box of 16. A Material Symbol is drawn on a 24-unit grid with
		the symbol occupying about 20 units, so a `check_circle` at 16px paints a disc about 12px
		across; a spinner's size is its outer diameter. Measure the element, not the ink it paints:
		the arc is three coloured borders and one transparent one, so its ink stops short by its
		border width. At 12 the outer edges coincide, but the arc is ink to its very edge while the
		glyph's disc stops short of its box, so it still reads as the largest thing in a row of
		pills; 10 is the scale's rung for this place.
	-->
	{#if mark}
		<Icon name={mark.name} size={16} filled={mark.filled} />
	{:else}
		<Spinner size={10} />
	{/if}{#if wordless}<span class="said">{word}</span>{:else}<span class="word">{word}</span>{/if}
</span>

<style>
	/* The same five declarations `Chip` centres itself with, in the same order. A badge and a chip
	   sit beside each other on a job row, and two pills whose contents sit at two different heights
	   is the kind of difference nobody can name and everybody can see. `line-height: 1` matters
	   most: without it the text box carries descender depth these labels never use, and
	   the words ride low inside the pill. */
	.badge {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: var(--space-1);
		line-height: 1;
		/* The same height a small chip is, read from the same token.
		 *
		 * Not a hard 20px: the mark is a 16px glyph, and 20 leaves it two pixels of air.
		 * `--chip-height-sm` is 22 and is what the
		 * scale calls this size, and a badge and a chip sit beside each other on a job row, which is
		 * the whole reason `pills.test.ts` exists. Two pills a pixel apart on one line is the thing
		 * nobody can name and everybody sees. */
		block-size: var(--chip-height-sm);
		padding-inline: var(--space-2);
		border-radius: var(--radius-full);
		font: var(--text-label);
		white-space: nowrap;
		flex: none;
		/* Never wider than what it stands in. A pill in a list's column is as wide as its words,
		   and words longer than the column would run on over the next one (a running task's state
		   under Activity's Time left). Capped here, the words are cut short with an ellipsis inside
		   the pill; a screen that can be that narrow puts the whole of them on the hover. */
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

	/*
	 * The colour is the state's, declared once in `app.css`. The `.state-*` classes set two custom
	 * properties that inherit, so the badges and the Jobs tallies share one mapping and nothing
	 * here holds an opinion about which green "done" is.
	 */
	.badge {
		background: var(--state-bg);
		color: var(--state-ink);
	}

	/* Inside something already painted in this state's colour. See `plain`. */
	.badge.plain {
		background: none;
		padding-inline: 0;
	}

	/*
	 * Nothing here makes the running badge move: its mark is the spinner, which turns on its own
	 * and answers reduced-motion where it is declared.
	 */
</style>
