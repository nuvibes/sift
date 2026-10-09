/*
 * Answers a person gave once that follow them to every machine, in one document read once. A key
 * that cannot be read reads as unanswered, which keeps every guard asking.
 */
import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { settingChanges } from '$lib/library/changes.svelte';

const held = $state<Record<string, string>>({});

let reading: Promise<void> | null = null;
/** A list must not be rebuilt from nothing and written back over the account's copy. */
let unread = false;

export function recallInterfaceState(): Promise<void> {
	if (reading) return reading;
	reading = (async () => {
		try {
			const answer = await api.get<components['schemas']['InterfaceState']>('/settings/interface');
			for (const [key, value] of Object.entries(answer.state)) held[key] = value;
		} catch {
			unread = true;
		}
	})();
	return reading;
}

function valueOf(key: string): string {
	return held[key] ?? '';
}

export function rereadInterfaceState(): void {
	reading = null;
	unread = false;
	for (const key of Object.keys(held)) delete held[key];
}

/* An answer given in another window follows this one, on the settings bell. */
async function readAgain(): Promise<void> {
	if (reading === null || unread) return;
	try {
		const answer = await api.get<components['schemas']['InterfaceState']>('/settings/interface');
		for (const key of Object.keys(held)) if (!(key in answer.state)) delete held[key];
		for (const [key, value] of Object.entries(answer.state)) held[key] = value;
	} catch {
		// What was read stands; the next move says so again.
	}
}

settingChanges.subscribe(() => void readAgain());

export async function interfaceState(): Promise<Record<string, string>> {
	await recallInterfaceState();
	if (unread) throw new Error("the interface state couldn't be read");
	return { ...held };
}

function remember(key: string, value: string): void {
	held[key] = value;
	void api.put('/settings/interface', { body: { state: { [key]: value } } }).catch(() => {
		// Written here, not there; the next press asks again.
	});
}

const CHIP_REMOVE = 'confirm.chip_remove';
const SKIP = 'skip';

/** False until told otherwise, including while the read is in flight. */
export function chipRemoveSkipped(): boolean {
	return valueOf(CHIP_REMOVE) === SKIP;
}

export function skipChipRemoveConfirm(): void {
	remember(CHIP_REMOVE, SKIP);
}

/** The way back: a guard that can be switched off must come back on. */
export function askBeforeChipRemove(): void {
	remember(CHIP_REMOVE, '');
}

/* ONE KEY for ignoring and removing a face: the same question on one strip. */
const FACE_REMOVAL = 'confirm.face_removal';

export function faceRemovalConfirmSkipped(): boolean {
	return valueOf(FACE_REMOVAL) === SKIP;
}

export function skipFaceRemovalConfirm(): void {
	remember(FACE_REMOVAL, SKIP);
}

export function askBeforeFaceRemoval(): void {
	remember(FACE_REMOVAL, '');
}

/* Recency, not a count: last month's project would stay on top for weeks. */
const RECENT_FOLDERS = 'recent.folders';

export const RECENT_FOLDERS_KEPT = 5;

export function recentFolders(): string[] {
	return valueOf(RECENT_FOLDERS)
		.split(',')
		.filter((id) => id !== '');
}

export function noteFolderUse(folderId: string): void {
	if (folderId === '') return;
	const kept = [folderId, ...recentFolders().filter((id) => id !== folderId)].slice(
		0,
		RECENT_FOLDERS_KEPT
	);
	remember(RECENT_FOLDERS, kept.join(','));
}

/* `open` or `shut`: the default is open, so an absent key must read as it. */
const POPOUT_EXPANDED = 'popout.expanded';
const OPEN = 'open';
const SHUT = 'shut';

/** Starting folded and unfolding a moment later reads as broken. */
export function popoutExpanded(): boolean {
	return valueOf(POPOUT_EXPANDED) !== SHUT;
}

export function rememberPopoutExpanded(expanded: boolean): void {
	remember(POPOUT_EXPANDED, expanded ? OPEN : SHUT);
}

/* Leaving the popout for another page keeps the clip going; on by default. */
const POPOUT_TO_MINI = 'popout.leave_to_mini';
const ON = 'on';
const OFF = 'off';

export function popoutLeavesToMini(): boolean {
	return valueOf(POPOUT_TO_MINI) !== OFF;
}

export function rememberPopoutLeavesToMini(on: boolean): void {
	remember(POPOUT_TO_MINI, on ? ON : OFF);
}

const EXPORT_WAY = 'faces.export_way';

/** Who the export's sheet starts with: everyone (`except`) or nobody (`only`). */
export type ExportWay = 'except' | 'only';

export function exportWay(): ExportWay {
	return valueOf(EXPORT_WAY) === 'only' ? 'only' : 'except';
}

export function rememberExportWay(way: ExportWay): void {
	remember(EXPORT_WAY, way);
}
