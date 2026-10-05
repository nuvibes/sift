// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * A Theater wall's presses that only the phone makes, and what the wall reports about its drawer.
 *
 * ## Why these are here and not in the screen's table
 *
 * The screen's table (`routes/theater/+page.svelte`) answers the keyboard and the phone from one
 * set of rows, and the rows a key reaches stay there. These are the rest of what the wall's bar
 * and a cell's drawer offer: the scrubber and the volume slider (a number, which no key carries),
 * the drawer's presses that have no letter (shuffle, Save as Loop, Randomize, the size, Hear only
 * this, the timer), and the two lists the bar chooses from (the layouts and the saved presets).
 * Each acts exactly as its control on the desk does, through the same wall and cell methods, so a
 * press on the phone and a press at the desk cannot come to mean two things. The screen spreads
 * them into its table; none has a shortcut, so the keyboard never asks one.
 *
 * ## What a press acts on
 *
 * The rule the desk's controls follow: what the wall is addressing (the chosen cell, or every
 * cell after Every cell), except where the thing is one clip's (the scrubber, the A-B loop and
 * its Save, the size), which is the cell the bar is drawing.
 */
import { clipTheStretch } from '$lib/edit/edit.svelte';
import { LAYOUTS, type LayoutId } from '$lib/theater/layouts';
import { pressShuffle } from '$lib/theater/orders';
import { presets } from '$lib/theater/presets.svelte';
import type { Cell } from '$lib/theater/cell.svelte';
import type { Wall } from '$lib/theater/wall.svelte';
import { toasts } from '$lib/shell/toasts.svelte';
import type { Actions, TheaterAction } from '$lib/shell/shortcuts';
import type { ScreenState } from './offer.svelte';

/** The one cell the bar is drawing: what a position, a loop or a size belongs to. */
function drawn(wall: Wall): Cell | undefined {
	return wall.cells[Math.min(wall.focused, wall.cells.length - 1)];
}

/** Whether a cell's A-B loop is marked at both ends on the file it is showing. */
function savable(cell: Cell | undefined): cell is Cell {
	return (
		cell !== undefined &&
		cell.playing !== null &&
		cell.loop.owns(cell.playing.id) &&
		cell.loop.running &&
		cell.loop.a !== null &&
		cell.loop.b !== null
	);
}

/**
 * Keep a cell's marked stretch as a Loop: the drawer's Save as Loop, with its words.
 *
 * One save at a time per wall's worth of presses: a second press while one is on its way would
 * make a second copy of the same stretch.
 */
let saving = false;

async function saveTheLoop(cell: Cell): Promise<void> {
	const file = cell.playing;
	const from = cell.loop.a;
	const to = cell.loop.b;
	if (saving || file === null || from === null || to === null) return;
	saving = true;
	try {
		const startMs = Math.round(from * 1000);
		const cut = await clipTheStretch(file.id, startMs, Math.round(to * 1000) - startMs, {
			asLoop: true
		});
		toasts.show(
			cut.made ? 'Saving it as a loop \u2014 exactly the stretch you marked' : cut.because,
			{ tone: cut.made ? 'success' : 'error' }
		);
	} finally {
		saving = false;
	}
}

/** The presses only the phone makes. See the head of this file. */
export function wallPresses(wall: Wall): Actions<TheaterAction> {
	return {
		'theater.seekTo': ({ key, value }) => {
			const cell = drawn(wall);
			if (key !== null || value === null || !cell?.seek) return false;
			cell.seek(Math.min(value, cell.duration || value));
			return true;
		},
		'theater.volumeTo': ({ key, value }) => {
			if (key !== null || value === null) return false;
			wall.addressed.forEach((cell) => (cell.volume = Math.min(100, Math.max(0, value))));
			return true;
		},
		'theater.shuffle': ({ key, value }) => {
			const cell = drawn(wall);
			if (key !== null || value === null || cell === undefined) return false;
			const shuffling = cell.ordering === 'shuffle';
			if (shuffling !== (value === 1)) pressShuffle(wall.addressed, shuffling);
			return true;
		},
		'theater.saveLoop': ({ key }) => {
			const cell = drawn(wall);
			if (key !== null || !savable(cell)) return false;
			void saveTheLoop(cell);
			return true;
		},
		'theater.random': ({ key }) => {
			if (key !== null || wall.addressed.length === 0) return false;
			wall.addressed.forEach((cell) => void cell.somethingElse());
			return true;
		},
		'theater.quality': ({ key, value }) => {
			const cell = drawn(wall);
			const rungs = cell?.plan?.qualities ?? [];
			const rung = value === null || rungs.length < 2 ? undefined : rungs[value];
			if (key !== null || !rung || !cell?.changeQuality) return false;
			cell.changeQuality(rung);
			return true;
		},
		'theater.solo': ({ key }) => {
			if (key !== null || wall.cells.length === 0) return false;
			wall.solo(Math.min(wall.focused, wall.cells.length - 1));
			return true;
		},
		// Zero is no timer, as the drawer's field reads it: the cell waits for the file to end.
		'theater.timer': ({ key, value }) => {
			if (key !== null || value === null) return false;
			wall.addressed.forEach((cell) => (cell.timerSeconds = value > 0 ? value : null));
			return true;
		},
		'theater.layout': ({ key, value }) => {
			const layout = value === null ? undefined : LAYOUTS[value];
			if (key !== null || layout === undefined) return false;
			wall.setLayout(layout.id as LayoutId);
			return true;
		},
		// The preset's place in the list this wall reported, which is the store's own order.
		'theater.preset': ({ key, value }) => {
			const kept = value === null ? undefined : presets.items[value];
			if (key !== null || kept === undefined) return false;
			wall.adopt(kept);
			return true;
		}
	};
}

/**
 * What the wall reports beyond playing: the drawer of the cell the bar is drawing, the bar's two
 * lists, and what each cell shows. The presets are asked for once, the first time a wall reports,
 * so the list is there to offer; the wall offers them from the moment they arrive.
 */
let askedForPresets = false;

export function wallState(wall: Wall): Partial<ScreenState> {
	if (!askedForPresets) {
		askedForPresets = true;
		void presets.ensure().catch(() => {
			// Nothing to do: a wall whose presets could not be read offers none to the phone, and
			// the Saved Layouts panel asks again when it is opened.
		});
	}
	const cell = drawn(wall);
	const rungs = cell?.plan?.qualities ?? [];
	const mine = cell?.playing ? cell.loop.owns(cell.playing.id) : false;
	const layout = wall.layout === null ? -1 : LAYOUTS.findIndex((one) => one.id === wall.layout);
	return {
		repeat: cell?.endBehaviour ?? null,
		shuffle: cell ? cell.ordering === 'shuffle' : null,
		loop_marks: !cell || !mine || cell.loop.a === null ? 0 : cell.loop.b === null ? 1 : 2,
		qualities: rungs.length > 1 ? rungs.map((rung) => rung.label) : [],
		quality:
			rungs.length > 1 && cell
				? rungs.findIndex((rung) => rung.url === (cell.quality?.url ?? cell.plan?.url))
				: null,
		timer: cell?.timerSeconds ?? null,
		every_cell: wall.everyCell,
		cell_held: cell ? cell.paused : null,
		cell_muted: cell ? cell.muted : null,
		layouts: LAYOUTS.map((one) => one.id),
		layout: layout < 0 ? null : layout,
		presets: presets.items.map((kept) => kept.name),
		cell_files: wall.cells.map((one) => one.playing?.id ?? null)
	};
}
