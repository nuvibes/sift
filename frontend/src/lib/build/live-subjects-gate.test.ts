/** What the live-subjects gate refuses.
 *
 * `scripts/check_live_subjects.js` holds every store and screen that reads from the server and
 * keeps the answer to naming a bell, or saying who follows for it. Each case here is a file
 * written for the purpose, run through exactly the check the build runs.
 */

import { describe, expect, it } from 'vitest';

import { judge, listens } from '../../../scripts/check_live_subjects.js';

const READS_AND_HOLDS = `
import { api } from '$lib/api/client';
export class Tunnels {
	items = $state([]);
	async load() {
		this.items = await api.get('/tunnels');
	}
}
`;

const nowhere = () => false;
const unread = () => '';

describe('the live-subjects gate', () => {
	it('refuses a store that reads, holds and names no bell', () => {
		expect(judge(READS_AND_HOLDS, nowhere, unread)).toMatch(/names no bell/);
	});

	it('passes the same store once it follows a bell', () => {
		const told = `import { settingChanges, whenChanged } from '$lib/library/changes.svelte';\n${READS_AND_HOLDS}\nfollow() { whenChanged(settingChanges, () => void this.load()); }`;
		expect(judge(told, nowhere, unread)).toBeNull();
	});

	it('does not count a bell named only in a comment', () => {
		const said = `/* re-read on settingChanges */\n${READS_AND_HOLDS}`;
		expect(listens(said)).toBe(false);
		expect(judge(said, nowhere, unread)).toMatch(/names no bell/);
	});

	it('holds a follower named in the marker to a file that listens', () => {
		const marked = `/* LIVE: followed by lib/Screen.svelte (load on the settings bell) */\n${READS_AND_HOLDS}`;
		expect(judge(marked, nowhere, unread)).toMatch(/not a file/);
		expect(
			judge(
				marked,
				() => true,
				() => '<script>let x = 1;</script>'
			)
		).toMatch(/names no bell/);
		expect(
			judge(
				marked,
				() => true,
				() => '<script>whenChanged(settingChanges, () => void tunnels.load());</script>'
			)
		).toBeNull();
	});

	it('takes a reason where nothing on the server can move what was read', () => {
		const marked = `/* LIVE: nothing moves it (the Sites that Sift supports are its code) */\n${READS_AND_HOLDS}`;
		expect(judge(marked, nowhere, unread)).toBeNull();
	});

	it('is not the business of a helper that holds nothing', () => {
		const helper = `import { api } from '$lib/api/client';\nexport const read = () => api.get('/x');`;
		expect(judge(helper, nowhere, unread)).toBeNull();
	});
});
