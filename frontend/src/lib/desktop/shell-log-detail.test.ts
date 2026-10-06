import { beforeEach, describe, expect, it, vi } from 'vitest';

/* The desktop shell's log is told the Detail setting at start-up, not only once the Log pane has
   been opened in a run. */

const shellLogDetail = vi.fn(async (detailed: boolean, _hidePersonal: boolean) => detailed);
let values = new Map<string, unknown>();
let saved: ((batch: Record<string, unknown>) => void) | undefined;

vi.mock('$lib/bridge', () => ({ bridge: { shellLogDetail } }));
vi.mock('$lib/settings-ui/settings', () => ({
	fetchSettingValues: vi.fn(async () => new Map(values)),
	onSettingsSaved: (watch: (batch: Record<string, unknown>) => void) => {
		saved = watch;
	}
}));

const { tellShellLogDetail, forgetToldShellLogDetail } = await import('./shell-log-detail');
import layout from '../../routes/+layout.svelte?raw';

beforeEach(() => {
	shellLogDetail.mockClear();
	forgetToldShellLogDetail();
	values = new Map();
});

describe('the shell told the log detail', () => {
	it('at start-up, from the library setting', async () => {
		values = new Map([['logs.detail', 'detailed']]);
		await tellShellLogDetail();
		expect(shellLogDetail).toHaveBeenCalledExactlyOnceWith(true, false);
	});

	it('says normal as not detailed', async () => {
		values = new Map([['logs.detail', 'normal']]);
		await tellShellLogDetail();
		expect(shellLogDetail).toHaveBeenCalledExactlyOnceWith(false, false);
	});

	it('says nothing when the setting cannot be read', async () => {
		await tellShellLogDetail();
		expect(shellLogDetail).not.toHaveBeenCalled();
	});

	it('again when the setting is saved, once per answer, and never for another setting', async () => {
		values = new Map([['logs.detail', 'normal']]);
		await tellShellLogDetail();
		saved?.({ 'logs.keep_mb': 50 });
		saved?.({ 'logs.detail': 'normal' });
		saved?.({ 'logs.detail': 'detailed' });
		expect(shellLogDetail.mock.calls).toEqual([
			[false, false],
			[true, false]
		]);
	});

	it('hands Hide personal details in the log beside it, read and saved', async () => {
		values = new Map<string, unknown>([
			['logs.detail', 'normal'],
			['logs.hide_personal', true]
		]);
		await tellShellLogDetail();
		saved?.({ 'logs.hide_personal': false });
		saved?.({ 'logs.detail': 'normal' });
		expect(shellLogDetail.mock.calls).toEqual([
			[false, true],
			[false, false]
		]);
	});

	it('is asked by the root layout once an admin is signed in and unlocked', () => {
		expect(layout).toMatch(
			/if \(!session\.adminUnlocked \|\| account === undefined(?: \|\| !screenAsked)?\) return;\s*void tellShellLogDetail\(\);/
		);
	});
});
