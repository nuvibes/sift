import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { drawnWhileFilled, stage } from './stage.svelte';

/*
 * `document.fullscreenElement`, spelled out.
 *
 * This environment has no fullscreen at all, so the property is `undefined` rather than `null`,
 * and `undefined === null` is false, which quietly makes the fault below untestable. Putting the
 * real value in is what makes the mutation fail: with the guard removed and this stub in place,
 * every test here goes red, and without the stub none of them do.
 */
beforeEach(() => {
	Object.defineProperty(document, 'fullscreenElement', { value: null, configurable: true });
});

/* The one question this store answers ("is the shell filling the window"), and the way it can
   get that question wrong for every screen that is not.

   `document.fullscreenElement` is null at rest, and the element is null until the layout registers
   it, which is one effect later than the first time the watcher runs. Comparing the two directly
   would make `null === null` mean "filled". */

afterEach(() => stage.register(null));

describe('whether the shell is filling the window', () => {
	it('is no while nothing has been registered, though both sides are null', async () => {
		stage.register(null);
		const unwatch = stage.watch();

		expect(stage.filling, 'an unregistered shell reported itself as filling the window').toBe(
			false
		);
		unwatch();
	});

	it('is no for a registered element that is not the fullscreen one', () => {
		stage.register(document.createElement('div'));
		const unwatch = stage.watch();

		expect(stage.filling).toBe(false);
		unwatch();
	});

	/*
	 * What a wrong answer to "is this filled" would cost, none of which looks like this store: the
	 * Theater screen hides its own heading while filled, so the heading would vanish; the bar goes
	 * on an idle clock while filled, so it would vanish in an ordinary window; and sending the wall
	 * to the corner leaves fullscreen on the way, so a wrong flag would fill the window instead.
	 *
	 * `B` on a screen that drives its own chrome, which is Theater alone. The contract for `driven`
	 * is that the screen writes `barHidden`; the wall re-asserts its answer only when its answer
	 * changes, so `B` writing `barHidden` directly would leave the bar hidden with nothing bringing
	 * it back.
	 */
	it('tells a driven screen rather than writing over it', () => {
		stage.register(null);
		Object.defineProperty(document, 'fullscreenElement', { value: null, configurable: true });
		const element = document.createElement('div');
		stage.register(element);
		Object.defineProperty(document, 'fullscreenElement', { value: element, configurable: true });
		const unwatch = stage.watch();
		stage.driven = true;
		stage.barHidden = false;

		stage.toggleBar();

		expect(stage.dismissed, 'the key said nothing the screen could read').toBe(true);
		expect(
			stage.barHidden,
			'the shell wrote the answer a driven screen is meant to write itself'
		).toBe(false);

		stage.toggleBar();
		expect(stage.dismissed, 'the same key did not bring it back').toBe(false);

		stage.driven = false;
		unwatch();
	});

	it('still writes the answer itself on a screen nothing is driving', () => {
		/* The positive control. Every other filled screen has no chrome of its own, so the shell's
		   own clock is the only thing that can answer, and a branch that always deferred would
		   leave the key doing nothing at all there. */
		const element = document.createElement('div');
		stage.register(element);
		Object.defineProperty(document, 'fullscreenElement', { value: element, configurable: true });
		const unwatch = stage.watch();
		stage.driven = false;
		stage.barHidden = false;

		stage.toggleBar();

		expect(stage.barHidden, 'the bar was not sent away').toBe(true);
		expect(stage.dismissed, 'a screen with nothing to tell was told anyway').toBe(false);

		stage.barHidden = false;
		unwatch();
	});

	it('will not send the bar away in a window', () => {
		stage.register(null);
		const unwatch = stage.watch();

		stage.toggleBar();

		expect(stage.barHidden, 'the bar was dismissed on a screen nothing was filling').toBe(false);
		unwatch();
	});
});

/*
 * A LAYER THAT LIVES OUTSIDE THE FILLED BOX IS CARRIED INTO IT, and back out.
 *
 * The toaster is drawn after the shell, and a browser filling the screen paints the filled element's
 * subtree and nothing else, so left outside, every toast raised on a filled wall would be raised,
 * counted down and dismissed with nothing on screen.
 */
describe('a layer kept drawn while the screen is filled', () => {
	function arrange() {
		const box = document.createElement('div');
		const outside = document.createElement('div');
		const layer = document.createElement('div');
		const after = document.createElement('span');
		outside.append(layer, after);
		document.body.append(box, outside);
		stage.register(box);
		return { box, outside, layer, after };
	}

	afterEach(() => {
		stage.filling = false;
		document.body.replaceChildren();
	});

	it('goes inside the filled box while the screen is filled', () => {
		const { box, layer } = arrange();
		stage.filling = true;

		drawnWhileFilled(layer);

		expect(box.contains(layer), 'the layer was left outside what the browser paints').toBe(true);
	});

	it('comes back to exactly where it was', () => {
		const { outside, layer, after } = arrange();
		stage.filling = true;

		const back = drawnWhileFilled(layer);
		back?.();

		expect(layer.parentElement).toBe(outside);
		expect(layer.nextSibling, 'it came back somewhere else in its parent').toBe(after);
	});

	it('stays where it is while the screen is not filled', () => {
		const { outside, layer } = arrange();

		expect(drawnWhileFilled(layer)).toBeUndefined();
		expect(layer.parentElement).toBe(outside);
	});
});
