/*
 * A sideways stroke steps through the run; nothing else does.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { readStroke, readSwipe, swipeBetween, SWIPE_DISTANCE, SWIPE_TIME } from './swipe';

describe('which strokes are a step', () => {
	it('reads a quick stroke to the left as next and to the right as previous', () => {
		expect(readSwipe(-120, 10, 200)).toBe('next');
		expect(readSwipe(120, -10, 200)).toBe('previous');
	});

	it('refuses a short one, a slow one and a mostly upright one', () => {
		expect(readSwipe(-(SWIPE_DISTANCE - 1), 0, 100)).toBeNull();
		expect(readSwipe(-200, 0, SWIPE_TIME + 1)).toBeNull();
		expect(readSwipe(-100, 90, 100)).toBeNull();
	});

	it('never steps on a stroke down or up, however quick', () => {
		expect(readSwipe(10, 160, 100)).toBeNull();
		expect(readSwipe(-10, -160, 100)).toBeNull();
	});
});

describe('which way a stroke went, the one reading a sheet is put away by too', () => {
	it('reads a quick stroke down as down and up as up', () => {
		expect(readStroke(8, 140, 200)).toBe('down');
		expect(readStroke(-8, -140, 200)).toBe('up');
		expect(readStroke(-140, 8, 200)).toBe('left');
		expect(readStroke(140, 8, 200)).toBe('right');
	});

	it('holds a stroke down to the same distance, slant and time as a sideways one', () => {
		expect(readStroke(0, SWIPE_DISTANCE - 1, 100)).toBeNull();
		expect(readStroke(0, 200, SWIPE_TIME + 1)).toBeNull();
		expect(readStroke(90, 100, 100)).toBeNull();
		expect(readStroke(0, SWIPE_DISTANCE, 100)).toBe('down');
	});
});

describe('the stroke on the element', () => {
	let node: HTMLElement;
	let stop: (() => void) | void;

	afterEach(() => {
		if (typeof stop === 'function') stop();
		node?.remove();
	});

	function mountOn(live = true) {
		node = document.createElement('div');
		node.innerHTML = '<video></video><div class="player-bar"><button>Play</button></div>';
		document.body.append(node);
		const next = vi.fn();
		const previous = vi.fn();
		stop = swipeBetween(() => ({ live, next, previous }))(node);
		return { next, previous };
	}

	function stroke(target: Element, from: number, to: number, pointerType = 'touch') {
		const at = (type: string, x: number) =>
			target.dispatchEvent(
				new PointerEvent(type, {
					bubbles: true,
					pointerType,
					isPrimary: true,
					pointerId: 3,
					clientX: x,
					clientY: 200
				})
			);
		at('pointerdown', from);
		at('pointerup', to);
	}

	it('steps on a finger drawn across the picture, and swallows the click it leaves', () => {
		const { next, previous } = mountOn();
		const video = node.querySelector('video')!;

		stroke(video, 300, 60);
		const heard = vi.fn();
		video.addEventListener('click', heard);
		video.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		stroke(video, 60, 300);

		expect(next).toHaveBeenCalledOnce();
		expect(previous).toHaveBeenCalledOnce();
		expect(heard).not.toHaveBeenCalled();
	});

	it('leaves a mouse, a control and a desktop window alone', () => {
		const { next } = mountOn();
		stroke(node.querySelector('video')!, 300, 60, 'mouse');
		stroke(node.querySelector('.player-bar button')!, 300, 60);
		if (typeof stop === 'function') stop();
		node.remove();
		const idle = mountOn(false);
		stroke(node.querySelector('video')!, 300, 60);

		expect(next).not.toHaveBeenCalled();
		expect(idle.next).not.toHaveBeenCalled();
	});
});

describe('the pull down that puts the viewer away', () => {
	let node: HTMLElement;
	let stop: (() => void) | void;

	afterEach(() => {
		if (typeof stop === 'function') stop();
		node?.remove();
	});

	function mount() {
		node = document.createElement('div');
		node.innerHTML = '<video></video>';
		document.body.append(node);
		const close = vi.fn();
		stop = swipeBetween(() => ({ live: true, close }))(node);
		return { close, video: node.querySelector('video')! };
	}

	const pointer = (target: Element, type: string, y: number) =>
		target.dispatchEvent(
			new PointerEvent(type, {
				bubbles: true,
				pointerType: 'touch',
				isPrimary: true,
				pointerId: 4,
				clientX: 100,
				clientY: y
			})
		);

	/* The browser's own half of a pull on a box it may scroll: it takes the stroke as it moves
	   (`pointercancel`) and the touch ends where the finger left the glass. */
	function pulledByTheBrowser(target: Element, from: number, to: number) {
		pointer(target, 'pointerdown', from);
		pointer(target, 'pointercancel', from + 10);
		const touch = { clientX: 100, clientY: to, identifier: 0, target } as unknown as Touch;
		target.dispatchEvent(
			Object.assign(new Event('touchend', { bubbles: true }), { changedTouches: [touch] })
		);
	}

	it('closes on a stroke down, whether it ends as a pointer or the browser took it over', () => {
		const { close, video } = mount();
		pointer(video, 'pointerdown', 100);
		pointer(video, 'pointerup', 300);
		expect(close).toHaveBeenCalledTimes(1);

		pulledByTheBrowser(video, 100, 300);
		expect(close).toHaveBeenCalledTimes(2);
	});

	it('does not close when the stroke began in a box scrolled away from its top', () => {
		const { close, video } = mount();
		node.scrollTop = 0;
		Object.defineProperty(node, 'scrollTop', { configurable: true, get: () => 40 });
		pulledByTheBrowser(video, 100, 300);
		pointer(video, 'pointerdown', 100);
		pointer(video, 'pointerup', 300);
		expect(close).not.toHaveBeenCalled();
	});

	it('never closes on a stroke up or a short one', () => {
		const { close, video } = mount();
		pulledByTheBrowser(video, 300, 100);
		pulledByTheBrowser(video, 100, 100 + SWIPE_DISTANCE - 1);
		expect(close).not.toHaveBeenCalled();
	});
});
