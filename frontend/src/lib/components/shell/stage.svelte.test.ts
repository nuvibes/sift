import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { drawnWhileFilled, stage } from './stage.svelte';

// jsdom has no fullscreen; the real `null` here is what lets the guard's test fail.
beforeEach(() => {
	Object.defineProperty(document, 'fullscreenElement', { value: null, configurable: true });
});

/* Both are null at rest and before registering, which must not read as "filled". */

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

	// `B` on a screen driving its own chrome (Theater) must tell it, or the bar stays hidden.
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
		// The positive control: elsewhere the shell's own clock is the only answer.
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

// The toaster lives outside the filled box, so it is carried in while filled and back after.
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
