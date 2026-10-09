/* Privacy, and what is not on it: who can reach the library over the network is on General, and
 * the clean-up that deletes old searches runs in the background with no row to set. */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { COPY } from './Privacy.search';

function markup(): string {
	const source = readFileSync('src/lib/settings-ui/Privacy.svelte', 'utf8');
	return source.slice(source.indexOf('</script>')).replace(/<!--[\s\S]*?-->/g, '');
}

describe('the Privacy pane', () => {
	it('draws neither network sharing nor a clean-up row', () => {
		expect(markup()).not.toContain('<NetworkSharing');
		expect(markup()).not.toContain('<TaskWhen');
	});

	it('keeps its own blocks, search history among them with how long searches are kept', () => {
		const drawn = markup();
		for (const heading of [
			'"Sign-in"',
			'"Hidden"',
			'"Auto-lock"',
			'"Sift lock"',
			'"Save to device"'
		])
			expect(drawn, heading).toContain(`heading=${heading}`);
		expect(drawn).toContain('heading={COPY.locations.heading}');
		const history = drawn.slice(drawn.indexOf('id="privacy.search_history"'));
		expect(history.slice(0, history.indexOf('</SettingGroup>'))).toContain('SEARCH_RECORDS_KEY');
		expect(COPY.searchHistory.heading).toBe('Search history');
	});

	it('points at the saved copies from the group that decides who may save', () => {
		const drawn = markup();
		const group = drawn.slice(drawn.indexOf('heading="Save to device"'));
		const inside = group.slice(0, group.indexOf('</SettingGroup>'));
		expect(inside).toContain('id="privacy.saved"');
		// The door is the bordered press every door on a pane is, never a link-tone word.
		const door = inside.slice(inside.indexOf('<ActionRow'));
		expect(door.slice(0, door.indexOf('/>'))).toContain('id="privacy.saved"');
		expect(inside).toContain("showSettingsSection('tasks', 'activity.saved')");
		expect(inside).not.toContain('<SettingLink');
	});
});
