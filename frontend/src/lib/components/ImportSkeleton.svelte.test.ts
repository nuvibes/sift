import { afterEach, describe, expect, it } from 'vitest';
import { mount } from 'svelte';
import ImportSkeleton from './ImportSkeleton.svelte';

/* The placeholder a file wears while it is being taken in. What matters is that someone who cannot
 * see the shimmer is still told what it is, and that it names the file when it has one.
 */

let host: HTMLElement;

afterEach(() => {
	host?.remove();
});

function render(props: { name?: string } = {}) {
	host = document.createElement('div');
	document.body.append(host);
	mount(ImportSkeleton, { target: host, props });
	return host.querySelector('.skeleton') as HTMLElement;
}

describe('the importing placeholder', () => {
	it('announces itself for a screen reader', () => {
		const skeleton = render();

		expect(skeleton.getAttribute('role')).toBe('img');
		expect(skeleton.getAttribute('aria-label')).toBe('Importing');
	});

	it('names the file when it knows it', () => {
		const skeleton = render({ name: 'holiday.mp4' });

		expect(skeleton.getAttribute('aria-label')).toBe('Importing holiday.mp4');
	});

	it('shows the in-progress status', () => {
		const skeleton = render();

		expect(skeleton.textContent).toContain('Importing');
		expect(skeleton.querySelector('.dot')).not.toBeNull();
	});
});
