/* The wall put away for the way back, also kept in the tab's own storage so a reload keeps it.
 * Files are written by id only, so no file name reaches the browser's storage. */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import type { Playable, SavedCell } from './cell.svelte';
import type { StoredShape } from './layouts';

/** One cell's file: the record itself only while it is kept in memory. */
interface KeptFile {
	id: string;
	at: number;
	seed: number | null;
	file?: Playable;
}

/** A wall put away: the saved-wall shape, plus each cell's file, place and shuffle. */
export interface KeptWall {
	preset: { layout: string; shape: StoredShape; strip: number; cells: SavedCell[] };
	playing: (KeptFile | null)[];
	focused: number;
	/* Whether the vault was open. A wall kept over an open vault is not taken up once it has shut. */
	vaultOpen: boolean;
}

const PREFIX = 'sift.theater.kept.';

/** Write the wall for this account, or forget it with null. */
export function storeWall(account: string, kept: KeptWall | null): void {
	try {
		if (kept === null) {
			sessionStorage.removeItem(PREFIX + account);
			return;
		}
		const playing = kept.playing.map((one) =>
			one === null ? null : { id: one.id, at: one.at, seed: one.seed }
		);
		sessionStorage.setItem(
			PREFIX + account,
			JSON.stringify({ preset: kept.preset, playing, focused: kept.focused })
		);
	} catch {
		// A browser that keeps nothing for the page starts Theater afresh after a reload.
	}
}

/**
 * Read this account's stored wall once, and forget it. `vaultOpen` is false because the server
 * judges every file again as each cell asks for it.
 */
export function takeStoredWall(account: string): KeptWall | null {
	let raw: string | null;
	try {
		raw = sessionStorage.getItem(PREFIX + account);
		sessionStorage.removeItem(PREFIX + account);
	} catch {
		return null;
	}
	if (!raw) return null;
	try {
		return readWall(JSON.parse(raw));
	} catch {
		return null;
	}
}

/* Another version may have written it, so every field is checked before it is trusted. */
function readWall(value: unknown): KeptWall | null {
	if (typeof value !== 'object' || value === null) return null;
	const { preset, playing, focused } = value as Record<string, unknown>;
	if (typeof preset !== 'object' || preset === null || !Array.isArray(playing)) return null;
	const { layout, shape, strip, cells } = preset as Record<string, unknown>;
	if (typeof layout !== 'string' || !Array.isArray(cells) || !cells.every(isCell)) return null;
	return {
		preset: {
			layout,
			shape: shape as StoredShape,
			strip: typeof strip === 'number' ? strip : 0,
			cells
		},
		playing: playing.map(readFile),
		focused: typeof focused === 'number' ? focused : 0,
		vaultOpen: false
	};
}

/* Each word of a cell is checked again as the cell takes it; only its outline is checked here. */
function isCell(value: unknown): value is SavedCell {
	return (
		typeof value === 'object' && value !== null && typeof (value as SavedCell).source === 'string'
	);
}

function readFile(value: unknown): KeptFile | null {
	if (typeof value !== 'object' || value === null) return null;
	const { id, at, seed } = value as Record<string, unknown>;
	if (typeof id !== 'string' || !id) return null;
	return {
		id,
		at: typeof at === 'number' && Number.isFinite(at) && at > 0 ? at : 0,
		seed: typeof seed === 'number' ? seed : null
	};
}

/**
 * A kept file asked of the server again, so its name is today's and Hidden is obeyed: null when it
 * is gone, not this account's, or Hidden's placeholder, which has no kind.
 */
export async function keptFile(asked: string): Promise<Playable | null> {
	let detail: components['schemas']['AssetDetail'];
	try {
		detail = await api.get<components['schemas']['AssetDetail']>(`/assets/${asked}`);
	} catch {
		return null;
	}
	if (!detail.media_type) return null;
	const { id, media_type, duration_ms, thumb, art, original_filename, width, height } = detail;
	const { favorite, rating, concealed } = detail;
	return {
		id,
		media_type,
		duration_ms,
		thumb,
		art,
		original_filename,
		width,
		height,
		favorite,
		rating,
		concealed
	};
}
