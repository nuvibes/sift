import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

import { OWNED, addressOf, landingFor, type ActivityTab } from './tabs';

function split(address: string): [string, string] {
	const url = new URL(address, 'http://sift.invalid');
	return [url.search, url.hash];
}

describe('The tabs of Tasks and Activity by address', () => {
	it('opens each tab at its own address, so a refresh or a copied link lands where it was', () => {
		for (const tab of ['tasks', 'now', 'history', 'log'] as ActivityTab[]) {
			expect(landingFor(...split(addressOf(tab))).tab).toBe(tab);
		}
		expect(addressOf('tasks')).toBe('/settings/tasks');
		expect(addressOf('now')).toBe('/settings/tasks?show=now');
		expect(addressOf('history')).toBe('/settings/tasks?show=history');
	});

	it('lands a setting drawn on the Log tab on the Log tab, whatever the tab in the address', () => {
		expect(landingFor('', '#download.verbose').tab).toBe('log');
		expect(landingFor('?show=history', '#logs.keep_mb').tab).toBe('log');
	});

	it('opens History narrowed to the act a folded list became', () => {
		expect(landingFor('', '#activity.saved')).toEqual({ tab: 'history', verb: 'saved' });
		expect(landingFor('', '#performance.scan_history')).toEqual({ tab: 'history', verb: 'ran' });
	});

	it('opens History on the decisions alone, where Organize sends its Decisions', () => {
		expect(landingFor('?show=history', '#activity.decisions')).toEqual({
			tab: 'history',
			decisions: true
		});
	});

	it('owns every setting the Log tab draws, so a link to one opens that tab', () => {
		/* The Log tab's rows are listed in `Logs.svelte`; a key listed there and not here would land
		   on Tasks, where the row is not, and the link would say the setting had moved. */
		const pane = readFileSync('src/lib/settings-ui/Logs.svelte', 'utf8');
		const listed = pane.slice(
			pane.indexOf('LOG_SETTINGS'),
			pane.indexOf('];', pane.indexOf('LOG_SETTINGS'))
		);
		const keys = [...listed.matchAll(/'([a-z_]+\.[a-z_]+)'/g)].map((one) => one[1]);
		expect(keys.length).toBeGreaterThan(2);
		for (const key of keys) expect(OWNED[key]?.tab, key).toBe('log');
	});

	it('leaves a key it does not own, and a tab it does not have, to Tasks', () => {
		expect(landingFor('', '#tasks.scan.when')).toEqual({ tab: 'tasks' });
		expect(Object.keys(OWNED)).not.toContain('tasks.scan.when');
		expect(landingFor('?show=elsewhere', '')).toEqual({ tab: 'tasks' });
		expect(landingFor('', '')).toEqual({ tab: 'tasks' });
		// A task's row named on the queue's old address still opens Tasks, where it is drawn.
		expect(landingFor('?show=now', '#tasks.duplicates.when')).toEqual({ tab: 'tasks' });
		// The known positive: a tab it has is honoured.
		expect(landingFor('?show=log', '')).toEqual({ tab: 'log' });
	});
});
