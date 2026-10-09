import { afterEach, describe, expect, it, vi } from 'vitest';
import { goto } from '$app/navigation';
import { pressOnCard } from './card-press';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

function card(): { box: HTMLElement; words: HTMLElement; field: HTMLElement } {
	const box = document.createElement('div');
	box.innerHTML = '<p>Words</p><input />';
	document.body.append(box);
	return { box, words: box.querySelector('p')!, field: box.querySelector('input')! };
}

function press(on: HTMLElement, opens: Parameters<typeof pressOnCard>[1], init = {}) {
	const box = on.closest('div')!;
	const listen = (event: Event) => pressOnCard(event as MouseEvent, opens);
	box.addEventListener('click', listen);
	on.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, ...init }));
	box.removeEventListener('click', listen);
}

afterEach(() => {
	document.body.replaceChildren();
	vi.mocked(goto).mockClear();
	vi.unstubAllGlobals();
});

describe('a press on a card', () => {
	it('goes to the address where the card holds no link to it', () => {
		const { words } = card();
		press(words, '/people/1');
		expect(goto).toHaveBeenCalledWith('/people/1');
	});

	it('leaves a field to itself, and does nothing for a card that opens nothing', () => {
		const { words, field } = card();
		press(field, '/people/1');
		press(words, undefined);
		expect(goto).not.toHaveBeenCalled();
	});

	it('opens a new tab where the keys ask for one', () => {
		const open = vi.fn();
		vi.stubGlobal('open', open);
		const { words } = card();
		press(words, '/people/1', { metaKey: true });
		expect(open).toHaveBeenCalledWith('/people/1', '_blank');
		expect(goto).not.toHaveBeenCalled();
	});
});
