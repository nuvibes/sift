/*
 * Every screen that mounts the player says which screen it is.
 *
 * The player is one component in the panel and in the corner, and it cannot tell which it is in,
 * so where a sitting happened is HANDED to it, and a frame that forgets to hand it over records
 * every sitting it plays as happening nowhere. Nothing fails when that happens: the report is
 * still accepted, the column is simply NULL, and it cannot be filled in afterwards. That silence is
 * why this is a test and not a comment.
 *
 * The test harness is left out on purpose: it mounts the player to test the PLAYER, and says where
 * only when a test asks it to.
 */

import { describe, expect, it } from 'vitest';

/**
 * Every report of a sitting in one file's text that does not say where it happened.
 *
 * A report is a post to a file's `/view`; its body says where when it carries the screen, either
 * written out or spread in from a place (`...place`, `...sittingPlace`). The body is read up to the
 * call's closing `})`, which every sender writes as one call.
 */
function silentReports(text: string): number {
	let silent = 0;
	for (const call of text.matchAll(/\.post\(\s*`\/assets\/\$\{[^}]+\}\/view`([\s\S]*?)\}\s*\)/g)) {
		if (!/\bscreen:|\.\.\.(place|sittingPlace)\b/.test(call[1])) silent += 1;
	}
	return silent;
}

/** Every sitting begun in one file's text without a place: `sitting.start(id)` with no second. */
function placelessStarts(text: string): number {
	let silent = 0;
	for (const call of text.matchAll(
		/\b(?:sitting|still)\.start\(([^()]*(?:\([^()]*\))?[^()]*)\)/g
	)) {
		if (!call[1].includes(',')) silent += 1;
	}
	return silent;
}

/** Every `<Player ... />` in one file's text that does not say where it is. */
function silentMounts(text: string): number {
	let silent = 0;
	for (const mount of text.matchAll(/<Player\b([\s\S]*?)\/>/g)) {
		if (!/\bplace=|\{place\}/.test(mount[1])) silent += 1;
	}
	return silent;
}

describe('where a sitting happened', () => {
	it('is said by every screen that mounts the player', async () => {
		const { readFileSync, readdirSync } = await import('node:fs');
		const { join, relative, resolve } = await import('node:path');
		const { fileURLToPath } = await import('node:url');
		const root = resolve(fileURLToPath(import.meta.url), '..', '..', '..', '..');

		const mounts: string[] = [];
		const silent: string[] = [];
		const reporters: string[] = [];
		const silentReporters: string[] = [];
		const walk = (at: string): void => {
			for (const entry of readdirSync(at, { withFileTypes: true })) {
				const here = join(at, entry.name);
				if (entry.isDirectory()) walk(here);
				else if (/\.test\.|Harness\.svelte$/.test(entry.name)) continue;
				else if (/\.(svelte|ts)$/.test(entry.name)) {
					const text = readFileSync(here, 'utf8');
					const where = relative(root, here).split('\\').join('/');
					if (/<Player\b/.test(text)) mounts.push(where);
					if (silentMounts(text) > 0) silent.push(where);
					if (/\/view`/.test(text)) reporters.push(where);
					if (silentReports(text) + placelessStarts(text) > 0) silentReporters.push(where);
				}
			}
		};
		walk(root);

		// A known positive for the walk itself: the two frames that play a file are both found.
		expect(mounts).toEqual(
			expect.arrayContaining([
				'lib/components/AssetView.svelte',
				'lib/components/player/MiniPlayer.svelte'
			])
		);
		expect(silent, 'these mount the player without saying which screen it is on').toEqual([]);

		// And every screen that reports a sitting of its own: the player, a picture's sitting, and
		// a Theater cell. A new one is found by the walk and held to the same rule.
		expect(reporters).toEqual(
			expect.arrayContaining([
				'lib/components/player/Player.svelte',
				'lib/player/sitting.svelte.ts',
				'lib/theater/wall.svelte.ts'
			])
		);
		expect(silentReporters, 'these report a sitting without saying where it happened').toEqual([]);
	});

	it('is caught when a report or a sitting forgets it', () => {
		const said = 'api.post(`/assets/${id}/view`, { body: { ...place, watch_ms: 0 } })';
		const written = "api.post(`/assets/${id}/view`, { body: { screen: 'theater' } })";
		const forgot = 'api.post(`/assets/${id}/view`, { body: { watch_ms: 0 } })';
		expect(silentReports(said) + silentReports(written)).toBe(0);
		expect(silentReports(forgot)).toBe(1);
		expect(placelessStarts('sitting.start(wanted, panelPlace(wanted))')).toBe(0);
		expect(placelessStarts('still.start(showing.id)')).toBe(1);
	});

	it('is caught when a mount forgets it', () => {
		expect(silentMounts('<Player id={x} compact />')).toBe(1);
		expect(silentMounts('<Player id={x} {place} />')).toBe(0);
		expect(silentMounts('<Player id={x} place={CORNER} />')).toBe(0);
		expect(silentMounts('<PlayerBar id={x} />'), 'the bar is not the player').toBe(0);
	});
});
