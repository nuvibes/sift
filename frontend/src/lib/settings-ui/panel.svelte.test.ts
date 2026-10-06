/* A settings pane re-reading in place: a setting moved elsewhere never puts the pane back to its
 * loading state, which would take every row off the screen and draw it again. */
import { flushSync } from 'svelte';
import { describe, expect, it, vi } from 'vitest';

const served = vi.hoisted(() => ({ sections: [] as unknown[], fail: false }));

vi.mock('$lib/settings-ui/settings', () => ({
	fetchSettings: vi.fn(async () => {
		if (served.fail) throw new Error('away');
		return structuredClone(served.sections);
	}),
	saveSettings: vi.fn(async () => undefined)
}));
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: vi.fn() } }));

const { SettingsPanel } = await import('./panel.svelte');
const { settingChanges } = await import('$lib/library/changes.svelte');

function section(value: boolean) {
	return [{ name: 'Playback', settings: [{ key: 'theater.autoplay', value }] }];
}

/** A pane's panel, built in a setup context the way a pane builds it, and its first read. */
async function pane() {
	let panel!: InstanceType<typeof SettingsPanel>;
	const stop = $effect.root(() => {
		panel = new SettingsPanel();
	});
	await panel.load();
	return { panel, stop };
}

describe('a settings pane told a setting moved', () => {
	it('takes the new value without passing through its loading state', async () => {
		served.sections = section(false);
		const { panel, stop } = await pane();
		expect(panel.loading).toBe(false);

		served.sections = section(true);
		const states: boolean[] = [];
		const watch = $effect.root(() => {
			$effect(() => {
				states.push(panel.loading);
			});
		});
		settingChanges.changed();
		flushSync();
		await vi.waitFor(() => expect(panel.value('theater.autoplay')).toBe(true));

		expect(states, 'the pane went back to loading').not.toContain(true);
		watch();
		stop();
	});

	it('keeps the rows it drew when a re-read fails', async () => {
		served.sections = section(false);
		served.fail = false;
		const { panel, stop } = await pane();
		const drawn = panel.sections;

		served.fail = true;
		await panel.load();

		expect(panel.failed).toBe(false);
		expect(panel.sections).toBe(drawn);
		served.fail = false;
		stop();
	});

	it('writes nothing when the answer is the same, so no row is drawn twice', async () => {
		served.sections = section(false);
		const { panel, stop } = await pane();
		const drawn = panel.sections;

		await panel.load();

		expect(panel.sections).toBe(drawn);
		stop();
	});
});
