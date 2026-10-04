/* Near-duplicate review, as the workbench draws it.
 *
 * Three things here are decided in the markup and nowhere else, and every one of them is a sentence
 * somebody acts on.
 *
 * The first is what an empty list MEANS. There are several completely different reasons the queue
 * can be empty (the library is clean, the closeness dial is hiding groups, half the library has
 * never been fingerprinted) and all of them draw the same blank screen. Left unsaid, the
 * reassuring reading is the one people take, and it is the wrong one.
 *
 * The second is the sentence in front of the page confirm. It names how many groups it covers, how
 * many files it would permanently delete and what that frees, and it is the only thing on this
 * screen that cannot be taken back.
 *
 * The third is the mark. The whole inversion this screen exists for is that somebody SKIMS what a
 * rule chose rather than choosing from scratch, so which file is lit, and that an override moves
 * it, is the behaviour, not decoration.
 *
 * The store underneath is replaced by a plain object: what it does with the network is tested where
 * it lives, and what is left to prove here is what the screen says about what it holds.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { BLANK_PICTURE } from '$lib/people/faces.svelte';
import type { Group, GroupFile, Outcome } from '$lib/settings-ui/maintenance-state.svelte';

/* The store, stood in for, and the seam that lets a test arrange one.
 *
 * Hoisted because `vi.mock`'s factory is lifted above everything else in the file, so anything the
 * factory closes over has to exist before the module graph is built. `arrange` is the hook a test
 * fills in; `built` is what the panel actually constructed, so an assertion can reach the same
 * object the markup is reading.
 */
const shared = vi.hoisted(() => {
	const nothing: Outcome = { settled: 0, removed: 0, refused: 0, unknown: 0 };
	const confirmMarked = vi.fn(async (_groups: Group[]) => nothing);
	const dismissGroups = vi.fn(async (_groups: Group[]) => nothing);
	/* Reads nothing, so no page lands: false, which is what the panel reads as "nothing to write
	   into the address". */
	const fillGroups = vi.fn(async (_paging: unknown, _needsYou: boolean) => false);
	const loadCarry = vi.fn(async () => {});
	const carryEverywhere = vi.fn(async () => ({ files: 0 }));
	const scanNow = vi.fn(async () => undefined);
	const openAsset = vi.fn((_id: string, _among: unknown[]) => {});

	class FakeMaintenance {
		groups: Group[] = [];
		chosen: Record<string, string> = {};
		summary = {
			total: 0,
			offset: 0,
			needsYou: 0,
			matching: 0,
			pendingTotal: 0,
			concealed: 0,
			awaitingFingerprint: 0,
			cannotFingerprint: 0,
			level: 'medium',
			maxDurationGapMs: null as number | null,
			rule: 'higher_res' as const,
			rules: [
				{ key: 'higher_res', label: 'Higher resolution' },
				{ key: 'larger', label: 'Larger file' }
			],
			levels: [
				{ key: 'exact', label: 'Exact - identical' },
				{ key: 'medium', label: 'Medium - re-encoded' }
			],
			maxDurationGapLimit: 3600,
			maxDurationGapWord: 'No limit' as string | null
		};
		loaded = true;
		loading = false;
		problem: string | null = null;
		busy = false;
		/* Nothing to carry unless a test says otherwise, which is what an ordinary library looks
		   like: the offer only exists where two bit-for-bit copies disagree about what is known
		   of them. */
		carry = { groups: 0, files: 0 };

		constructor() {
			shared.arrange(this);
			shared.built.push(this);
		}
		/* The real ones, because they are what the tick on screen and the press agree through:
		   a stub for them would make the mark a fact about the test rather than about the screen. */
		keeperOf(group: Group): string | null {
			const key = group.files[0]?.id ?? '';
			const mine = this.chosen[key];
			if (mine && group.files.some((one) => one.id === mine)) return mine;
			return group.keeper ?? null;
		}
		choose(group: Group, fileId: string): void {
			this.chosen = { ...this.chosen, [group.files[0]?.id ?? '']: fileId };
		}
		get marked(): Group[] {
			return this.groups.filter((one) => !one.too_big && this.keeperOf(one) !== null);
		}
		get wouldDelete(): GroupFile[] {
			return this.marked.flatMap((group) => {
				const keep = this.keeperOf(group);
				return group.files.filter((one) => one.id !== keep);
			});
		}
		fillGroups = fillGroups;
		loadCarry = loadCarry;
		carryEverywhere = carryEverywhere;
		confirmMarked = confirmMarked;
		dismissGroups = dismissGroups;
		scanNow = scanNow;
	}

	return {
		carryEverywhere,
		confirmMarked,
		dismissGroups,
		loadCarry,
		fillGroups,
		openAsset,
		scanNow,
		FakeMaintenance,
		built: [] as FakeMaintenance[],
		arrange: (_one: FakeMaintenance) => {}
	};
});

const { confirmMarked, dismissGroups, openAsset, scanNow } = shared;
type FakeMaintenance = InstanceType<typeof shared.FakeMaintenance>;

vi.mock('$lib/settings-ui/maintenance-state.svelte', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/settings-ui/maintenance-state.svelte')>();
	// The formatters are the real ones. They are what turns a byte count into the words on the
	// card, and a stub for them would be a test of the stub.
	return { ...real, Maintenance: shared.FakeMaintenance };
});

vi.mock('$lib/organize/organize.svelte', () => ({ answered: { changed: vi.fn() } }));
/* The comparison's last run is read from its row on Tasks; a test fills it where it asks. */
const compared = vi.hoisted(() => ({
	last: null as null | { ended_at: number; outcome: string; said: string | null }
}));
vi.mock('$lib/jobs/tasks.svelte', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/jobs/tasks.svelte')>()),
	taskList: {
		ensure: vi.fn(async () => {}),
		row: (id: string) =>
			id === 'duplicates' && compared.last ? { id, last: compared.last } : undefined
	}
}));
vi.mock('$lib/player/asset-view', () => ({ openAsset: shared.openAsset }));
vi.mock('$lib/settings-ui/settings-view', () => ({
	openSettings: vi.fn(),
	openSettingsInstead: () => () => {}
}));
/* Built ON the real module rather than instead of it, so an export nobody here thought about is
   still the real one. `$lib/library/rating` calls `onSettingsSaved` at import time, three components deep
   from this one, and a stand-in short of one export fails the whole suite with an error about
   rating scales. */
vi.mock('$lib/settings-ui/settings', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/settings-ui/settings')>()),
	saveSettings: vi.fn()
}));

import DuplicatesPanel from './DuplicatesPanel.svelte';

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;
let store: FakeMaintenance;

function file(id: string, over: Partial<GroupFile> = {}): GroupFile {
	return {
		id,
		media_type: 'video',
		concealed: false,
		original_filename: `${id}.mp4`,
		where: `Videos/${id}.mp4`,
		size_bytes: 1000,
		width: 1920,
		height: 1080,
		duration_ms: 1000,
		container: 'mp4',
		added_at: 100,
		art: null,
		...over
	};
}

function group(over: Partial<Group> = {}): Group {
	return {
		files: [file('asset-a'), file('asset-b')],
		method: 'phash',
		distance: 1,
		keeper: 'asset-a',
		too_big: false,
		...over
	};
}

function draw(arrange: (one: FakeMaintenance) => void): HTMLElement {
	shared.arrange = arrange as (one: unknown) => void;
	shared.built.length = 0;
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(DuplicatesPanel, { target: host }) as Record<string, unknown>;
	flushSync();
	store = shared.built[0];
	return host;
}

/** Take the panel down between two arrangements in one test.
 *
 * An open confirm is dismissed FIRST, and that is not tidiness. A dialog is portalled to the end of
 * the document, so it outlives `host.remove()`, and tearing the panel down under it leaves the
 * dialog reading a `$derived` whose owner has gone, which Svelte reports as `derived_inert` and
 * which would put a stale sentence in front of somebody for a frame. Pressing Cancel is what a
 * person does, and it is the only thing here that closes it.
 *
 * It does not silence it entirely: a sheet closes on a TRANSITION, so it is still mounted when
 * the flush returns, and the warning survives for the one test that leaves a destructive confirm
 * open. Pressing Escape as well raises more of them rather than fewer, so Cancel alone is
 * pressed.
 */
function undraw(): void {
	for (const one of document.body.querySelectorAll('button')) {
		if (one.textContent?.trim() === 'Cancel') one.click();
	}
	flushSync();
	if (mounted) unmount(mounted);
	mounted = null;
	host?.remove();
}

/** Press a button by what it says. */
function press(shown: HTMLElement, words: string): void {
	const button = [...shown.querySelectorAll('button')].find((one) =>
		one.textContent?.includes(words)
	);
	button?.click();
	flushSync();
}

beforeEach(() => {
	confirmMarked.mockClear();
	dismissGroups.mockClear();
	openAsset.mockClear();
	scanNow.mockClear();
});

afterEach(() => {
	undraw();
	shared.arrange = () => {};
});

describe('what an empty list means', () => {
	it('says how many videos have not been fingerprinted, so the queue is not read as clean', () => {
		const shown = draw((one) => {
			one.summary.awaitingFingerprint = 4;
		});

		expect(shown.textContent).toContain('4');
		expect(shown.textContent).toContain('not been fingerprinted');
	});

	it('writes a count under the bar the way every screen writes a number', () => {
		const shown = draw((one) => {
			one.summary.awaitingFingerprint = 2036;
			one.summary.cannotFingerprint = 1250;
		});

		expect(shown.textContent).toContain('2,036');
		expect(shown.textContent).toContain('1,250');
		expect(shown.textContent).not.toContain('2036');
	});

	it('reads one waiting video as singular rather than as "1 videos have"', () => {
		const shown = draw((one) => {
			one.summary.awaitingFingerprint = 1;
		});

		expect(shown.textContent).toContain('video has');
		expect(shown.textContent).not.toContain('videos have');
	});

	/* Work that will NOT happen, said apart from work in flight. Folded into the count above, an
	   empty queue with fifty unreadable files in the library reads as a clean library. */
	it('says how many files can never be compared at all', () => {
		const shown = draw((one) => {
			one.summary.cannotFingerprint = 5;
		});

		expect(shown.textContent).toContain('5');
		expect(shown.textContent).toContain("files can't");
		expect(shown.textContent).toContain('no fingerprint to match against');
	});

	it('reads one such file as singular rather than as "1 files cannot"', () => {
		const shown = draw((one) => {
			one.summary.cannotFingerprint = 1;
		});

		expect(shown.textContent).toContain("file can't");
		expect(shown.textContent).not.toContain("files can't");
	});

	it('says nothing about uncomparable files when every file can be compared', () => {
		// The known negative. A control explaining something that did not happen is noise.
		const shown = draw(() => {});

		expect(shown.textContent).not.toContain("can't be compared");
	});

	it('says how many pairs the closeness setting is holding back', () => {
		const shown = draw((one) => {
			one.summary.pendingTotal = 9;
			one.summary.matching = 2;
		});

		expect(shown.textContent).toContain('7');
		expect(shown.textContent).toContain("closeness setting doesn't show");
	});

	it('says nothing about hidden pairs when the setting hides none', () => {
		/* `pendingTotal - matching` can only be read as a count of what is held back when it is
		   positive. A negative one drawn raw would say "-2 more pairs are waiting". */
		const shown = draw((one) => {
			one.summary.pendingTotal = 2;
			one.summary.matching = 5;
		});

		expect(shown.textContent).not.toContain("closeness setting doesn't show");
	});

	it('says how many groups are behind a vault this session has not opened', () => {
		const shown = draw((one) => {
			one.summary.concealed = 3;
		});

		expect(shown.textContent).toContain("vault this session hasn't opened");
	});

	it('tells a clean library apart from a filtered one', () => {
		const clean = draw((one) => {
			one.summary.pendingTotal = 0;
		});
		expect(clean.textContent).toContain("hasn't found any duplicates it's unsure about");

		undraw();

		const filtered = draw((one) => {
			one.summary.pendingTotal = 6;
		});
		expect(filtered.textContent).toContain('Nothing matches your current settings');
	});

	it('does not say the library is clean when the read failed', () => {
		/* A request that failed says nothing about the library, and "nothing to review" is the
		   reassuring reading of silence. */
		const shown = draw((one) => {
			one.loaded = false;
			one.loading = false;
		});

		expect(shown.textContent).toContain("Duplicates couldn't be loaded");
		expect(shown.textContent).not.toContain("hasn't found any duplicates");
	});
});

describe('the mark, which is the whole inversion', () => {
	it('carries all three dials, and each writes its own setting', async () => {
		/* A dial and the pile it governs are read together, so they are here, beside the pile, and
		   each one still writes through the ordinary settings endpoint, which is what makes a change
		   made in another window reach this one. */
		const shown = draw((one) => {
			one.groups = [group()];
		});

		const labels = [...shown.querySelectorAll('.bar span')].map((one) => one.textContent?.trim());
		expect(labels).toContain('Keep');
		expect(labels).toContain('Alike');
		expect(labels).toContain('Length within');
	});

	it('says which file the rule would keep, in words and not only in colour', () => {
		const shown = draw((one) => {
			one.groups = [group()];
			one.summary.total = 1;
		});

		expect(shown.textContent).toContain('Keeping this one');
		// And the other file offers to take its place rather than offering to be deleted.
		expect(shown.textContent).toContain('Keep this instead');
	});

	it('moves the mark when somebody overrides it, and writes nothing', () => {
		const shown = draw((one) => {
			one.groups = [group()];
		});

		press(shown, 'Keep this instead');

		expect(store.keeperOf(store.groups[0])).toBe('asset-b');
		expect(confirmMarked).not.toHaveBeenCalled();
	});

	it('draws both of two groups that share their smallest file', () => {
		/* A GIF is fingerprinted twice, so the same two files are a group under `videohash` and a
		   group under `video_phash`. Keyed by the file alone, Svelte throws on the duplicate key and
		   the whole list draws NOTHING, with the bar above it still reporting the real counts, which
		   makes it look like a loading screen. */
		const shown = draw((one) => {
			one.groups = [
				group({ method: 'videohash', distance: 0 }),
				group({ method: 'video_phash', distance: 0 })
			];
		});

		expect(shown.querySelectorAll('.groups > li')).toHaveLength(2);
		/* Both say "Identical", which is the likeness vocabulary doing its job: the two are equally
		   alike and the METHOD that measured them is not something a reader has to decode. What is
		   under test here is that both cards are DRAWN, which the count above is. */
		expect(shown.querySelectorAll('.closeness')).toHaveLength(2);
	});

	it('says what a group would cost: how many go, and what that frees', () => {
		const shown = draw((one) => {
			one.groups = [
				group({
					files: [file('a', { size_bytes: 4000 }), file('b', { size_bytes: 1500 })],
					keeper: 'a'
				})
			];
		});

		expect(shown.textContent).toContain('2 files, 1 would be deleted, frees 1.5 kB');
	});

	it('says plainly when no rule could choose, rather than leaving the group looking unfinished', () => {
		const shown = draw((one) => {
			one.groups = [group({ keeper: null })];
		});

		expect(shown.textContent).toContain('no rule could choose between them');
		expect(shown.textContent).not.toContain('Keeping this one');
	});

	it('offers nothing at all on a chain, and says the answer is a tighter dial', () => {
		/* A component past the cap is a chain of pairs whose two ends may look nothing alike, so
		   there is no keeper anybody could vouch for, and the useful action is the setting. */
		const shown = draw((one) => {
			one.groups = [
				group({ files: [file('a'), file('b'), file('c')], keeper: null, too_big: true })
			];
		});

		expect(shown.textContent).toContain('joined in a chain');
		// The dial is named as it is drawn above, on this panel. Settings does not draw it, so a
		// link there would land on a pane without it.
		expect(shown.textContent).toContain('tighten Alike above');
		expect(shown.querySelector('a[href*="dedup.level"]')).toBeNull();
		// And a way to look at all of it on a screen of its own, which is what a chain is FOR.
		expect(shown.querySelector('a[href^="/organize/duplicates/"]')).not.toBeNull();
		expect(shown.textContent).not.toContain('Keep this instead');
		/* Nor "they are different", which the SERVER refuses on a chain, so offering it would be a
		   button that did nothing: the confirm opens, is answered, and the group stays exactly
		   where it was. */
		expect(shown.textContent).not.toContain('Keep all');
	});

	it('draws a locked file as locked and says nothing else about it', () => {
		const shown = draw((one) => {
			one.groups = [
				group({
					files: [file('a'), file('b', { concealed: true, original_filename: null, where: null })],
					keeper: null
				})
			];
		});

		expect(shown.textContent).toContain('Unlock the vault to see this file');
		expect(shown.textContent).not.toContain('b.mp4');
	});

	it('draws a locked file as the hidden mark, and asks the server for no picture of it', () => {
		/*
		 * The hidden copy's tile must not ask for a still the server refuses, which would draw the
		 * browser's torn-page glyph on every visit.
		 */
		const shown = draw((one) => {
			one.groups = [
				group({
					files: [file('a'), file('b', { concealed: true, original_filename: null, where: null })],
					keeper: null
				})
			];
		});

		const sources = [...shown.querySelectorAll('img')].map((one) => one.getAttribute('src') ?? '');
		expect(sources.some((src) => src.includes('/api/assets/b/'))).toBe(false);
		expect(sources.filter((src) => src.startsWith('data:image/svg+xml,'))).toHaveLength(1);
		expect(sources.some((src) => src.includes('/api/assets/a/thumb'))).toBe(true);
	});

	it('opens the PLAYER on a thumbnail, with the group as what it can step through', () => {
		/* An overlay of its own, positioned against the panel, is fine at two thumbnails tall and
		   wrong at twenty-four groups, where `inset: 0` centres the picture thousands of pixels down
		   and a press appears to do nothing; and it would show a still of what is usually a video.
		   Handing the group in is what makes the shared opener right here rather than merely shared:
		   Next steps between the files being compared, which is the question this screen asks. */
		const shown = draw((one) => {
			one.groups = [group({ files: [file('a'), file('b', { media_type: 'image' })] })];
		});

		const preview = shown.querySelector<HTMLElement>('button[aria-label^="Open "]');
		preview?.click();
		flushSync();

		expect(openAsset).toHaveBeenCalledWith('a', [
			{ id: 'a', runs: true },
			{ id: 'b', runs: false }
		]);
	});

	it('draws the path of each file, because the pictures are the same picture', () => {
		const shown = draw((one) => {
			one.groups = [
				group({
					files: [
						file('a', { where: 'Videos/2024/clip.mp4' }),
						file('b', { where: 'Backup/clip.mp4' })
					]
				})
			];
		});

		expect(shown.textContent).toContain('Videos/2024/clip.mp4');
		expect(shown.textContent).toContain('Backup/clip.mp4');
	});
});

describe('the sentence in front of the page confirm', () => {
	it('names the groups, the files that go, and what that frees', () => {
		const shown = draw((one) => {
			one.groups = [
				group({
					files: [file('a', { size_bytes: 5000 }), file('b', { size_bytes: 2000 })],
					keeper: 'a'
				}),
				group({
					files: [file('c', { size_bytes: 5000 }), file('d', { size_bytes: 3000 })],
					keeper: 'c'
				}),
				// Neither of these is in the press: one nothing chose in, one a chain.
				group({ files: [file('e'), file('f')], keeper: null }),
				group({ files: [file('g'), file('h')], keeper: null, too_big: true })
			];
		});

		press(shown, 'Confirm this page');

		const said = document.body.textContent ?? '';
		expect(said).toContain('Across 2 groups');
		expect(said).toContain('the other 2 from your disk');
		expect(said).toContain('freeing 5.0 kB');
		expect(said).toContain("can't be undone");
	});

	it('counts only the groups it would act on, in the question the button asks', () => {
		/* A count is the sentence's, not the button's: the button is a verb. */
		const shown = draw((one) => {
			one.groups = [group(), group({ files: [file('c'), file('d')], keeper: null })];
		});

		expect(shown.textContent).not.toContain('Confirm this page (');
		press(shown, 'Confirm this page');
		expect(document.body.textContent).toContain('Across 1 group');
	});

	it('deletes nothing merely by being asked about', () => {
		/* The confirm is a question. Nothing may go until it is answered, which is the whole reason
		   there is one. */
		const shown = draw((one) => {
			one.groups = [group()];
		});

		press(shown, 'Confirm this page');

		expect(confirmMarked).not.toHaveBeenCalled();
	});

	it('cannot be pressed when the rule chose in nothing on the page', () => {
		const shown = draw((one) => {
			one.groups = [group({ keeper: null })];
		});

		const button = [...shown.querySelectorAll('button')].find((one) =>
			one.textContent?.includes('Confirm this page')
		);

		expect(button?.disabled).toBe(true);
	});
});

describe('the ones that need a person', () => {
	it('offers to show only those, and says how many there are in the sentence', () => {
		const shown = draw((one) => {
			one.groups = [group()];
			one.summary.needsYou = 7;
		});

		expect(shown.textContent).toContain('Show the ones that need you');
		expect(shown.querySelector('.standing')?.textContent).toContain('7 need your input.');
	});

	it('draws its door in the same tone as the two buttons beside it', () => {
		const shown = draw((one) => {
			one.groups = [group()];
			one.summary.needsYou = 7;
		});

		const tones = [...shown.querySelectorAll<HTMLElement>('.presses .btn')].map((one) =>
			['primary', 'secondary', 'ghost'].find((tone) => one.classList.contains(tone))
		);
		expect(tones.length).toBeGreaterThan(1);
		expect(new Set(tones)).toEqual(new Set(['secondary']));
	});

	it('says nothing about them when a rule chose in every group', () => {
		const shown = draw((one) => {
			one.groups = [group()];
			one.summary.needsYou = 0;
		});

		expect(shown.textContent).not.toContain('need your input');
	});
});

describe('copying details to the duplicates', () => {
	/* Not the exact-copies queue next door. Identical BYTES are one file in several places and
	   already share everything recorded about them; this is the file that merely looks the same,
	   bit for bit on the fingerprint, and knows nothing about itself. */
	it('says how many files would gain something before anything is pressed', () => {
		const shown = draw((one) => {
			one.groups = [group()];
			one.carry = { groups: 3, files: 4 };
		});

		expect(shown.querySelector('.standing')?.textContent).toContain(
			'4 duplicates can take the details of an identical copy.'
		);
		expect(shown.textContent).toContain('Copy details');
	});

	it('says nothing at all when every duplicate already has the details of its copy', () => {
		const shown = draw((one) => {
			one.groups = [group()];
			one.carry = { groups: 0, files: 0 };
		});

		expect(shown.textContent).not.toContain('Copy details');
	});

	it('asks first, and says nothing is deleted and each file can be undone on its own', () => {
		const shown = draw((one) => {
			one.groups = [group()];
			one.carry = { groups: 1, files: 1 };
		});

		press(shown, 'Copy details');

		const said = document.body.textContent ?? '';
		expect(said).toContain('1 file in 1 group');
		expect(said).toContain('Nothing is deleted');
		expect(said).toContain('Copy details to the duplicates?');
		expect(shared.carryEverywhere).not.toHaveBeenCalled();
	});
});

describe('saying these are different', () => {
	it('asks before writing an answer that lasts for ever', () => {
		const shown = draw((one) => {
			one.groups = [group({ files: [file('a'), file('b'), file('c')] })];
		});

		press(shown, 'Keep all');

		const said = document.body.textContent ?? '';
		expect(said).toContain('All 3 files are kept');
		expect(said).toContain('Keep all 3 files?');
		expect(said).toContain('even after another scan');
		expect(dismissGroups).not.toHaveBeenCalled();
	});

	/* The server refuses a group holding a file this session may not look at, so the press would
	   do nothing and say nothing. */
	it('offers no Keep all on a group holding a file in a locked vault', () => {
		const shown = draw((one) => {
			one.groups = [
				group({
					files: [file('a'), file('b', { concealed: true, original_filename: null, where: null })]
				})
			];
		});

		const words = [...shown.querySelectorAll('button')].map((b) => b.textContent?.trim());
		expect(words).not.toContain('Keep all');
	});
});

it('says when the files were last compared, from the task on record, under the bar', () => {
	compared.last = { ended_at: Math.floor(Date.now() / 1000) - 7200, outcome: 'done', said: null };
	try {
		const shown = draw((one) => {
			one.groups = [group()];
		});
		expect(shown.querySelector('.standing')?.textContent).toContain(
			'Sift last compared your files 2 hours ago.'
		);
	} finally {
		compared.last = null;
	}
});

describe('a library that was already there when Sift was installed', () => {
	it('offers a scan, because nothing is arriving for one to settle after', () => {
		const shown = draw((one) => {
			one.groups = [];
		});

		press(shown, 'Run the comparison now');

		expect(scanNow).toHaveBeenCalled();
	});
});

it('asks for each still by the address that names it, so a second visit asks nothing', () => {
	/* A bare `/thumb` is answered the careful way: one conditional request per tile on every
	   visit. With the token the browser keeps it; without
	   one the address stays bare, which is slower and never wrong. */
	const shown = draw((one) => {
		one.groups = [group({ files: [file('a', { art: 'tok1' }), file('b')] })];
	});

	expect([...shown.querySelectorAll('img')].map((one) => one.getAttribute('src'))).toEqual([
		'/api/assets/a/thumb?v=tok1',
		'/api/assets/b/thumb'
	]);
});

it('leaves the picture ground where a still cannot be drawn, not a broken picture', () => {
	const shown = draw((one) => {
		one.groups = [group({ files: [file('a'), file('b')] })];
	});

	const still = shown.querySelector<HTMLImageElement>('.file img')!;
	still.dispatchEvent(new Event('error'));

	expect(still.getAttribute('src')).toBe(BLANK_PICTURE);
});

describe('the tiles of one group', () => {
	/* The keep press stands at the tile's foot, so a group's presses make one line whether a name
	   took one line or two; and a name wraps between its words, not in the middle of one. */
	it('puts every keep press at the foot of its tile', async () => {
		const shown = draw((one) => {
			one.groups = [group()];
		});
		const source = (await import('./DuplicatesPanel.svelte?raw')).default;
		const { applyStyles, removeStyles } = await import('$lib/design/testing-styles');
		const press = [...shown.querySelectorAll<HTMLElement>('.file > button')].find((one) =>
			one.textContent?.includes('Keep this instead')
		) as HTMLElement;
		const kept = shown.querySelector('.kept') as HTMLElement;
		applyStyles(source, press.closest('.file'));
		expect(getComputedStyle(press).marginBlockStart).toBe('auto');
		expect(getComputedStyle(kept).marginBlockStart).toBe('auto');
		// The keeping mark is a control's height, so its words share the keep press's line.
		expect(getComputedStyle(kept).getPropertyValue('min-block-size')).toBe('var(--control-height)');
		removeStyles();
		undraw();
	});

	/* A track with a fixed 160-pixel maximum makes the browser count columns at 160, so the
	   eighth card of a group in a row a little narrower than eight of them wraps alone. */
	it('lays every card of a group in one row, counting columns at the smallest card', async () => {
		const eight = Array.from({ length: 8 }, (_, n) => file(`asset-${n}`));
		const shown = draw((one) => {
			one.groups = [group({ files: eight, keeper: 'asset-0' })];
		});
		const source = (await import('./DuplicatesPanel.svelte?raw')).default;
		const { applyStyles, removeStyles } = await import('$lib/design/testing-styles');
		const files = shown.querySelector('.files') as HTMLElement;
		applyStyles(source, files);
		expect(files.querySelectorAll('.file')).toHaveLength(8);
		expect(files.style.getPropertyValue('--cards')).toBe('8');
		const style = getComputedStyle(files);
		expect(style.gridTemplateColumns).toBe('repeat(auto-fit, minmax(min(120px, 100%), 1fr))');
		// The ceiling is the card count times one card's width, named once on the grid.
		expect(style.getPropertyValue('--card')).toBe('160px');
		expect(style.getPropertyValue('max-inline-size')).toContain('var(--cards) * var(--card)');
		removeStyles();
		undraw();
	});

	it('gives a name places to wrap between its words', () => {
		const shown = draw((one) => {
			one.groups = [
				group({
					files: [file('asset-a', { original_filename: 'QuietHarbourDawn.mp4' }), file('asset-b')]
				})
			];
		});
		const name = shown.querySelector('.file .name') as HTMLElement;
		expect(name.textContent).toBe('QuietHarbourDawn.mp4');
		expect(name.querySelectorAll('wbr').length).toBeGreaterThan(0);
		undraw();
	});

	it('calls a length difference of zero what it is, no limit', () => {
		const shown = draw((one) => {
			one.groups = [group()];
		});
		const said = [...shown.querySelectorAll('input')].map((one) => [one.value, one.placeholder]);
		expect(said.flat()).toContain('No limit');
		expect(said.flat()).not.toContain('any');
		undraw();
	});

	it('takes that word from the setting, as the queue answers it', () => {
		const shown = draw((one) => {
			one.groups = [group()];
			one.summary.maxDurationGapWord = 'Any length';
		});
		const said = [...shown.querySelectorAll('input')].map((one) => [one.value, one.placeholder]);
		expect(said.flat()).toContain('Any length');
		expect(said.flat()).not.toContain('No limit');
		undraw();
	});
});
