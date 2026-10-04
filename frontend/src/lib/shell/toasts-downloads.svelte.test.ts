/* What a finished download says, under each answer of `download.finished_message`, and the
   listener that says it on every screen. */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const settings = vi.hoisted(() => ({ value: 'off' as unknown }));
vi.mock('$lib/settings-ui/settings', () => ({
	fetchSettings: vi.fn(async () => [
		{
			name: 'Downloads',
			settings: [{ key: 'download.finished_message', value: settings.value }]
		}
	])
}));

import {
	FinishedDownloads,
	finishedToasts,
	type Finished,
	type FinishedToast
} from './toasts-downloads.svelte';
import { wordsOf } from '$lib/components/common/toast-pieces';

const one = (id: string, asset: string | null = `a-${id}`): Finished => ({
	id,
	asset_id: asset,
	filename: `clip-${id}.mp4`
});

describe('the shape rule', () => {
	it('says nothing when Off, whatever finished', () => {
		expect(finishedToasts('off', [one('1')])).toEqual([]);
		expect(finishedToasts('off', [one('1'), one('2')])).toEqual([]);
	});

	it('says each download by name, the name a link only where it landed in the library', () => {
		expect(finishedToasts('each', [one('1'), one('2', null)])).toEqual([
			{
				message: ['Downloaded ', { text: 'clip-1.mp4', kind: 'asset', id: 'a-1' }],
				assetId: 'a-1'
			},
			{ message: ['Downloaded ', 'clip-2.mp4'], assetId: null }
		]);
	});

	it('says many once, as a count, and one as that one', () => {
		expect(finishedToasts('together', [one('1'), one('2'), one('3'), one('4')])).toEqual([
			{ message: '4 downloads finished', assetId: null }
		]);
		expect(finishedToasts('together', [one('1')]).map((toast) => wordsOf(toast.message))).toEqual([
			'Downloaded clip-1.mp4'
		]);
	});
});

function page(done: Finished[], busy = 0) {
	return {
		downloads: done.map((row) => ({ ...row, status: 'done' })),
		total: done.length,
		summary: { running: busy, queued: 0, by_state: {} }
	};
}

describe('the listener', () => {
	let said: FinishedToast[];
	let now: ReturnType<typeof page>;
	let listener: FinishedDownloads;

	beforeEach(() => {
		said = [];
		now = page([one('old')]);
		listener = new FinishedDownloads(
			async () => now as never,
			(toast) => said.push(toast)
		);
	});

	it('reads nothing and says nothing while Off, the default', async () => {
		settings.value = 'off';
		await listener.load();
		now = page([one('new'), one('old')]);
		await listener.look();
		expect(said).toEqual([]);
	});

	it('remembers what had finished before it started, then says each new one', async () => {
		settings.value = 'each';
		await listener.load();
		now = page([one('b'), one('a'), one('old')]);
		await listener.look();
		expect(said.map((toast) => wordsOf(toast.message))).toEqual([
			'Downloaded clip-b.mp4',
			'Downloaded clip-a.mp4'
		]);
		await listener.look();
		expect(said).toHaveLength(2);
	});

	it('under Once for many, holds what finished until nothing is left downloading', async () => {
		settings.value = 'together';
		await listener.load();
		now = page([one('a'), one('old')], 1);
		await listener.look();
		now = page([one('b'), one('a'), one('old')], 1);
		await listener.look();
		expect(said).toEqual([]);
		now = page([one('c'), one('b'), one('a'), one('old')], 0);
		await listener.look();
		expect(said).toEqual([{ message: '3 downloads finished', assetId: null }]);
	});

	it('under Once for many, says a lone finish as that file', async () => {
		settings.value = 'together';
		await listener.load();
		now = page([one('a'), one('old')], 0);
		await listener.look();
		expect(said.map((toast) => wordsOf(toast.message))).toEqual(['Downloaded clip-a.mp4']);
	});
});
