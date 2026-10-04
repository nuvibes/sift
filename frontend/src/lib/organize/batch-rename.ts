// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * Renaming a batch of files from one template: what the sheet asks the server, and the words it
 * says about the answer.
 *
 * The plan is the server's, every name of it. The sheet shows what the server would do and never
 * works a name out itself: a preview filled in here would be a second copy of the naming words
 * that agrees with the real one until the day it does not, and the day it does not is a thousand
 * files named something nobody saw.
 */
import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { counted, filesSaid } from '$lib/entity/entity-counts';

export type RenamePreview = components['schemas']['RenamePreview'];
export type RenameRow = components['schemas']['RenameRow'];
type RenameDone = components['schemas']['RenameBatchDone'];
export type OnClash = 'number' | 'skip';

/** The most files one batch takes. The server refuses more, so the sheet never sends more. */
export const MOST_FILES = 1000;

/** What the box starts with: every file keeps its name and takes its place in the batch. */
export const STARTING_TEMPLATE = '{name} {n}';

/** What the box starts with for one file: its own name, since one file has no place in a batch. */
const ONE_FILE_TEMPLATE = '{name}';

/** The template a batch of this many files starts from. */
export function startingTemplate(count: number): string {
	return count === 1 ? ONE_FILE_TEMPLATE : STARTING_TEMPLATE;
}

/** The two answers to a name that is already taken, in the words the sheet offers them. */
export const ON_CLASH: { value: OnClash; label: string }[] = [
	{ value: 'number', label: 'Add the next number' },
	{ value: 'skip', label: 'Leave that file as it is' }
];

/** The order the words are offered in: the ones people reach for first. */
export const WORD_ORDER = ['name', 'n', 'creator', 'site', 'title', 'date', 'time', 'posted', 'id'];

export async function previewRename(
	assetIds: readonly string[],
	template: string,
	onClash: OnClash
): Promise<RenamePreview> {
	return api.post<RenamePreview>('/organize/rename/preview', {
		body: { asset_ids: [...assetIds], template, on_clash: onClash }
	});
}

export async function applyRename(
	assetIds: readonly string[],
	template: string,
	onClash: OnClash
): Promise<RenameDone> {
	return api.post<RenameDone>('/organize/rename', {
		body: { asset_ids: [...assetIds], template, on_clash: onClash }
	});
}

/**
 * The files a folder shows, in name order, up to `MOST_FILES`: a folder as a batch.
 *
 * Read through the wall's own list, so a file the viewer may not see is not in the batch, and the
 * order `{n}` counts in is the order the folder is read in.
 */
export async function folderFiles(folderId: string): Promise<string[]> {
	const ids: string[] = [];
	for (let offset = 0; offset < MOST_FILES; offset += PAGE) {
		const page = await api.get<components['schemas']['AssetPageResponse']>('/assets', {
			query: { in: folderId, depth: 'direct', sort: 'name_az', limit: PAGE, offset }
		});
		ids.push(...page.items.map((item) => item.id));
		if (page.items.length < PAGE || ids.length >= page.total) break;
	}
	return ids.slice(0, MOST_FILES);
}

/** One page of a folder, the largest the list serves. */
const PAGE = 200;

/** Between two words a pressed word lands beside. */
const SEPARATOR = ' ';
/** Text that ends in a word or a closing brace, so the next word needs a separator before it. */
const ENDS_IN_A_WORD = /[\p{L}\p{N}}]$/u;
/** Text that starts with a word or an opening brace, so it needs a separator after one. */
const STARTS_WITH_A_WORD = /^[\p{L}\p{N}{]/u;

/**
 * A word put where the cursor is, with a space between it and a word beside it, and where the
 * cursor goes after it. Two presses make `{name} {n}`, never `{name}{n}`.
 */
export function insertWord(
	text: string,
	from: number,
	to: number,
	word: string
): { text: string; caret: number } {
	const before = text.slice(0, from);
	const after = text.slice(to);
	const lead = ENDS_IN_A_WORD.test(before) ? SEPARATOR : '';
	const token = `{${word}}`;
	const tail = STARTS_WITH_A_WORD.test(after) ? SEPARATOR : '';
	return {
		text: before + lead + token + tail + after,
		caret: before.length + lead.length + token.length
	};
}

/**
 * What the plan will do, in one or two sentences a person can act on: how many files change, what
 * the clashes do, and how many keep their names.
 */
export function summary(preview: RenamePreview, onClash: OnClash): string {
	const said: string[] = [];
	if (preview.renaming === 0) said.push('No file gets a new name.');
	else if (preview.renaming === preview.total)
		said.push(
			preview.total === 1
				? 'The file gets a new name.'
				: `All ${filesSaid(preview.total)} get new names.`
		);
	else said.push(`${counted(preview.renaming)} of ${filesSaid(preview.total)} get new names.`);
	if (preview.numbered > 0)
		said.push(
			preview.numbered === 1
				? 'One name was taken, so it gets the next number.'
				: `${counted(preview.numbered)} names were taken, so they get the next number.`
		);
	const leftAlone = preview.clashes - preview.numbered;
	if (onClash === 'skip' && leftAlone > 0)
		said.push(
			leftAlone === 1
				? 'One name is taken, so that file keeps its name.'
				: `${counted(leftAlone)} names are taken, so those files keep theirs.`
		);
	if (preview.refused > 0)
		said.push(
			preview.refused === 1
				? "One file can't be renamed."
				: `${counted(preview.refused)} files can't be renamed.`
		);
	return said.join(' ');
}

/** The word beside a row whose file does not simply get the name the template made. */
export function rowNote(row: RenameRow): string | null {
	switch (row.state) {
		case 'numbered':
			return 'Name taken, numbered';
		case 'taken':
			return 'Name taken';
		case 'twice':
			return 'Another file takes it';
		case 'same':
			return 'Keeps its name';
		case 'refused':
			return row.reason ?? "Can't be renamed";
		default:
			return null;
	}
}
