import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import GraphicsCard from './GraphicsCard.svelte';
import GraphicsCardRemove from './GraphicsCardRemove.svelte';
import { GraphicsCardState } from './graphics-card-state.svelte';
import { ApiError } from '$lib/api/client';

/* The panel has five states and the whole point of it is that they read differently.
 *
 * The one worth the most is the split between "installed" and "works". A runtime lists every device
 * it was compiled for whether or not the hardware behind it can be reached, so a panel that treated
 * the two as one question would tell somebody their card was in use while every job ran on the
 * processor. That is the exact silence this feature exists to remove, and it is what a screen would
 * quietly reintroduce.
 */

/* `vi.hoisted`, because a `vi.mock` factory is lifted to the top of the file and an ordinary const
   is not: without it the factory runs before these exist and every import in the module graph fails
   with "cannot access before initialization". */
const get = vi.hoisted(() => vi.fn());
const post = vi.hoisted(() => vi.fn());
const del = vi.hoisted(() => vi.fn());

vi.mock('$lib/api/client', async (importOriginal) => {
	const actual = await importOriginal<typeof import('$lib/api/client')>();
	return { ...actual, api: { ...actual.api, get, post, del } };
});

const watch = vi.hoisted(() => ({
	running: false,
	fraction: 0,
	note: null as string | null,
	outcome: null as string | null,
	resume: vi.fn(async () => {}),
	follow: vi.fn(),
	couldNotStart: vi.fn()
}));
const { resume, follow, couldNotStart } = watch;

/* Which RUN of the server is answering. The panel waits for a DIFFERENT one before it believes a
   restart happened: the server answers the ask before it goes, so waiting for a reply would decide
   it had come back before it had left. */
const bootId = vi.hoisted(() => vi.fn(async () => 'before' as string | null));

vi.mock('$lib/shell/health', () => ({ serverBootId: bootId }));

vi.mock('$lib/jobs/accelerator.svelte', () => ({
	ACCEL_INSTALL: 'accel_install',
	accelWatch: watch
}));

function machine(overrides: Record<string, unknown> = {}) {
	return {
		card: 'NVIDIA GeForce RTX 4080',
		installed: false,
		already_capable: false,
		supported: true,
		download_bytes: 1_335_666_167,
		version: '1.28.0-cp313-cu13',
		job_id: null,
		restart_needed: false,
		...overrides
	};
}

let host: HTMLElement;

beforeEach(() => {
	get.mockReset();
	post.mockReset();
	del.mockReset();
	follow.mockReset();
	couldNotStart.mockReset();
	watch.running = false;
	watch.fraction = 0;
	watch.note = null;
	watch.outcome = null;
	bootId.mockReset();
	bootId.mockResolvedValue('before');
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	host.remove();
});

async function draw(card = new GraphicsCardState()) {
	mount(GraphicsCard, { target: host, props: { card } });
	flushSync();
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
	return host.textContent ?? '';
}

describe('the graphics card panel', () => {
	it('offers nothing, and says why, on a machine with no card', async () => {
		get.mockResolvedValue(machine({ supported: false, card: null }));

		const said = await draw();

		expect(said).toMatch(/can't see a GPU/i);
		expect(host.querySelector('button')).toBeNull();
	});

	it('offers nothing when the machine already has a runtime that drives the card', async () => {
		/* The answer to "what if I already have these files". Somebody running from source may have
		   installed the graphics-card runtime themselves, at a version they chose. Downloading over
		   the top of that spends a gigabyte replacing something that works, and putting a folder in
		   front of theirs on the import path takes the version decision away from them. */
		get.mockResolvedValue(machine({ already_capable: true }));

		const said = await draw();

		expect(said).toMatch(/already has a runtime/i);
		expect(said).toMatch(/doesn't replace it/i);
		expect(host.querySelector('button')).toBeNull();
	});

	it('names the size BEFORE anybody agrees to the download', async () => {
		/* Over a gigabyte is not a thing to start on somebody's connection and mention afterwards. */
		get.mockResolvedValue(machine());

		const said = await draw();

		expect(said).toMatch(/1\.3 GB/);
		expect(said).toMatch(/NVIDIA GeForce RTX 4080/);
	});

	it('says the download is kept with the library rather than inside the application', async () => {
		/* Not decoration: it is the reason the feature does not switch itself off at the next
		   update, and somebody deciding whether to spend a gigabyte is entitled to know it. */
		get.mockResolvedValue(machine());

		// `\s+` rather than a space: the sentence wraps in the markup, so the rendered text carries a
		// newline and an indent in the middle of it.
		expect(await draw()).toMatch(/installing an update doesn't\s+delete it/i);
	});

	it('does not claim the card works merely because the runtime is installed', async () => {
		get.mockResolvedValue(machine({ installed: true }));

		const said = await draw();

		expect(said).toMatch(/Choose the GPU under Faces, Smart Search and Watermarks/i);
		expect(said).not.toMatch(/it is working/i);
		const buttons = [...host.querySelectorAll('button')].map((one) => one.textContent ?? '');
		expect(buttons.some((label) => /run the test/i.test(label))).toBe(true);
	});

	it('reports what the card said when the test fails, not only that it failed', async () => {
		/* A red mark with no sentence leaves somebody with no next step. */
		get.mockResolvedValue(machine({ installed: true }));
		post.mockResolvedValue({ works: false, problem: 'the driver is too old for this runtime' });
		await draw();

		const test = [...host.querySelectorAll('button')].find((one) =>
			/run the test/i.test(one.textContent ?? '')
		);
		test?.click();
		await Promise.resolve();
		await Promise.resolve();
		flushSync();

		expect(host.textContent).toMatch(/driver is too old/i);
	});

	it('says a restart is needed when it was installed too late in this session', async () => {
		/* The install is real and cannot take effect until Sift is opened again. Saying nothing
		   would leave a screen claiming the card is in use while the work goes to the processor.
		   How it is SAID depends on the shell (an offer to do it here, a sentence in a browser),
		   and both are covered at the foot of this file. */
		get.mockResolvedValue(machine({ installed: true, restart_needed: true }));

		expect(await draw()).toMatch(/Restart needed/i);
	});

	it('follows the job it just started, so the bar appears without a reload', async () => {
		get.mockResolvedValue(machine());
		post.mockResolvedValue(machine({ job_id: 'job-7' }));
		await draw();

		const download = [...host.querySelectorAll('button')].find((one) =>
			/download/i.test(one.textContent ?? '')
		);
		download?.click();
		await Promise.resolve();
		await Promise.resolve();

		expect(follow).toHaveBeenCalledWith('job-7');
	});

	it('joins a download already running when the screen is opened', async () => {
		/* A gigabyte outlives the settings sheet. Without this, closing and reopening shows the
		   button again and the only way to find out is to reload the page. */
		get.mockResolvedValue(machine());

		await draw();

		expect(resume).toHaveBeenCalled();
	});

	it('says what happened rather than drawing an empty panel when the read fails', async () => {
		/* Drawing "no card" on a failed read would be a confident wrong answer about the machine. */
		get.mockRejectedValue(new Error('offline'));

		// Nothing at all rather than a confident wrong answer about somebody's machine.
		expect(await draw()).toBe('');
	});
});

/* The restart, which the panel cannot do without.
 *
 * Turning the card on installs a second build of a library that is already loaded, and two builds
 * of one library cannot both be in a process. So the panel has to offer to start it again, and
 * the test beside it has to be refused until that has happened, because a test run in this session
 * answers "the card was refused" whatever the card can actually do. Read before the restart, that
 * sentence is indistinguishable from a card that does not work.
 */
describe('when a restart is needed', () => {
	function pressableNamed(text: string): HTMLButtonElement | undefined {
		return [...host.querySelectorAll('button')].find((one) =>
			(one.textContent ?? '').includes(text)
		);
	}

	/* THE ONE THAT DECIDES WHICH COMPUTER RESTARTS. The process that has to start again is the one
	   the runtime is loaded into, which is the SERVER, and this panel is read from browsers and
	   from client-mode copies on other machines. Restarting the application in front of the reader
	   would restart the wrong computer, say it worked, and leave the card as it was. */
	it('asks the server to restart, not whatever is being read', async () => {
		get.mockResolvedValue(machine({ installed: true, restart_needed: true }));
		post.mockResolvedValue({ restarting: true });

		await draw();
		pressableNamed('Restart Sift')?.click();
		flushSync();

		/* Waited for rather than counted in ticks: the press first asks which computer this window
		   is on, for the line History keeps of the restart. */
		await vi.waitFor(() => expect(post).toHaveBeenCalledWith('/performance/restart'));
	});

	/* THE HALF A RESTART NEEDS, or one that worked looks like one that did nothing: stopping at
	   the yes would leave the button saying "Restarting" for ever and the panel showing the
	   state of the process it had just stopped. */
	it('waits for a different run of the server, then reads the panel again', async () => {
		get.mockResolvedValue(machine({ installed: true, restart_needed: true }));
		post.mockResolvedValue({ restarting: true });
		await draw();
		get.mockClear();
		get.mockResolvedValue(machine({ installed: true, restart_needed: false }));
		// Still the old process for one look, then the new one.
		bootId.mockResolvedValueOnce('before');
		bootId.mockResolvedValueOnce('before');
		bootId.mockResolvedValue('after');

		pressableNamed('Restart Sift')?.click();
		await vi.waitFor(() => expect(get).toHaveBeenCalledWith('/performance/accelerator'), {
			timeout: 5000
		});
		flushSync();

		// And the panel is about the new process: the restart row is gone with it.
		expect(pressableNamed('Restart Sift')).toBeUndefined();
	});

	/* Pressing Download and pressing Restart is the whole of what anybody should have to do. The
	   test is the only thing between a restart and knowing whether it worked, it takes seconds, and
	   it is the question every person has at exactly this moment. */
	it('tests the card itself once the server is back', async () => {
		get.mockResolvedValue(machine({ installed: true, restart_needed: true }));
		post.mockResolvedValue({ restarting: true });
		await draw();
		get.mockResolvedValue(machine({ installed: true, restart_needed: false }));
		post.mockClear();
		post.mockResolvedValue({ works: true, problem: null });
		// The run it was BEFORE, then the run it is after. Set to the same value both times, the
		// panel correctly refuses to call it a restart, which is the guard working.
		bootId.mockResolvedValueOnce('before');
		bootId.mockResolvedValue('after');

		pressableNamed('Restart Sift')?.click();
		/* Waiting on the SCREEN rather than on the call: the call is made a tick before the answer
		   reaches the panel, so asserting on it and then reading the page catches the page half a
		   step behind and fails on a feature that works. */
		await vi.waitFor(
			() => {
				flushSync();
				expect(host.textContent).toContain('Working');
			},
			{ timeout: 5000 }
		);

		expect(post).toHaveBeenCalledWith('/performance/accelerator/test');
	});

	/* A backend nothing is supervising would stop and stay stopped, and the server refuses rather
	   than taking the library off the air to find out. The refusal has to reach the screen. */
	it('says why when the server will not restart itself', async () => {
		get.mockResolvedValue(machine({ installed: true, restart_needed: true }));
		post.mockRejectedValue(
			new ApiError(409, 'That did not work.', 'Nothing is watching this copy of Sift.')
		);

		await draw();
		pressableNamed('Restart Sift')?.click();
		flushSync();

		await vi.waitFor(() => {
			flushSync();
			expect(host.textContent).toContain('Nothing is watching this copy of Sift');
		});
		// And it is pressable again, rather than stuck saying "Restarting" for ever.
		expect(pressableNamed('Restart Sift')).toBeDefined();
	});

	/* THE OTHER ONE THAT MATTERS. A test pressed before the restart gives a wrong answer
	   confidently. */
	it('will not let the card be tested until it has happened', async () => {
		get.mockResolvedValue(machine({ installed: true, restart_needed: true }));

		await draw();

		expect(pressableNamed('Run the test')?.disabled).toBe(true);
		expect(host.textContent).toContain('Restart first');
	});

	it('lets the card be tested once it is not needed', async () => {
		get.mockResolvedValue(machine({ installed: true, restart_needed: false }));

		await draw();

		expect(pressableNamed('Run the test')?.disabled).toBe(false);
	});
});

describe('deleting GPU support', () => {
	it('is not in the block that describes the card, but in its own last group', async () => {
		/* The way out of a pane is its last group. Drawn here it would sit mid-pane, between the
		   card's test and the benchmark. */
		get.mockResolvedValue(machine({ installed: true }));
		const card = new GraphicsCardState();
		await draw(card);
		expect(host.textContent).not.toContain('Delete GPU support');

		const foot = document.createElement('div');
		document.body.append(foot);
		mount(GraphicsCardRemove, { target: foot, props: { card } });
		flushSync();
		const group = foot.querySelector('section.group');
		expect(group?.querySelector('.section-heading')?.textContent).toContain('GPU support');
		expect(group?.querySelector('.row .name')?.textContent).toBe('Delete GPU support');
		expect(group?.querySelector('button')?.classList.contains('danger-quiet')).toBe(true);
		foot.remove();
	});

	it('draws nothing where there is nothing to delete', async () => {
		get.mockResolvedValue(machine({ installed: false }));
		const card = new GraphicsCardState();
		await draw(card);
		const foot = document.createElement('div');
		document.body.append(foot);
		mount(GraphicsCardRemove, { target: foot, props: { card } });
		flushSync();
		expect(foot.textContent?.trim()).toBe('');
		foot.remove();
	});

	it("answers a link to it while there is nothing to delete by ringing the card's block", async () => {
		get.mockResolvedValue(machine({ installed: false }));
		const card = new GraphicsCardState();
		host.className = 'section-body';
		await draw(card);
		mount(GraphicsCardRemove, { target: host, props: { card } });
		flushSync();
		const { toasts } = await import('$lib/shell/toasts.svelte');
		const { revealSetting } = await import('./settings-anchor.svelte');
		const said = vi.spyOn(toasts, 'show');
		const scrolled = Element.prototype.scrollIntoView;
		Element.prototype.scrollIntoView = vi.fn();
		try {
			await expect(revealSetting('performance.gpu-remove')).resolves.toBe(true);
			expect(said).toHaveBeenCalledWith(
				"GPU support isn't downloaded, so there's nothing to delete."
			);
		} finally {
			Element.prototype.scrollIntoView = scrolled;
			said.mockRestore();
		}
	});
});
