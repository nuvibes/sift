/*
 * What the compression sheet promises before anything is encoded.
 *
 * Three claims are load-bearing and none of them can be read off the source:
 *
 * 1. It asks the server, and asks again when the target changes. A panel answering from a first
 *    reply would describe a target nobody chose.
 * 2. A file that cannot meet the target is named, with its own reason, and the button does not
 *    offer to compress it. A single banner over forty files says nothing anybody can act on.
 * 3. "Go ahead anyway" starts unticked every time it opens, so forcing one file never forces forty.
 *
 * Rendered rather than read: matching an expression out of the component is a second copy of the
 * component, not a check.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import { reactiveProps } from '$lib/design/testing.svelte';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import CompressDialog from './CompressDialog.svelte';
import source from './CompressDialog.svelte?raw';
import pressable from '$lib/components/common/Pressable.svelte?raw';
import KindBars from '$lib/components/insights/KindBars.svelte';
import barChart from '$lib/components/charts/BarChart.svelte?raw';

const answers = vi.hoisted(() => ({
	preflight: vi.fn(),
	start: vi.fn(),
	sample: vi.fn(),
	waitForSample: vi.fn()
}));

vi.mock('$lib/library/compress.svelte', async (importOriginal) => ({
	...(await importOriginal<Record<string, unknown>>()),
	preflight: answers.preflight,
	start: answers.start,
	sample: answers.sample,
	waitForSample: answers.waitForSample
}));

/* The chooser is a listbox that positions itself against its trigger, which needs a layout jsdom
   does not have: the trigger clicks and no options ever appear. Replaced by the smallest thing
   with the same effect on this component: something that hands a value back the way choosing one
   does. What it looks like is tested where that component lives. */
const picker = vi.hoisted(() => ({
	props: null as { value: string; onValueChange?: (value: string) => void } | null
}));

vi.mock('$lib/components/common', async (importOriginal) => ({
	...(await importOriginal<Record<string, unknown>>()),
	Select: (_anchor: unknown, props: { value: string }) => {
		picker.props = props as typeof picker.props;
	}
}));

function verdict(over: Partial<Record<string, unknown>> = {}) {
	return {
		asset_id: 'a1',
		filename: 'clip.mp4',
		output_filename: 'clip-10MB.mp4',
		reachable: true,
		predicted_bytes: 9 * 1024 * 1024,
		smallest_reachable_bytes: null,
		reason: null,
		copy_only: false,
		rewrap_only: false,
		converts_audio: false,
		skip_reason: null,
		...over
	};
}

function reply(over: Partial<Record<string, unknown>> = {}) {
	return {
		target_bytes: 10 * 1024 * 1024,
		files: [verdict()],
		eligible_count: 1,
		unreachable_count: 0,
		copy_only_count: 0,
		audio_conversion_count: 0,
		suggested_target_bytes: null,
		...over
	};
}

let host: HTMLElement;

beforeEach(() => {
	picker.props = null;
	answers.preflight.mockReset().mockResolvedValue(reply());
	answers.start.mockReset().mockResolvedValue({ job_ids: ['j1'], started: 1, skipped: 0 });
	answers.sample.mockReset().mockResolvedValue({ job_id: 'j-sample' });
	answers.waitForSample.mockReset().mockResolvedValue(true);
});

afterEach(() => {
	host?.remove();
	document.body.innerHTML = '';
});

async function render(assetIds = ['a1']) {
	host = document.createElement('div');
	document.body.append(host);
	const props = reactiveProps({ open: true, assetIds });
	mount(CompressDialog, { target: host, props });
	flushSync();
	// The sheet asks the moment it opens, and the answer lands a microtask later.
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
	return props;
}

function button(): HTMLButtonElement | undefined {
	// Case-insensitive because the label says what it will do, and when it will do nothing it says
	// that instead of offering to compress no files.
	//
	// `:not(.tick)` because the insist row is a button too (the whole row is the control, so the
	// box inside it is only a picture of the state) and it says "Compress them anyway", which
	// would match this before the confirm does.
	return [...document.querySelectorAll('button:not(.tick)')].find((one) =>
		/compress/i.test(one.textContent ?? '')
	) as HTMLButtonElement | undefined;
}

describe('the compression sheet', () => {
	it('asks the server what this would do, before offering to do it', async () => {
		await render();
		expect(answers.preflight).toHaveBeenCalled();
		expect(answers.start).not.toHaveBeenCalled();
	});

	it('asks again when the target changes', async () => {
		await render();
		const first = answers.preflight.mock.calls.length;

		picker.props!.onValueChange?.('small');
		flushSync();
		await Promise.resolve();
		flushSync();

		expect(answers.preflight.mock.calls.length).toBeGreaterThan(first);
		expect(answers.preflight.mock.calls.at(-1)?.[0]).toMatchObject({ preset: 'small' });
	});

	it('names each file that cannot be made that small, with its own reason', async () => {
		answers.preflight.mockResolvedValue(
			reply({
				files: [
					verdict({
						reachable: false,
						reason: 'This is 2h 14m of 3840x2160 video.',
						predicted_bytes: null
					})
				],
				eligible_count: 1,
				unreachable_count: 1,
				suggested_target_bytes: 700 * 1024 * 1024
			})
		);
		await render();

		expect(document.body.textContent).toContain('This is 2h 14m of 3840x2160 video.');
		// And it ends in an alternative rather than a dead end.
		expect(document.body.textContent).toContain('700 MB');
	});

	it('will not start on a file it has said cannot meet the target', async () => {
		answers.preflight.mockResolvedValue(
			reply({
				files: [verdict({ reachable: false, reason: 'Too long.' })],
				eligible_count: 1,
				unreachable_count: 1
			})
		);
		await render();

		expect(button()?.disabled).toBe(true);
	});

	it('starts once the person insists, and only then', async () => {
		answers.preflight.mockResolvedValue(
			reply({
				files: [verdict({ reachable: false, reason: 'Too long.' })],
				eligible_count: 1,
				unreachable_count: 1
			})
		);
		await render();

		/* The whole ROW is the control and the box inside it is a picture of the state, so what
		   is pressed and what is read are the row's: `aria-pressed`, on the `Pressable`. */
		const insist = [...document.querySelectorAll('.tick')].at(-1) as HTMLButtonElement | undefined;
		// Unticked when it opens. The person insisting is the whole point of it being here.
		expect(insist?.getAttribute('aria-pressed')).toBe('false');

		insist!.click();
		flushSync();

		expect(button()?.disabled).toBe(false);
		button()!.click();
		await Promise.resolve();
		expect(answers.start).toHaveBeenCalledWith(expect.objectContaining({ force: true }));
	});

	it('never asks the server to force anything unless it was asked to', async () => {
		await render();
		button()!.click();
		await Promise.resolve();
		expect(answers.start).toHaveBeenCalledWith(expect.objectContaining({ force: false }));
	});

	it('says which files it will leave alone, and why', async () => {
		answers.preflight.mockResolvedValue(
			reply({
				files: [
					verdict({
						skip_reason:
							'A photograph is resized rather than compressed: open it and use the editor.',
						reachable: false
					})
				],
				eligible_count: 0
			})
		);
		await render();

		expect(document.body.textContent).toContain('resized rather than compressed');
		expect(button()?.disabled).toBe(true);
	});

	it('says when a file already meets the target, rather than encoding it', async () => {
		answers.preflight.mockResolvedValue(
			reply({ files: [verdict({ copy_only: true })], copy_only_count: 1 })
		);
		await render();

		expect(document.body.textContent).toContain('already meet this');
		// And does not offer to compress it. The server skips a file that already fits, so a button
		// counting it promises work that the message afterwards says did not happen.
		expect(button()?.disabled).toBe(true);
		expect(button()?.textContent).toContain('Nothing to compress');
	});

	it('counts only the files it is actually going to encode', async () => {
		// Four selected: one already fits, one cannot be made that small, two will be encoded. The
		// button has to say two: eligible is four and neither of the first two is going to happen.
		answers.preflight.mockResolvedValue(
			reply({
				files: [
					verdict({ asset_id: 'a1', copy_only: true }),
					verdict({ asset_id: 'a2', reachable: false, reason: 'Too long.' }),
					verdict({ asset_id: 'a3' }),
					verdict({ asset_id: 'a4' })
				],
				eligible_count: 4,
				unreachable_count: 1,
				copy_only_count: 1
			})
		);
		await render(['a1', 'a2', 'a3', 'a4']);

		expect(button()?.textContent).toContain('Compress 2 files');

		// Insisting brings back the one that cannot be met, and not the one there is nothing to do to.
		const insist = [...document.querySelectorAll('.tick')].at(-1) as HTMLButtonElement | undefined;
		insist!.click();
		flushSync();

		expect(button()?.textContent).toContain('Compress 3 files');
	});

	it('warns when the sound will be converted, which otherwise never happens', async () => {
		answers.preflight.mockResolvedValue(
			reply({ files: [verdict({ converts_audio: true })], audio_conversion_count: 1 })
		);
		await render();

		expect(document.body.textContent).toContain("can't travel in that format");
	});

	it('promises in words that nothing already there is replaced', async () => {
		// The one thing about this verb somebody could be wrong about, and the reason there is no
		// overwrite. It is stated on the sheet rather than left to be inferred from its absence.
		await render();
		expect(document.body.textContent).toMatch(/beside the original/);
		expect(document.body.textContent).toMatch(/changed or replaced/);
	});
});

describe('the sample', () => {
	it('is offered for one file, and encodes a few seconds on request', async () => {
		answers.sample.mockResolvedValue({ job_id: 'j-sample' });
		answers.waitForSample.mockResolvedValue(true);
		await render(['a1']);

		const ask = [...document.querySelectorAll('button')].find((one) =>
			/few seconds/.test(one.textContent ?? '')
		) as HTMLButtonElement | undefined;
		expect(ask).toBeDefined();

		ask!.click();
		await Promise.resolve();
		await Promise.resolve();
		flushSync();

		expect(answers.sample).toHaveBeenCalled();
		expect(document.querySelector('video')?.getAttribute('src')).toContain('j-sample');
	});

	it('is not offered over a selection', async () => {
		// A sample of forty is a sample of one of them, presented as if it said something about the
		// other thirty-nine.
		answers.preflight.mockResolvedValue(
			reply({ files: [verdict(), verdict({ asset_id: 'a2' })], eligible_count: 2 })
		);
		await render(['a1', 'a2']);

		const ask = [...document.querySelectorAll('button')].find((one) =>
			/few seconds/.test(one.textContent ?? '')
		);
		expect(ask).toBeUndefined();
	});

	it('says so when the sample could not be made', async () => {
		answers.sample.mockResolvedValue({ job_id: 'j-sample' });
		answers.waitForSample.mockResolvedValue(false);
		await render(['a1']);

		const ask = [...document.querySelectorAll('button')].find((one) =>
			/few seconds/.test(one.textContent ?? '')
		) as HTMLButtonElement;
		ask.click();
		await Promise.resolve();
		await Promise.resolve();
		flushSync();

		expect(document.body.textContent).toContain("couldn't be made");
		expect(document.querySelector('video')).toBeNull();
	});
});

describe('the tick rows', () => {
	afterEach(removeStyles);

	it("lie in a row over Pressable's block, and leave a chart's axis label alone", async () => {
		await render();
		const tick = document.querySelector('.tick') as HTMLElement;
		applyStyles(pressable, tick);
		applyStyles(source, tick.closest('.panel'));

		/* A chart's label under its bar writes `.tick` too, and is centred as text: laid out as a
		   flex row, its words sit at the start instead. */
		const chart = document.createElement('div');
		document.body.append(chart);
		mount(KindBars, {
			target: chart,
			props: {
				chart: { unit: 'plays', bars: [{ label: 'Mon', parts: [{ kind: 'video', value: 2 }] }] },
				label: 'Plays'
			} as never
		});
		flushSync();
		const axis = chart.querySelector('.tick') as HTMLElement;
		// KindBars draws through BarChart, whose stylesheet is where `.tick` is laid out.
		applyStyles(barChart, axis);

		expect(getComputedStyle(tick).display).toBe('flex');
		expect(getComputedStyle(axis).display).not.toBe('flex');
		expect(getComputedStyle(axis).textAlign).toBe('center');
	});
});
