/* One destructive treatment on every settings pane: a row with the name and its sentence on the
 * left and a quiet danger button on the right, which is `ActionRow` with `destructive`. */
import { words } from '$lib/design/testing.svelte';
import { readdirSync, readFileSync } from 'node:fs';
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, describe, expect, it } from 'vitest';

import ActionRow from './ActionRow.svelte';

const HERE = 'src/lib/settings-ui';

/** The markup of a pane, comments out, so a note about the rule is not read as a breach of it. */
function markupOf(file: string): string {
	const source = readFileSync(`${HERE}/${file}`, 'utf8');
	return source
		.slice(source.lastIndexOf('</script>'))
		.replace(/<!--[\s\S]*?-->/g, '')
		.replace(/<style[\s\S]*?<\/style>/g, '');
}

let drawn: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	document.body.innerHTML = '';
});

describe('the destructive act on a settings pane', () => {
	it('is drawn by the row, never by a danger button a pane writes itself', () => {
		const panes = readdirSync(HERE).filter(
			(file) => file.endsWith('.svelte') && file !== 'ActionRow.svelte'
		);
		expect(panes.length).toBeGreaterThan(20);
		const own = panes.filter((file) => /tone=(["'{])\s*'?danger/.test(markupOf(file)));
		expect(own).toEqual([]);
	});

	it('puts the quiet danger button in the control column, after the name and its sentence', () => {
		drawn = mount(ActionRow, {
			target: document.body,
			props: {
				label: 'Face data',
				help: 'Deletes every face.',
				action: 'Delete face data',
				destructive: true,
				onclick: () => {}
			}
		});
		flushSync();

		const row = document.body.querySelector('.row');
		const button = row?.querySelector('.control .press button');
		expect(row?.querySelector('.named .name')?.textContent).toBe('Face data');
		expect(words(button)).toBe('Delete face data');
		expect(button?.classList.contains('danger-quiet')).toBe(true);
		expect(row?.lastElementChild?.classList.contains('control')).toBe(true);
	});
});
