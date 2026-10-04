/*
 * The three properties that decide whether the grid is usable at three thousand items.
 *
 * All three fail the same way: the grid still works, and gets slower the more there is of it,
 * so none of them shows up in a screenshot or in an assertion about what is on screen. They are
 * only ever caught by counting: how many observers, how many decoders, how many requests still in
 * flight after the tile that asked for them has gone.
 */

import { beforeEach, describe, expect, test, vi } from 'vitest';
import {
	PreviewLoader,
	VideoPool,
	ViewportWatcher,
	VIDEO_POOL_CAP,
	VISIBLE_POOL_CAP
} from './viewport.svelte';

/** A stand-in that counts how many of itself were constructed. */
class FakeObserver {
	static built = 0;
	static instances: FakeObserver[] = [];

	observed = new Set<Element>();
	margin: string;
	#callback: IntersectionObserverCallback;

	constructor(callback: IntersectionObserverCallback, options: IntersectionObserverInit = {}) {
		FakeObserver.built += 1;
		FakeObserver.instances.push(this);
		this.#callback = callback;
		this.margin = options.rootMargin ?? '';
	}

	observe(element: Element) {
		this.observed.add(element);
	}

	unobserve(element: Element) {
		this.observed.delete(element);
	}

	disconnect() {
		this.observed.clear();
	}

	/** Drive a visibility change the way the browser would. */
	fire(target: Element, isIntersecting: boolean, intersectionRatio = 1) {
		this.#callback(
			[{ target, isIntersecting, intersectionRatio } as IntersectionObserverEntry],
			this as unknown as IntersectionObserver
		);
	}
}

beforeEach(() => {
	FakeObserver.built = 0;
	FakeObserver.instances = [];
	vi.stubGlobal('IntersectionObserver', FakeObserver);
});

describe('two observers for the whole grid', () => {
	test('a hundred tiles share the same two observers', () => {
		const watcher = new ViewportWatcher();

		for (let index = 0; index < 100; index += 1) {
			watcher.watch(document.createElement('div'), () => {});
		}

		// The number that matters. One observer per tile is the natural thing to write and it is
		// the reason grids stop scrolling smoothly: every one of them is re-checked against every
		// scroll, and nothing about the result looks wrong until there are thousands.
		expect(FakeObserver.built, 'a tile built its own observer').toBe(2);
		expect(watcher.observerCount).toBe(2);
		expect(watcher.watchedCount).toBe(100);
	});

	test('a tile that goes away stops being watched, and the observer stays', () => {
		const watcher = new ViewportWatcher();
		const first = document.createElement('div');
		const stop = watcher.watch(first, () => {});
		watcher.watch(document.createElement('div'), () => {});

		stop();

		expect(watcher.watchedCount).toBe(1);
		expect(watcher.observerCount).toBe(2);
	});

	test('the subscriber hears about its own element only', () => {
		const watcher = new ViewportWatcher();
		const mine = document.createElement('div');
		const theirs = document.createElement('div');
		const heard: boolean[] = [];

		watcher.watch(mine, (visible) => heard.push(visible));
		watcher.watch(theirs, () => {});

		FakeObserver.instances[0].fire(theirs, true);
		expect(heard).toEqual([]);

		FakeObserver.instances[0].fire(mine, true);
		expect(heard).toEqual([true]);
	});
});

describe('a slot goes to what is on the screen before the row below it', () => {
	/*
	 * The rank a tile claims a slot with is its share of the SCREEN. The observer that starts a
	 * clip's fetch watches a row either side of the screen as well, and a tile anywhere in that row
	 * is wholly inside its widened box: read from there, the row below the screen would rank as high
	 * as the middle of it, and slots would play tiles nobody could see while tiles on the screen
	 * showed their stills.
	 */
	function observers() {
		const near = FakeObserver.instances.find((one) => one.margin !== '');
		const seen = FakeObserver.instances.find((one) => one.margin === '');
		if (!near || !seen) throw new Error('the grid is not watching the screen and its margin');
		return { near, seen };
	}

	test('a tile in the margin is near and has no share of the screen', () => {
		const watcher = new ViewportWatcher();
		const below = document.createElement('div');
		const heard: [boolean, number][] = [];
		watcher.watch(below, (visible, ratio) => heard.push([visible, ratio]));

		observers().near.fire(below, true, 1);

		expect(heard.at(-1)).toEqual([true, 0]);
	});

	test('a tile on the screen claims with its share of it, and takes the slot from the margin', () => {
		const watcher = new ViewportWatcher();
		const pool = new VideoPool(1);
		const below = document.createElement('div');
		const shown = document.createElement('div');
		watcher.watch(below, (visible, ratio) => visible && pool.claim('below', ratio));
		watcher.watch(shown, (visible, ratio) => visible && pool.claim('shown', ratio));
		const { near, seen } = observers();

		near.fire(below, true, 1);
		near.fire(shown, true, 1);
		seen.fire(shown, true, 0.6);

		expect(pool.holds('shown'), 'the screen lost its slot to the row below it').toBe(true);
		expect(pool.holds('below')).toBe(false);
	});
});

describe('the video pool is capped', () => {
	test('more visible tiles than slots means exactly cap videos', () => {
		const pool = new VideoPool();
		const granted: string[] = [];

		// Twenty tiles on screen at once, which "play everything visible" makes ordinary on a large
		// display. Twenty decoders is not ordinary: it is how a tab runs out of memory.
		for (let index = 0; index < 20; index += 1) {
			if (pool.claim(`asset-${index}`, index)) granted.push(`asset-${index}`);
		}

		expect(pool.size).toBe(VIDEO_POOL_CAP);
		expect(pool.size).toBeLessThanOrEqual(pool.cap);
	});

	test('the tiles being looked at are the ones that get the slots', () => {
		const pool = new VideoPool();

		// Four weak claims fill it, then a strong one arrives.
		for (let index = 0; index < VIDEO_POOL_CAP; index += 1) pool.claim(`edge-${index}`, 0.1);
		pool.claim('centre', 0.9);

		expect(pool.holds('centre'), 'the tile in the middle of the screen was refused').toBe(true);
		expect(pool.size).toBe(VIDEO_POOL_CAP);
	});

	test('a weaker claim than everything already held is refused rather than squeezed in', () => {
		const pool = new VideoPool();
		for (let index = 0; index < VIDEO_POOL_CAP; index += 1) pool.claim(`held-${index}`, 0.8);

		expect(pool.claim('faraway', 0.1)).toBe(false);
		expect(pool.size).toBe(VIDEO_POOL_CAP);
		expect(pool.holds('faraway')).toBe(false);
	});

	test('re-claiming a slot already held does not consume a second one', () => {
		const pool = new VideoPool();
		pool.claim('one', 0.5);
		pool.claim('one', 0.9);

		expect(pool.size).toBe(1);
	});

	test('releasing frees the slot for somebody else', () => {
		const pool = new VideoPool();
		for (let index = 0; index < VIDEO_POOL_CAP; index += 1) pool.claim(`held-${index}`, 0.8);
		pool.release('held-0');

		expect(pool.claim('newcomer', 0.1)).toBe(true);
		expect(pool.size).toBe(VIDEO_POOL_CAP);
	});
});

describe('the cap changes when somebody asks for everything visible', () => {
	/* "Preview everything visible" is supposed to mean everything visible, and against the resting
	 * cap it would mean four, so on a screen of forty tiles, thirty-six would sit still and the
	 * setting would look broken. Four is the number for a pool filling itself from scrolling, not for one that was
	 * asked for. */

	test('a raised cap lets a whole screenful play', () => {
		const pool = new VideoPool();
		pool.resize(VISIBLE_POOL_CAP);

		for (let index = 0; index < 40; index += 1) pool.claim(`tile-${index}`, 0.5);

		expect(pool.size).toBe(40);
	});

	test('and it is still a cap, because a video is a decoder rather than a picture', () => {
		const pool = new VideoPool();
		pool.resize(VISIBLE_POOL_CAP);

		for (let index = 0; index < VISIBLE_POOL_CAP + 25; index += 1) pool.claim(`tile-${index}`, 0.5);

		expect(pool.size).toBe(VISIBLE_POOL_CAP);
	});

	test('lowering it stops the extra ones there and then', () => {
		// Turning the setting off has to stop the videos it started. Waiting for the next claim
		// would leave forty of them decoding until something happened to scroll.
		const pool = new VideoPool();
		pool.resize(VISIBLE_POOL_CAP);
		for (let index = 0; index < 30; index += 1) pool.claim(`tile-${index}`, 0.5);

		pool.resize(VIDEO_POOL_CAP);

		expect(pool.size).toBe(VIDEO_POOL_CAP);
	});

	test('and keeps the strongest claims when it does', () => {
		// The tiles in the middle of the screen, not whichever four were asked for first.
		const pool = new VideoPool();
		pool.resize(VISIBLE_POOL_CAP);
		pool.claim('edge', 0.1);
		pool.claim('middle', 0.9);
		pool.claim('other-edge', 0.2);

		pool.resize(1);

		expect(pool.holds('middle')).toBe(true);
		expect(pool.size).toBe(1);
	});
});

describe('a preview stops being fetched when its tile leaves', () => {
	test('cancelling aborts the request in flight', async () => {
		const loader = new PreviewLoader();
		let seen: AbortSignal | undefined;

		const never = vi.fn((_url: string, init?: RequestInit) => {
			seen = init?.signal ?? undefined;
			// A request that never settles on its own, so the only way out is the abort.
			return new Promise<Response>((_resolve, reject) => {
				init?.signal?.addEventListener('abort', () => reject(new Error('aborted')));
			});
		});

		const pending = loader.prefetch('asset', '/api/assets/asset/preview', never as never);
		expect(loader.inFlightCount).toBe(1);

		loader.cancel('asset');
		await pending;

		expect(seen?.aborted, 'the fetch was left running after the tile scrolled away').toBe(true);
		expect(loader.inFlightCount).toBe(0);
	});

	test('a fast scroll does not leave a queue of requests behind it', async () => {
		const loader = new PreviewLoader();
		const fetcher = vi.fn(
			(_url: string, init?: RequestInit) =>
				new Promise<Response>((_resolve, reject) => {
					init?.signal?.addEventListener('abort', () => reject(new Error('aborted')));
				})
		);

		// Two hundred tiles crossed in a couple of seconds, each cancelled as it went past.
		const started = [];
		for (let index = 0; index < 200; index += 1) {
			started.push(loader.prefetch(`asset-${index}`, `/preview/${index}`, fetcher as never));
		}
		for (let index = 0; index < 200; index += 1) loader.cancel(`asset-${index}`);
		await Promise.all(started);

		expect(loader.inFlightCount, 'requests outlived the tiles that asked for them').toBe(0);
	});

	test('asking twice for the same clip only fetches once', async () => {
		const loader = new PreviewLoader();
		const fetcher = vi.fn(
			(_url: string, init?: RequestInit) =>
				new Promise<Response>((_resolve, reject) => {
					init?.signal?.addEventListener('abort', () => reject(new Error('aborted')));
				})
		);

		const first = loader.prefetch('asset', '/preview', fetcher as never);
		const second = loader.prefetch('asset', '/preview', fetcher as never);
		loader.cancel('asset');
		await Promise.all([first, second]);

		expect(fetcher).toHaveBeenCalledTimes(1);
	});

	test('a failed fetch leaves the tile with its still picture and no error', async () => {
		const loader = new PreviewLoader();
		const failing = vi.fn(() => Promise.reject(new Error('network')));

		await loader.prefetch('asset', '/preview', failing as never);

		expect(loader.urlFor('asset')).toBeUndefined();
		expect(loader.inFlightCount).toBe(0);
	});
});

describe('a preview set aside because it was not built yet can be asked for again', () => {
	/* A video's preview does not exist until the sprite job behind it finishes, so a tile that
	 * comes on screen mid-scan answers 404 and is remembered as missing: otherwise every scroll
	 * re-asks it and an image-heavy library spends a refused request per tile for ever. But
	 * "missing" for the life of the loader would hide a sprite built after the tile was first
	 * seen until the page was reloaded. `forget` is what the grid calls when work settles to let
	 * it be asked once more.
	 */
	test('a 404 is remembered, then forgotten so the built clip loads', async () => {
		vi.stubGlobal('URL', {
			createObjectURL: () => 'blob:preview',
			revokeObjectURL: () => {}
		});

		const loader = new PreviewLoader();
		// Still being built when the tile first appears: 404, and the still picture stays.
		const notYet = vi.fn(() => Promise.resolve({ ok: false } as Response));
		await loader.prefetch('vid', '/preview/vid', notYet as never);
		expect(loader.urlFor('vid')).toBeUndefined();

		// Remembered as missing: asking again does not even reach the fetcher.
		await loader.prefetch('vid', '/preview/vid', notYet as never);
		expect(notYet).toHaveBeenCalledTimes(1);

		// The sprite job has finished and work settled, so the miss is forgotten: now the clip loads.
		loader.forget('vid');
		const built = vi.fn(() =>
			Promise.resolve({ ok: true, blob: () => Promise.resolve(new Blob(['clip'])) } as Response)
		);
		await loader.prefetch('vid', '/preview/vid', built as never);
		expect(built).toHaveBeenCalledTimes(1);
		expect(loader.urlFor('vid')).toBeDefined();
	});
});

describe('a refused clip is remembered by its address', () => {
	test('the same address is not asked again, and a new one is', async () => {
		vi.stubGlobal('URL', {
			createObjectURL: () => 'blob:preview',
			revokeObjectURL: () => {}
		});

		const loader = new PreviewLoader();
		const refused = vi.fn(() => Promise.resolve({ ok: false } as Response));
		await loader.prefetch('vid', '/preview/vid?v=one', refused as never);
		await loader.prefetch('vid', '/preview/vid?v=one', refused as never);
		expect(refused, 'a refused address was asked again').toHaveBeenCalledTimes(1);

		const built = vi.fn(() =>
			Promise.resolve({ ok: true, blob: () => Promise.resolve(new Blob(['clip'])) } as Response)
		);
		await loader.prefetch('vid', '/preview/vid?v=two', built as never);
		expect(built, 'the clip at its new address was never asked for').toHaveBeenCalledTimes(1);
		expect(loader.urlFor('vid')).toBeDefined();
	});
});

describe('loaded clips do not accumulate for ever', () => {
	/*
	 * A browser holds a blob until its URL is revoked. Without a bound, a long scroll through a
	 * large library keeps every clip it ever fetched (a fifth of a megabyte each, thousands of
	 * them), and the machine this is meant to stay smooth on is the first to notice. Nothing looks
	 * wrong while it happens.
	 */
	function clipFetcher() {
		return vi.fn(() =>
			Promise.resolve({ ok: true, blob: () => Promise.resolve(new Blob(['clip'])) } as Response)
		);
	}

	test('the cache stops growing, and what it drops it releases', async () => {
		const revoked: string[] = [];
		vi.stubGlobal('URL', {
			createObjectURL: (blob: Blob) => `blob:${revoked.length}-${blob.size}-${Math.random()}`,
			revokeObjectURL: (url: string) => revoked.push(url)
		});

		const loader = new PreviewLoader(4);
		const fetcher = clipFetcher();

		for (let index = 0; index < 20; index += 1) {
			await loader.prefetch(`asset-${index}`, `/preview/${index}`, fetcher as never);
		}

		expect(loader.readyCount).toBe(4);
		expect(revoked.length, 'clips were dropped without releasing their memory').toBe(16);
	});

	test('a clip still being asked for is not the one dropped', async () => {
		vi.stubGlobal('URL', {
			createObjectURL: () => `blob:${Math.random()}`,
			revokeObjectURL: () => {}
		});

		const loader = new PreviewLoader(3);
		const fetcher = clipFetcher();

		await loader.prefetch('keep', '/preview/keep', fetcher as never);
		await loader.prefetch('b', '/preview/b', fetcher as never);
		await loader.prefetch('c', '/preview/c', fetcher as never);
		// Asking again is the grid saying it is still wanted.
		await loader.prefetch('keep', '/preview/keep', fetcher as never);
		await loader.prefetch('d', '/preview/d', fetcher as never);

		expect(loader.urlFor('keep'), 'the clip being looked at was evicted').toBeDefined();
		expect(loader.urlFor('b')).toBeUndefined();
	});

	test('a clip a tile on screen wants is never dropped, however many are on screen', async () => {
		/*
		 * A cache smaller than the screen would evict, with each clip that arrived, one that a visible
		 * tile was playing, so "Preview everything visible" would light only some of what is in view.
		 * The ceiling is for what has gone off the screen; what is on it stays.
		 */
		const revoked: string[] = [];
		vi.stubGlobal('URL', {
			createObjectURL: () => `blob:${Math.random()}`,
			revokeObjectURL: (url: string) => revoked.push(url)
		});
		const onScreen = new Set<string>();
		const loader = new PreviewLoader(4, (id) => onScreen.has(id));
		const fetcher = clipFetcher();

		for (let index = 0; index < 10; index += 1) {
			onScreen.add(`seen-${index}`);
			await loader.prefetch(`seen-${index}`, `/preview/${index}`, fetcher as never);
		}
		expect(loader.readyCount, 'a clip on screen was evicted').toBe(10);
		expect(revoked).toEqual([]);

		// Scrolled away: the next arrival reclaims down to the ceiling, oldest first.
		onScreen.clear();
		onScreen.add('next');
		await loader.prefetch('next', '/preview/next', fetcher as never);
		expect(loader.readyCount).toBe(4);
		expect(loader.urlFor('next')).toBeDefined();
		expect(loader.urlFor('seen-0')).toBeUndefined();
	});

	test('reading a clip does not change anything, because reads happen while drawing', async () => {
		/*
		 * The trap this guards: marking an entry as recently used on read is the obvious way to
		 * write an LRU, and this one is read during render. Mutating reactive state there is a
		 * re-render loop: the same shape as a grid fetching its first page for as long as the tab
		 * is open.
		 */
		vi.stubGlobal('URL', {
			createObjectURL: () => `blob:${Math.random()}`,
			revokeObjectURL: () => {}
		});

		const loader = new PreviewLoader(2);
		const fetcher = clipFetcher();
		await loader.prefetch('first', '/preview/first', fetcher as never);
		await loader.prefetch('second', '/preview/second', fetcher as never);

		// Read the older one many times. If reading reordered the cache, the next insert would drop
		// a different entry than it does here.
		for (let index = 0; index < 10; index += 1) loader.urlFor('first');
		await loader.prefetch('third', '/preview/third', fetcher as never);

		expect(loader.readyCount).toBe(2);
		expect(loader.urlFor('first'), 'reading reordered the cache').toBeUndefined();
	});
});

describe('a clip built again is fetched again', () => {
	/* A clip is held by the tile's id, and a rebuilt clip has a new address (its token is the
	 * picture's digest). Held by id alone, the tile would go on playing the clip from before the
	 * rebuild for as long as the tab was open, however many times the page was read. */
	test('a new address for a held clip fetches it, and the old one is let go once it lands', async () => {
		let made = 0;
		const revoked: string[] = [];
		vi.stubGlobal('URL', {
			createObjectURL: () => `blob:${(made += 1)}`,
			revokeObjectURL: (url: string) => revoked.push(url)
		});
		const loader = new PreviewLoader();
		const fetcher = vi.fn(() =>
			Promise.resolve({ ok: true, blob: () => Promise.resolve(new Blob(['clip'])) } as Response)
		);

		await loader.prefetch('vid', '/preview/vid?v=one', fetcher as never);
		await loader.prefetch('vid', '/preview/vid?v=one', fetcher as never);
		expect(fetcher, 'the same address was fetched twice').toHaveBeenCalledTimes(1);

		await loader.prefetch('vid', '/preview/vid?v=two', fetcher as never);

		expect(fetcher, 'the rebuilt clip was never asked for').toHaveBeenCalledTimes(2);
		expect(loader.urlFor('vid')).toBe('blob:2');
		expect(revoked).toEqual(['blob:1']);
	});
});

describe('how many previews are moving', () => {
	/* What the preview control's tooltip counts: the holders drawn playing, which is a slot AND the
	 * clip in hand. A slot held for a clip still on its way is a still picture. */
	test('counts the holders the question says are moving, and follows them as they leave', () => {
		const pool = new VideoPool(VISIBLE_POOL_CAP);
		for (const id of ['a', 'b', 'c']) pool.claim(id, 1);
		const inHand = new Set(['a', 'b']);

		expect(pool.moving((id) => inHand.has(id))).toBe(2);

		pool.release('a');
		expect(
			pool.moving((id) => inHand.has(id)),
			'a tile scrolled away was still counted'
		).toBe(1);
	});

	test('never counts past the ceiling, however many tiles are in view', () => {
		const pool = new VideoPool(VISIBLE_POOL_CAP);
		for (let index = 0; index < VISIBLE_POOL_CAP + 12; index += 1) pool.claim(`t${index}`, 1);

		expect(pool.moving(() => true)).toBe(VISIBLE_POOL_CAP);
	});
});
