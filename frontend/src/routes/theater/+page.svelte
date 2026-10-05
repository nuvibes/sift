<script lang="ts">
	/* LAYS ITSELF OUT: the wall is a flex column that fills the box, with no page frame and no
	   padding: several videos at once want the whole screen, and inside the shell's scrolling
	   region the column would have no definite height to fill. */

	/* WHY NOT FRAMED: the frame's body is a scrolling region with a foot of `--page-pad` under it,
	   and this screen needs neither. A wall of playing feeds must be exactly the height of the box
	   and must never scroll (a cell scrolled off the screen is a video still playing where nobody
	   can see it), and FILLED it has to reach all four edges of the window, which the frame cannot
	   express: `bleed` takes the side padding away and deliberately keeps the foot, because a pager
	   on a bleeding wall still lines up with the title. So a framed Theater would leave a band of
	   page under a full-screen wall. Weighed against that, what the frame would give back is the
	   two numbers this file already reads from it and nothing else: the header's `inset` and
	   `--page-pad` on `.stalls`, both of them the frame's own tokens used as the frame uses them.
	   Reconsider the day the frame can be told its body does not scroll. */

	/*
	 * Theater: several videos at once, each cell drawing from a filter of its own.
	 *
	 * Everything is inside one box: fullscreen composites only the fullscreen element's subtree, so
	 * dialogs, the source picker and menus are drawn inside the wall, and cells are never reparented
	 * (moving a node closes what is open on it). At a phone's width (`phoneWidth`) it says it needs a
	 * wider window; a phone drives the desk's walls from the Remote (`THEATER_ON_A_PHONE`).
	 */
	import { editingCell, narrowingFor, narrowingName } from '$lib/theater/narrowing';
	import { onDestroy, onMount } from 'svelte';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import { Empty } from '$lib/components/common';
	import { WIDER_WINDOW_TITLE } from '$lib/shell/wider-window';
	import CellChoice from '$lib/components/theater/CellChoice.svelte';
	import TheaterWall from '$lib/components/theater/TheaterWall.svelte';
	import Shortcuts from '$lib/components/theater/Shortcuts.svelte';
	import TheaterTools from '$lib/components/theater/TheaterTools.svelte';
	import PresetsPanel from '$lib/components/theater/PresetsPanel.svelte';
	import SaveLayout from '$lib/components/theater/SaveLayout.svelte';
	import { screenBar, type Narrowing } from '$lib/components/shell/screen-bar.svelte';
	import LayoutGlyph from '$lib/components/theater/LayoutGlyph.svelte';
	import { LAYOUTS, layout, type LayoutId } from '$lib/theater/layouts';
	import { stage } from '$lib/components/shell/stage.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { mini } from '$lib/player/mini.svelte';
	import { ACTS, keyOf } from '$lib/player/acts';
	import { rowQuiet, wallChrome } from '$lib/theater/chrome.svelte';
	import { vault } from '$lib/shell/vault.svelte';
	import { toCorner } from '$lib/theater/corner';
	import {
		applyOrder,
		CELL_DEFAULT_ORDER,
		cellOrders,
		NO_CELL_TO_ORDER
	} from '$lib/theater/orders';
	import { showing } from '$lib/theater/wall.svelte';
	import { presets } from '$lib/theater/presets.svelte';
	import { page } from '$app/state';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import {
		matches,
		pressed,
		shortcut,
		type Actions,
		type Asked,
		type TheaterAction
	} from '$lib/shell/shortcuts';
	import { screenOffer } from '$lib/remote/offer.svelte';
	import { wallPresses, wallState } from '$lib/remote/wall-remote';
	import {
		loopModeAt,
		loopModeIcon,
		loopModeLabel,
		nextLoopMode,
		loopRepeats
	} from '$lib/player/loop-modes';
	import { SKIP_SECONDS } from '$lib/player/skip';
	import type { Cell } from '$lib/theater/cell.svelte';
	import {
		muteEcho,
		noteEcho,
		rateWords,
		skipEcho,
		volumeEcho,
		type CellEcho,
		type Echo
	} from '$lib/theater/echoes';
	import { tapHold } from '$lib/theater/taps';

	/*
	 * The wall, which is NOT this screen's to own: the corner panel keeps it running when Theater
	 * is left, so both say when they are done and the last one releases it.
	 */
	const wall = showing.ensure();

	/** Whether this is the desk's screen rather than the phone's: the shell's one reading of it. */
	const roomy = $derived(!phoneWidth.yes);

	/** Why a phone gets no wall, under the title every such refusal wears. */
	const PHONE_REFUSAL =
		"Several videos side by side don't fit on a screen this size. Open it on a computer or a tablet.";
	/** Whether the vault was open last time this looked, so a relock can be told from an unlock. */
	let wasUnlocked = vault.unlocked;

	/*
	 * Theater's controls, on the bar every other screen uses, which the filled box holds too, so
	 * there is no second row inside the wall.
	 */
	const mine = Symbol('theater-screen');

	/*
	 * Which cell the shared filter panel is editing: the one whose filter was pressed, else the one
	 * the keyboard is on (`$lib/theater/narrowing`).
	 */
	const editing = $derived(editingCell(wall));

	/* The orders offered, which gains a `Shuffle again` row while the cell being read is already
	   shuffled. Worked out in `$lib/theater/orders`, where it can be tested. */
	const orders = $derived(
		cellOrders(wall.cells[editing]?.sort ?? null, wall.cells[editing]?.source ?? '')
	);

	/*
	 * WHAT FILTER MEANS ON THIS SCREEN: one cell's source, read from one and written to all, held
	 * and tested in `$lib/theater/narrowing`.
	 */
	const narrowing: Narrowing = narrowingFor(wall);

	$effect(() => {
		/* A PHONE: nothing of the wall's on the bar. The screen says it needs a wider window, and a
		   Filter, a Layouts or a Sound on for a wall that is not drawn would be doors into nothing. */
		if (!roomy) {
			screenBar.publish(mine, {});
			return;
		}
		screenBar.publish(mine, {
			/* Filter is LIVE here, on the cell named beside it. */
			filterable: true,
			narrowing,
			narrowingLead: whichCell,
			/* The wall's two verbs, beside the cell chip, only while filled: in a window they are on
			   the top bar, and two live copies of a control is a fault. */
			besideTheName: stage.filling ? wallVerbs : undefined,
			/* Which cell the chips are about. The row is above every cell on the wall, so `media: gif`
			   is true of one of them and says nothing at all about which. */
			narrowingName: narrowingName(wall),
			/*
			 * SORT ACTS ON THE SELECTED CELL: a wall has up to nine lists and none of its own, as with
			 * filters. The order is the cell's own query (`Cell.sort`), written to every addressed
			 * cell and read from the edited one (`$lib/theater/narrowing`).
			 */
			sorts: wall.addressed.length > 0 ? orders : NO_CELL_TO_ORDER,
			sort: wall.cells[editing]?.sort ?? CELL_DEFAULT_ORDER,
			/* What the press MEANS is `$lib/theater/orders`'s, where it is tested; nothing stores
			   `reshuffle`. */
			onSort: (next) => applyOrder(wall.addressed, next),
			resizable: 'Choose a layout, on this row',
			/* The wall's own hold, on the bar's own play control: one question, one control. */
			playable: true,
			playing: !wall.paused,
			playLabel: wall.paused ? ACTS.playEverything : ACTS.pauseEverything,
			playShortcut: keyOf(wall.paused ? 'playEverything' : 'pauseEverything', 'theater'),
			onPlay: () => wall.togglePause(),
			/*
			 * LAYOUT IS A MENU, not a panel: a list you pick one of, by this row's rule.
			 */
			menus: [
				{
					id: 'layout',
					icon: 'view_array',
					label: 'Layouts',
					options: LAYOUTS.map((one) => ({
						value: one.id,
						label: one.label,
						tooltip: one.tooltip
					})),
					/* Each shape drawn as itself, from the wall's own `grid-template`, since names one
					   character apart are different pictures. */
					preview: layoutPicture,
					value: wall.layout ?? 'side_by_side_by_side',
					onChoose: (next) => wall.setLayout(next as LayoutId)
				}
			],
			/* The wall's silence goes on the TOP bar, after the play control that asks the same question
			   about the same wall and the Hidden control the bar keeps in one place. See `topExtra`. */
			topExtra: tools,
			/* This row goes and comes back WITH the wall's own bar, unless it holds the menus in a
			   window; see `rowQuiet`. */
			quiet: rowQuiet(wallChrome.up, stage.filling, screenBar.roomOnTopBar),
			panels: [
				/*
				 * What is KEPT, and what the keys do: the two that are genuinely panels (a row of pills,
				 * a reference table).
				 */
				{
					id: 'presets',
					icon: 'table_view',
					label: 'Saved Layouts',
					content: PresetsPanel,
					lead: true
				},
				/* The keys are the APPLICATION's rather than this wall's, so the trigger sits on the top bar
				   with the other window-wide controls. What it opens still opens on the screen's own row. */
				{
					id: 'keys',
					icon: 'keyboard',
					label: 'Keyboard shortcuts',
					content: Shortcuts,
					atTheTop: true
				}
			]
		});
	});

	$effect(() => () => screenBar.release(mine));

	/*
	 * The vault has shut, so every cell lets go of what it is holding, at once and mid-file, since it
	 * may be concealed. Watched through `vault.unlocked`, the fact itself.
	 */
	$effect(() => {
		const unlocked = vault.unlocked;
		if (unlocked === wasUnlocked) return;
		wasUnlocked = unlocked;
		if (!mini.wall) wall.vaultChanged(!unlocked);
	});

	/*
	 * The one entry the operating system gets for this page: one media session per page, pointed at
	 * the cell somebody is hearing and repointed when that moves.
	 */
	$effect(() => {
		const heard = wall.heard;
		const showing = heard === null ? null : wall.all[heard].playing;
		if (!('mediaSession' in navigator)) return;
		navigator.mediaSession.metadata =
			showing === null
				? null
				: new MediaMetadata({ title: showing.original_filename ?? 'Theater', artist: 'Theater' });
		return () => (navigator.mediaSession.metadata = null);
	});

	/* And whenever a preference moves while the screen is open (`Wall.open` re-reads preferences and
	 * not the arriving state), since the Settings sheet opens over the wall it changes. */
	whenChanged(settingChanges, () => void wall.open());

	onMount(() => {
		/* Opened first, then the kept wall the address names, which replaces what the opening put
		   up. */
		void wall.open().then(openAddressedWall);
		window.addEventListener('keydown', key);
		// BOTH, and the second one is not optional: M is a modifier while it is held and a mute when
		// it is let go, so the mute lives on the way up. See `letGo`.
		window.addEventListener('keyup', letGo);
		// And the focus leaving, which is the one way a keyup never arrives. See `lostTheKeyboard`.
		window.addEventListener('blur', lostTheKeyboard);
		return () => {
			window.removeEventListener('keydown', key);
			window.removeEventListener('keyup', letGo);
			window.removeEventListener('blur', lostTheKeyboard);
			// A held key outlives the screen otherwise: a rate is put back by the release, and a
			// rewind's clock would go on seeking a cell this screen no longer draws.
			numbers.cancel();
		};
	});

	/*
	 * The saved wall an address names (`?wall=<id>`), from Insights' list of walls; after `open`, or
	 * the layout preference would reshape it.
	 */
	async function openAddressedWall(): Promise<void> {
		const id = page.url.searchParams.get('wall');
		if (!id) return;
		try {
			const kept = await presets.byId(id);
			if (kept) wall.adopt(kept);
			else toasts.show('That Saved Layout no longer exists', { tone: 'error' });
		} catch {
			toasts.show("Your Saved Layouts couldn't be loaded", { tone: 'error' });
		}
	}

	// The panel keeps it if the panel has it. A screen that released unconditionally would silence a
	// wall somebody had just popped out into the corner.
	onDestroy(() => showing.drop(mini.wall));

	/** Fill the screen. The shell's box, so the controls come with it, never one video. */
	function fullscreen() {
		stage.toggle();
	}

	/*
	 * Theater's keys. The numbers are the deliberate override of a player's percentage seek, shown
	 * on the shortcut sheet and bound only while this screen is mounted.
	 */
	/*
	 * WHETHER M IS BEING HELD: held, it modifies the arrows into volume keys; alone, it mutes, which
	 * is known only when it is LET GO with no arrow used. Repeats are ignored.
	 */
	let holdingM = false;
	/** Whether an arrow was used while M was down, which is what turns the release into nothing. */
	let usedM = false;

	/** How much the up and down keys move the volume. Ten, so a few presses cross the range. */
	const VOLUME_STEP = 10;
	/** And the two beside them, for settling on a level rather than finding one. */
	const VOLUME_NUDGE = 2;

	/*
	 * WHAT THE KEYS ACT ON: the cell the keyboard is on, or every cell (`Wall.addressed`, the
	 * backtick), so no verb needs a second binding.
	 */
	function acting() {
		return wall.addressed;
	}

	/*
	 * WHAT A KEY LAST DID AT EACH CELL, by that cell's own key: the corner badge reports a PRESS
	 * (`$lib/theater/echoes`), since a key's effect may be on a control nobody can see; per cell, so
	 * untouched cells show nothing. On this screen, since the corner panel takes no keys.
	 */
	const echoes = $state<Record<string, CellEcho>>({});

	/** Say what a press just did, on every cell it did it to. */
	function echo(cells: readonly Cell[], said: Echo): void {
		noteEcho(
			echoes,
			cells.map((cell) => cell.key),
			said
		);
	}

	/** The one cell the bar is drawing: what an absolute position or a loop mark belongs to. */
	function here() {
		return wall.cells[Math.min(wall.focused, wall.cells.length - 1)];
	}

	/*
	 * VOLUME, AND THE BADGE THAT SAYS WHERE IT LANDED: the step APPLIED, read before and after on the
	 * first addressed cell ("+0 (100)" at the top), which speaks for all (`Wall.setMuted`).
	 */
	function louder(by: number) {
		const cells = acting();
		if (cells.length === 0) return;
		const before = cells[0].volume;
		for (const cell of cells) {
			cell.volume = Math.min(100, Math.max(0, cell.volume + by));
		}
		const now = cells[0].volume;
		echo(cells, volumeEcho(before, now));
	}

	function skip(by: number) {
		const moved: Cell[] = [];
		for (const cell of acting()) {
			if (cell.seek === null) continue;
			const wanted = cell.position + by;
			cell.seek(Math.min(cell.duration || wanted, Math.max(0, wanted)));
			moved.push(cell);
		}
		echo(moved, skipEcho(by));
	}

	/*
	 * ONE FILE ON, AND ONE FILE BACK, for the step keys and the double and triple tap. The badge
	 * rises after the cell has landed and says only the direction; a cell with nothing behind it
	 * still answers, dimmed.
	 */
	async function stepOn(cell: Cell): Promise<void> {
		await cell.advance();
		echo([cell], { icon: 'skip_next', label: 'Next' });
	}

	async function stepBack(cell: Cell): Promise<void> {
		if (!cell.hasBack) {
			echo([cell], { icon: 'skip_previous', label: 'Nothing before this', muted: true });
			return;
		}
		await cell.back();
		echo([cell], { icon: 'skip_previous', label: 'Previous' });
	}

	/*
	 * THE NUMBER KEYS, WHICH CARRY FIVE VERBS EACH: the finger already names the cell, so the verb is
	 * how the key is pressed:
	 *
	 * - one tap: talk to that cell
	 * - two taps: the next file in it; three: the one before
	 * - hold the first press: slow motion, until it is let go
	 * - hold the second: faster, and faster again if the hold runs on; hold the third: backwards
	 *
	 * The cell lights on the way DOWN, without waiting out the tap window. The timers and their
	 * awkward cases live in `$lib/theater/taps`, under fake timers.
	 */

	/** How much slower a held first press plays. Half, which is the rate every player calls slow. */
	const SLOW_RATE = 0.5;
	/** How much faster a held second press plays, and what it steps up to if the hold runs on. */
	const FAST_RATE = 2;
	const FASTER_RATE = 4;
	/** How long it holds at the first rate before taking the second. */
	const FASTER_AFTER_MS = 2000;
	/** How fast a held third press runs backwards, and how often it moves the playhead. */
	const REWIND_RATE = 2;
	const REWIND_TICK_MS = 100;

	/*
	 * THE GESTURE A HELD KEY STARTED, and what letting go has to put back: plain variables, since
	 * nothing draws them and state written by a timer that reads it schedules against itself.
	 */
	let held: { cells: Cell[]; rates: number[]; rewinding: boolean } | null = null;
	let fasterTimer: ReturnType<typeof setTimeout> | undefined;
	let rewindTimer: ReturnType<typeof setInterval> | undefined;
	/** Where each rewinding cell's playhead has been walked back to, and when it was last walked. */
	let rewoundTo: number[] = [];
	let rewoundAt = 0;

	/** The one cell a number names, or none when the wall has no such cell. */
	function cellsFor(key: string): Cell[] {
		const wanted = Number(key) - 1;
		const cell = wall.cells[wanted];
		return wanted >= 0 && cell ? [cell] : [];
	}

	/** Take a rate for as long as the key is down, remembering what to give back. */
	function holdRate(cells: Cell[], rate: number, said: Echo): void {
		held = { cells, rates: cells.map((cell) => cell.rate), rewinding: false };
		for (const cell of cells) cell.rate = rate;
		echo(cells, said);
	}

	/*
	 * RUNNING BACKWARDS, which no video element can be asked to do: the playhead is walked back on a
	 * short clock toward a carried target, so a playing cell rewinds at the same rate as a stopped
	 * one. `performance.now()`, since the wall clock can step backwards.
	 */
	function rewind(cells: Cell[]): void {
		const moving = cells.filter((cell) => cell.seek !== null);
		if (moving.length === 0) return;
		held = { cells: moving, rates: moving.map((cell) => cell.rate), rewinding: true };
		rewoundTo = moving.map((cell) => cell.position);
		rewoundAt = performance.now();
		rewindTimer = setInterval(() => {
			const now = performance.now();
			const by = ((now - rewoundAt) / 1000) * REWIND_RATE;
			rewoundAt = now;
			moving.forEach((cell, at) => {
				rewoundTo[at] = Math.max(0, rewoundTo[at] - by);
				cell.seek?.(rewoundTo[at]);
			});
		}, REWIND_TICK_MS);
		echo(moving, { icon: 'fast_rewind', label: 'Rewinding', detail: rateWords(REWIND_RATE) });
	}

	/** Let go of whatever a held key started, and put the rate back where it was. */
	function letGoOfTheHold(): void {
		clearTimeout(fasterTimer);
		fasterTimer = undefined;
		clearInterval(rewindTimer);
		rewindTimer = undefined;
		rewoundTo = [];
		if (held === null) return;
		const { cells, rates, rewinding } = held;
		held = null;
		cells.forEach((cell, at) => (cell.rate = rates[at]));
		echo(cells, {
			icon: 'play_arrow',
			label: rewinding ? 'Playing' : 'Ordinary speed',
			detail: rateWords(rates[0] ?? 1)
		});
	}

	const numbers = tapHold({
		tap(run) {
			const cells = cellsFor(run.key);
			// One tap addressed the cell on the way down, which is where it happens.
			if (run.count === 2) cells.forEach((cell) => void stepOn(cell));
			if (run.count === 3) cells.forEach((cell) => void stepBack(cell));
		},
		hold(run) {
			const cells = cellsFor(run.key);
			if (cells.length === 0) return;
			if (run.count === 1) {
				holdRate(cells, SLOW_RATE, {
					icon: 'slow_motion_video',
					label: 'Slow motion',
					detail: rateWords(SLOW_RATE)
				});
				return;
			}
			if (run.count === 2) {
				holdRate(cells, FAST_RATE, {
					icon: 'fast_forward',
					label: 'Faster',
					detail: rateWords(FAST_RATE)
				});
				/* A second rate for a hold that runs on: two for a few seconds, four to get past
				   something, neither needing a key of its own. */
				fasterTimer = setTimeout(() => {
					if (held === null) return;
					for (const cell of held.cells) cell.rate = FASTER_RATE;
					echo(held.cells, {
						icon: 'fast_forward',
						label: 'Faster',
						detail: rateWords(FASTER_RATE)
					});
				}, FASTER_AFTER_MS);
				return;
			}
			if (run.count === 3) rewind(cells);
		},
		release() {
			letGoOfTheHold();
		}
	});

	/*
	 * The window losing the focus, which is the one way a keyup never arrives: the recogniser
	 * releases the hold, so the rate is put back.
	 */
	function lostTheKeyboard(): void {
		numbers.cancel();
	}

	/*
	 * M LET GO, which is where the per-cell mute actually happens; without the release `holdingM`
	 * would stay true and turn the plain arrows into volume keys.
	 */
	function letGo(event: KeyboardEvent) {
		/* The number keys first: the recogniser ignores other keys, and ending a hold puts the rate back. */
		numbers.up(event.key);
		/*
		 * The DECLARATION is asked which letters this is, as every handler here does. Disarming is
		 * asked FIRST and loosely (the physical key), the mute strictly (Ctrl+M already silenced the
		 * wall), or `holdingM` could stick true.
		 */
		if (!shortcut('theater.mute').keys.includes(event.key)) return;
		holdingM = false;
		// Held and used as a modifier, so the release means nothing on its own.
		if (usedM || !matches(event, 'theater.mute')) return;
		// One answer for all of them, decided by the cell the bar is drawing (see `Wall.setMuted`).
		const cell = wall.cells[Math.min(wall.focused, wall.cells.length - 1)];
		const silent = !(cell?.muted ?? true);
		wall.setMuted(wall.addressedAt, silent);
		echo(wall.addressed, muteEcho(silent));
	}

	/*
	 * THE WALL'S VERBS, answering the keyboard and the phone from one table (`pressed`, `commanded` in
	 * `$lib/shell/shortcuts`). Rows are matched in order where keys share a letter: cell keys first,
	 * the mutes before the volume rows (which decline unless M is held) before the arrows. A toggle
	 * from the phone sends the state it wants.
	 */
	const actions: Actions<TheaterAction> = {
		'theater.cell': ({ key, value }) => {
			if (key === null) {
				if (value === null || value >= wall.cells.length) return false;
				wall.chooseByNumber(value);
				return true;
			}
			// Which cell is the only thing read off the press itself: the shortcut is "talk to a
			// cell", and the number in it is the argument rather than a shortcut of its own.
			const wanted = Number(key.key) - 1;
			if (wanted >= wall.cells.length) return false;
			/* Auto-repeat is one key still down, not a second press, and chooses no cell again. */
			numbers.down(key.key, key.repeat);
			if (!key.repeat) wall.chooseByNumber(wanted);
			return true;
		},
		// The key beside the numbers, doing what the numbers do for all of them at once.
		'theater.everyCell': () => {
			wall.focusEvery();
			return true;
		},
		'theater.muteAll': ({ key, value }) => {
			if (key === null && value !== null && wall.masterMuted === (value === 1)) return true;
			wall.toggleMaster();
			echo(wall.cells, {
				icon: wall.masterMuted ? 'volume_off' : 'volume_up',
				label: wall.masterMuted ? 'Everything silenced' : 'Sound back',
				muted: wall.masterMuted
			});
			return true;
		},
		'theater.mute': ({ key, value }) => {
			// The phone sends the state it wants, for what the wall is addressing, at once: it has
			// no key to let go of.
			if (key === null) {
				if (value === null) return false;
				wall.setMuted(wall.addressedAt, value === 1);
				return true;
			}
			// DOWN arms it; the mute happens on the way UP. See `holdingM`.
			if (!key.repeat) {
				holdingM = true;
				usedM = false;
			}
			return true;
		},
		/*
		 * THE FOUR VOLUME KEYS, which exist only while M is down: up and down move ten, left and right
		 * move two for settling.
		 */
		'theater.louder': (asked) => louderWhileM(asked, VOLUME_STEP),
		'theater.quieter': (asked) => louderWhileM(asked, -VOLUME_STEP),
		'theater.louderABit': (asked) => louderWhileM(asked, VOLUME_NUDGE),
		'theater.quieterABit': (asked) => louderWhileM(asked, -VOLUME_NUDGE),
		'theater.previous': () => {
			acting().forEach((cell) => void stepBack(cell));
			return true;
		},
		'theater.next': () => {
			acting().forEach((cell) => void stepOn(cell));
			return true;
		},
		'theater.back': () => {
			skip(-SKIP_SECONDS);
			return true;
		},
		'theater.forward': () => {
			skip(SKIP_SECONDS);
			return true;
		},
		'theater.pauseAll': ({ key, value }) => {
			if (key === null && value !== null && wall.paused === (value === 1)) return true;
			wall.togglePause();
			echo(wall.cells, {
				icon: wall.paused ? 'pause' : 'play_arrow',
				label: wall.paused ? 'Everything stopped' : 'Everything playing',
				muted: wall.paused
			});
			return true;
		},
		'theater.pause': ({ key, value }) => {
			const cells = acting();
			if (cells.length === 0) return true;
			// The phone sends the state it wants: a cell already there is left as it is, and a held
			// wall is already holding every cell.
			const held = wall.paused || cells[0].paused;
			if (key === null && value !== null && held === (value === 1)) return true;
			// The wall's own hold wins over a cell's: starting one cell out of a stopped wall is not
			// a thing to guess at, which is the rule every other play control here follows.
			if (wall.paused) {
				wall.togglePause();
				return true;
			}
			// One answer for all of them, decided by the first: pressing this with the whole wall
			// selected does one thing to every cell rather than flipping each to its own opposite.
			const next = !cells[0].paused;
			cells.forEach((cell) => (cell.paused = next));
			echo(cells, {
				icon: next ? 'pause' : 'play_arrow',
				label: next ? 'Stopped' : 'Playing',
				muted: next
			});
			return true;
		},
		'theater.repeat': ({ key, value }) => {
			// One answer for everything this is addressing, decided by the cell the bar is drawing:
			// the same rule the control in the drawer follows. The phone names the answer it wants.
			const cells = acting();
			if (cells.length === 0) return true;
			const wanted = key === null ? loopModeAt(value) : null;
			if (key === null && wanted === null) return false;
			const next = wanted ?? nextLoopMode(cells[0].endBehaviour);
			cells.forEach((cell) => (cell.endBehaviour = next));
			/* The badge is a KEY's echo, raised only for the cells this press addressed (`KeyEcho`). */
			echo(cells, {
				icon: loopModeIcon(next),
				label: loopModeLabel(next),
				muted: !loopRepeats(next)
			});
			return true;
		},
		'theater.loop': () => {
			/* The A-B mark, on the ONE cell whose timeline the bar draws: a loop belongs to one clip. */
			const cell = here();
			if (!cell?.playing) return true;
			cell.loop.mark(cell.playing.id, cell.position);
			/* Read AFTER the mark: what it did is where the pair stands now. */
			echo([cell], {
				icon: 'all_inclusive',
				label:
					cell.loop.a === null ? 'Loop cleared' : cell.loop.b === null ? 'Loop start' : 'Loop end',
				muted: !cell.loop.running
			});
			return true;
		},
		'theater.fill': () => {
			/*
			 * NO BADGE, the one deliberately silent verb: filling the window is its own report, and
			 * `requestFullscreen` resolves later, so `stage.filling` read now would say the opposite.
			 */
			fullscreen();
			return true;
		},
		'theater.corner': () => {
			toCorner();
			return true;
		},
		'theater.keys': () => {
			screenBar.toggle('keys');
			return true;
		},
		// The presses only the phone makes: the scrubber, the volume, the drawer's unlettered
		// presses, the layouts and the presets. No shortcut, so no key ever asks one.
		...wallPresses(wall)
	};

	/** A volume key, which is one only while M is held down. See `holdingM`. */
	function louderWhileM({ key }: Asked, by: number): boolean {
		if (key === null || !holdingM) return false;
		usedM = true;
		louder(by);
		return true;
	}

	/*
	 * Offered to the signed-in user's phone while the wall is open, answered from the same table;
	 * whether this tab offers is `screenOffer`'s question.
	 */
	onMount(() =>
		screenOffer.offer({
			kind: 'theater',
			actions,
			state: () => {
				const cell = here();
				return {
					playing: !wall.paused,
					position: cell?.position ?? 0,
					length: cell && cell.duration > 0 ? cell.duration : null,
					file: cell?.playing?.id ?? null,
					volume: cell?.volume ?? 0,
					muted: wall.masterMuted,
					cells: wall.cells.length,
					focused: wall.cells.length > 0 ? Math.min(wall.focused, wall.cells.length - 1) : null,
					...wallState(wall)
				};
			}
		})
	);

	function key(event: KeyboardEvent) {
		if (pressed(event, actions)) return;
		if (matches(event, 'app.dismiss')) {
			// Leaving full screen is the browser's own answer to Escape and must stay that way.
			if (stage.filling || screenBar.open === null) return;
			event.preventDefault();
			screenBar.close();
		}
	}
</script>

<!-- The buttons that act rather than open something. A component, not bare markup: a snippet
     is styled where it is RENDERED, so buttons written here would arrive on the bar undressed. -->
{#snippet tools()}
	<TheaterTools />
{/snippet}

<!-- Which cell the filter panel is editing, a component, since a snippet is styled where rendered. -->
{#snippet whichCell()}
	<CellChoice />
{/snippet}

<!-- THE WALL'S TWO VERBS, BESIDE THE CELL CHIP WHILE THE SCREEN IS FILLED: the top bar holding
     them is not drawn then. -->
{#snippet wallVerbs()}
	<TheaterTools size="small" pauseToo />
{/snippet}

<!-- One layout, drawn as its own shape, for the chooser on the bar. -->
{#snippet layoutPicture(option: { value: string; label: string })}
	<LayoutGlyph shape={layout(option.value)} />
{/snippet}

<svelte:head><title>Theater</title></svelte:head>

<section class="screen">
	<!--
		A HANDLE, AT THE FOOT OF THE SCREEN: a sliver of the bar's surface, so the hidden controls can
		be found. On the screen, which reaches the window's bottom, not the wall. A picture of an
		affordance, not a button: pointing at it opens the bar.
	-->
	{#if !stage.filling && !mini.wall && !wallChrome.up}
		<span class="handle" aria-hidden="true">
			<Icon name="expand_circle_up" size={20} />
		</span>
	{/if}
	{#if !stage.filling}
		<!-- No title while filled. `inset`, since a screen owning its own scroll gets no layout padding. -->
		<PageHeader title="Theater" icon="interactive_space" inset />
	{/if}

	{#if !roomy}
		<!-- A PHONE: the refusal every screen too wide for a phone draws, titled the same, with its own
		     reason under it. Never the desk's wall hidden by a stylesheet. -->
		<Empty scope="page" title={WIDER_WINDOW_TITLE}>{PHONE_REFUSAL}</Empty>
	{:else}
		<div class="stalls" class:filled={stage.filling}>
			{#if mini.wall}
				<!-- It is in the corner. Said, rather than drawn as an empty screen: a wall that is
				     somewhere else looks exactly like one that failed to load. -->
				<p class="narrow">
					The wall is in the corner panel — close the panel to bring it back here.
				</p>
			{:else}
				<TheaterWall {wall} onfullscreen={fullscreen} {echoes} />
			{/if}
		</div>
	{/if}
</section>

<SaveLayout />

<style>
	.screen {
		position: relative;
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		block-size: 100%;
		min-block-size: 0;
	}

	/*
	 * The handle: a sliver of the bar's own surface (its blur and hairline), rounded at the top only,
	 * in a tab the height of the page inset under the wall, so it covers no picture.
	 */
	.handle {
		position: absolute;
		inset-block-end: 0;
		inset-inline-start: 50%;
		translate: -50% 0;
		display: flex;
		align-items: center;
		justify-content: center;
		inline-size: 102px;
		block-size: var(--page-pad);
		overflow: hidden;
		border: 1px solid var(--sift-line);
		border-block-end: none;
		border-start-start-radius: 999px;
		border-start-end-radius: 999px;
		background: var(--sift-scrim);
		backdrop-filter: blur(var(--blur-glass));
		color: var(--sift-ink-3);
		pointer-events: none;
		z-index: 2;
	}

	/*
	 * THE WALL SITS INSIDE THE PAGE in a window, inset by `--page-pad` and rounded; filled, it takes
	 * everything.
	 */
	.stalls {
		display: flex;
		flex-direction: column;
		min-block-size: 0;
		flex: 1;
		padding-inline: var(--page-pad);
		padding-block-end: var(--page-pad);
	}

	/* Rounded on the box that CLIPS, not on the wall: the wall's own corners would be rounded under
	   square feeds, which shows the ground through four notches rather than rounding anything. */
	.stalls > :global(.wall) {
		overflow: hidden;
		border-radius: var(--radius-lg);
	}

	/* Filled, the wall is the screen. The inset and the corner both go, because there is nothing
	   beside it for them to line up with. */
	.stalls.filled {
		padding: 0;
	}

	.stalls.filled > :global(.wall) {
		border-radius: 0;
	}

	.narrow {
		margin: var(--space-6) auto;
		max-inline-size: 40ch;
		color: var(--sift-ink-2);
		font: var(--text-body);
		text-align: center;
	}
</style>
