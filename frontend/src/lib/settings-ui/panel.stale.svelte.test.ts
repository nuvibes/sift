/* A switch pressed keeps its new state: a re-read asked before the write landed answers the old one. */
import { describe, expect, it, vi } from 'vitest';

const served = vi.hoisted(() => ({
	value: false,
	reads: 0,
	/* Holds the next read's answer until the test lets it go, as a slow re-read would be. */
	gate: null as Promise<void> | null
}));

vi.mock('$lib/settings-ui/settings', () => ({
	fetchSettings: vi.fn(async () => {
		/* Another setting moves on every read, so no answer is the same as the one drawn. */
		served.reads += 1;
		const answer = [
			{
				name: 'Playback',
				settings: [
					{ key: 'player.resume', value: served.value },
					{ key: 'player.volume', value: served.reads }
				]
			}
		];
		if (served.gate) await served.gate;
		return answer;
	}),
	saveSettings: vi.fn(async (values: Record<string, unknown>) => {
		served.value = values['player.resume'] as boolean;
	})
}));
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: vi.fn() } }));

const { SettingsPanel } = await import('./panel.svelte');

async function pane() {
	let panel!: InstanceType<typeof SettingsPanel>;
	const stop = $effect.root(() => {
		panel = new SettingsPanel();
	});
	await panel.load();
	return { panel, stop };
}

describe('a re-read that left before a write', () => {
	it('does not put the switch back', async () => {
		served.value = false;
		const { panel, stop } = await pane();
		let open = () => {};
		served.gate = new Promise((resolve) => (open = resolve));
		const stale = panel.load();
		served.gate = null;
		await panel.save('player.resume', true);
		open();
		await stale;
		expect(panel.value('player.resume')).toBe(true);

		/* A read asked after the write lands takes what the server says. */
		served.value = false;
		await panel.load();
		expect(panel.value('player.resume')).toBe(false);
		stop();
	});
});
