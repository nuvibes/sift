/* A player's screen changing: one movement, on one pair of tokens, for every player.
 *
 * The first half is the movement itself: it reads `--dur-stage` and `--ease-stage` (and their
 * leaving pair for a change that makes the box smaller), it starts from the box the stage stood
 * in, and reduced motion makes it instant. The second half is the gate: every player's stage
 * moves through this module, and none of them writes a transition, a duration or a movement of
 * its own for its screen change.
 */

import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { motion } from '$lib/shell/motion.svelte';
import {
	aboutToChange,
	handPlace,
	screenChanges,
	settle,
	stagePace,
	stageTransition,
	takePlace,
	travel
} from './motion';

const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const read = (path: string) => readFileSync(join(SOURCE, path), 'utf8');

function box(left: number, top: number, width: number, height: number): DOMRect {
	return {
		left,
		top,
		width,
		height,
		x: left,
		y: top,
		right: left + width,
		bottom: top + height
	} as DOMRect;
}

/** A node standing at `at`, which records what it is asked to animate. */
function stageAt(at: DOMRect) {
	const node = document.createElement('div');
	document.body.append(node);
	let where = at;
	node.getBoundingClientRect = () => where;
	const animate = vi.fn(
		(_frames: Keyframe[], _options: KeyframeAnimationOptions) => ({}) as Animation
	);
	node.animate = animate as unknown as typeof node.animate;
	node.getAnimations = () => [];
	return { node, animate, moveTo: (next: DOMRect) => (where = next) };
}

beforeAll(() => {
	/* Set before anything reads them: a token is read once and kept. Numbers nothing else uses, so
	   a pass cannot come from the fallback. */
	const root = document.documentElement.style;
	root.setProperty('--dur-stage', '331ms');
	root.setProperty('--dur-stage-leave', '211ms');
	root.setProperty('--ease-stage', 'cubic-bezier(0.1, 0.2, 0.3, 1)');
	root.setProperty('--ease-stage-leave', 'cubic-bezier(0.5, 0, 0.9, 1)');
});

afterEach(() => {
	motion.preference = 'full';
	document.body.innerHTML = '';
	takePlace();
});

describe('the movement', () => {
	it('reads the stage tokens, and the leaving pair for a change that makes the box smaller', () => {
		expect(stagePace(false)).toEqual({ duration: 331, curve: [0.1, 0.2, 0.3, 1] });
		expect(stagePace(true)).toEqual({ duration: 211, curve: [0.5, 0, 0.9, 1] });
	});

	it('starts a box growing into the screen from the box it stood in', () => {
		const { node, animate } = stageAt(box(0, 0, 1600, 900));
		settle(node, box(255, 82, 1081, 609));
		const [frames, options] = animate.mock.calls[0];
		// Offset 0: a lone keyframe without one is the END of the movement, played backwards.
		expect(frames).toEqual([
			{
				offset: 0,
				translate: `calc(0px + ${255 + 540.5 - 800}px) calc(0px + ${82 + 304.5 - 450}px)`,
				scale: `${1081 / 1600}`
			}
		]);
		expect(options).toEqual({ duration: 331, easing: 'cubic-bezier(0.1, 0.2, 0.3, 1)' });
	});

	it('goes back down one pace quicker, on the exit curve', () => {
		const { node, animate } = stageAt(box(255, 82, 1081, 609));
		settle(node, box(0, 0, 1600, 900));
		expect(animate.mock.calls[0][1]).toEqual({
			duration: 211,
			easing: 'cubic-bezier(0.5, 0, 0.9, 1)'
		});
	});

	it('is instant under reduced motion', () => {
		motion.preference = 'reduce';
		const { node, animate } = stageAt(box(0, 0, 1600, 900));
		expect(settle(node, box(255, 82, 1081, 609))).toBeNull();
		expect(animate).not.toHaveBeenCalled();
		expect(stagePace(false).duration).toBe(0);
	});

	it('moves a stage whose key changes, from where it stood at the press', () => {
		const { node, animate, moveTo } = stageAt(box(1064, 591, 520, 293));
		const action = screenChanges(node, { key: false, fade: true });
		document.dispatchEvent(new Event('pointerdown'));
		moveTo(box(400, 772, 800, 64));
		action.update({ key: true, fade: true });
		expect(animate).toHaveBeenCalledTimes(1);
		expect(animate.mock.calls[0][0][0]).toMatchObject({ offset: 0, opacity: 0 });
		action.destroy();
	});

	it('moves only the outermost stage when the screen fills, never a cell inside a moving wall', () => {
		vi.stubGlobal('requestAnimationFrame', (run: FrameRequestCallback) => {
			run(0);
			return 0;
		});
		const wall = stageAt(box(233, 149, 1334, 727));
		const cell = stageAt(box(233, 149, 660, 360));
		wall.node.append(cell.node);
		const walled = screenChanges(wall.node);
		const celled = screenChanges(cell.node);
		wall.moveTo(box(0, 70, 1600, 830));
		cell.moveTo(box(0, 70, 792, 412));
		document.dispatchEvent(new Event('fullscreenchange'));
		expect(wall.animate).toHaveBeenCalledTimes(1);
		expect(cell.animate).not.toHaveBeenCalled();
		celled.destroy();
		walled.destroy();
		vi.unstubAllGlobals();
	});

	it('hands a place from one player to the next once', () => {
		handPlace(box(1, 2, 3, 4));
		expect(takePlace()).toEqual(box(1, 2, 3, 4));
		expect(takePlace()).toBeNull();
		aboutToChange();
	});

	it('grows an arriving player out of the place it was handed, and fades or shrinks a leaving one', () => {
		const { node } = stageAt(box(0, 0, 1000, 500));
		const arriving = stageTransition(node, { from: () => box(250, 125, 500, 250) })({
			direction: 'in'
		});
		expect(arriving.duration).toBe(331);
		expect(arriving.css(0, 1)).toBe('opacity: 0; transform: translate(0px, 0px) scale(0.5)');
		const leaving = stageTransition(node)({ direction: 'out' });
		expect(leaving.duration).toBe(211);
		expect(leaving.css(0.5, 0.5)).toBe('opacity: 0.5');
		const shrinking = stageTransition(node, {
			from: () => box(0, 0, 10, 10),
			leave: () => 'shrink'
		})({
			direction: 'out'
		});
		expect(shrinking.duration).toBe(211);
		expect(shrinking.css(0, 1)).toBe('opacity: 0; transform: translate(0px, 0px) scale(0.88)');
	});

	it('measures about the centre, one scale for both sides', () => {
		expect(travel(box(0, 0, 100, 50), box(0, 0, 200, 100))).toEqual({
			dx: -50,
			dy: -25,
			scale: 0.5
		});
	});
});

describe('every player moves through this module', () => {
	/* The five players' stages: the Player's (the frame it and the viewer share), the viewer
	   opening and closing, the mini player and the Audio player, a Theater wall, and the Player
	   and a Theater wall handing their picture to the corner. */
	const players: Record<string, RegExp> = {
		'lib/components/player/MediaStage.svelte': /use:screenChanges=\{\{ key: filling \}\}/,
		'lib/components/AssetModal.svelte': /stageTransition\(node, \{\s*from: takePlace,/,
		'lib/components/player/MiniPlayer.svelte': /use:screenChanges=\{\{ key: mini\.bar \|\| docked/,
		'lib/components/theater/TheaterWall.svelte': /<div class="stages" use:screenChanges>/,
		'lib/components/player/Player.svelte': /handPlace\(frame\?\.element/,
		'lib/theater/corner.ts': /handPlace\(document\.querySelector\('\[data-theater-wall\]'\)/
	};

	/* The selectors that ARE a player's stage. A `transition` or an `animation` written on one of
	   them is a second movement for the screen change, with a duration nobody else reads. */
	const stages: Record<string, string[]> = {
		'lib/components/player/MediaStage.svelte': ['.stage', '.stage.filling'],
		'lib/components/AssetModal.svelte': ['.sheet', '.sheet.fills'],
		'lib/components/player/MiniPlayer.svelte': ['.mini', '.mini.bar', '.mini.docked'],
		'lib/components/theater/TheaterWall.svelte': ['.wall']
	};

	for (const [path, selectors] of Object.entries(stages)) {
		it(`${path} writes no transition or duration of its own on its stage`, () => {
			const source = read(path);
			for (const selector of selectors) {
				const escaped = selector.replace(/\./g, '\\.');
				const rules = [...source.matchAll(new RegExp(`\\n\\t${escaped} \\{([^}]*)\\}`, 'g'))];
				// Found at all, or the gate reads nothing and passes on nothing.
				expect(rules.length, selector).toBeGreaterThan(0);
				for (const [, body] of rules) {
					expect(body, selector).not.toMatch(/(^|\s)(transition|animation)[a-z-]*\s*:/);
				}
			}
		});
	}

	it('keeps no screen-change movement of its own in the players: each moves through this one', () => {
		for (const [path, uses] of Object.entries(players)) expect(read(path), path).toMatch(uses);
		expect(read('lib/components/theater/TheaterWall.svelte')).not.toMatch(/pace: 'ambient'/);
		expect(read('lib/components/AssetModal.svelte')).not.toMatch(/arrive\(node, \{ pace: 'slow'/);
		expect(read('lib/components/player/MiniPlayer.svelte')).not.toMatch(/\{ duration: 0 \}/);
	});

	it('declares the stage tokens once, and only the movement reads them', () => {
		const css = read('app.css');
		for (const token of [
			'--dur-stage',
			'--dur-stage-leave',
			'--ease-stage',
			'--ease-stage-leave'
		]) {
			expect(css.match(new RegExp(`\\n\\t${token}:`, 'g'))?.length, token).toBe(1);
		}
		for (const path of [...Object.keys(players), 'lib/components/player/fullscreen.ts']) {
			expect(read(path), path).not.toMatch(/--(dur|ease)-stage/);
		}
	});
});
