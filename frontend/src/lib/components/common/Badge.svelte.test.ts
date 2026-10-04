import { afterEach, describe, expect, it } from 'vitest';
import { mount, unmount } from 'svelte';
import { words } from '$lib/design/testing.svelte';
import codepoints from '$lib/generated/icon-codepoints.json';
import Badge from './Badge.svelte';

/* The states, and which of them are red.
 *
 * Red means a job failed or something is about to be destroyed. Blocked means a site wants cookies;
 * quarantined means the check at the door refused a file. Both of those want a person, and
 * neither of them is a failure: painting them red teaches people that red is background noise,
 * and then the red that matters is background noise too. Canceled is the same argument from the
 * other end: somebody stopped it deliberately, so it is not a failure either.
 */

let host: HTMLElement;

afterEach(() => {
	host?.remove();
});

/** The character an icon name draws as, so a test can name the glyph rather than the codepoint. */
function glyph(name: keyof typeof codepoints): string {
	return String.fromCodePoint(parseInt(codepoints[name], 16));
}

function render(props: Parameters<typeof Badge>[1]) {
	host = document.createElement('div');
	document.body.append(host);
	const component = mount(Badge, { target: host, props });
	return {
		badge: host.querySelector('.badge') as HTMLElement,
		component
	};
}

describe('the state', () => {
	it('says itself, without the screen having to supply the word', () => {
		const { badge } = render({ state: 'quarantined' });

		expect(words(badge)).toBe('Quarantined');
	});

	it('can be said more precisely when the screen knows more', () => {
		// "Waiting for cookies" is the useful sentence. The colour is still blocked's.
		const { badge } = render({ state: 'blocked', label: 'Waiting for cookies' });

		expect(words(badge)).toBe('Waiting for cookies');
		expect(badge.classList.contains('state-blocked')).toBe(true);
	});
});

describe('in a column narrower than its words', () => {
	it('is never wider than what it stands in, and cuts its words short inside the pill', async () => {
		/* A pill that cannot give way runs a running task's state on under Activity's Time left. */
		const { applyStyles, removeStyles } = await import('$lib/design/testing-styles');
		const { default: source } = await import('./Badge.svelte?raw');
		const { badge } = render({ state: 'queued', label: 'Running, 8 in progress' });
		applyStyles(source, badge);
		try {
			expect(getComputedStyle(badge).maxInlineSize).toBe('100%');
			const word = badge.querySelector<HTMLElement>('.word');
			expect(word?.textContent).toBe('Running, 8 in progress');
			expect(getComputedStyle(word!).overflow).toBe('hidden');
			expect(getComputedStyle(word!).textOverflow).toBe('ellipsis');
			expect(getComputedStyle(word!).minInlineSize).toBe('0px');
		} finally {
			removeStyles();
		}
	});
});

describe('what is amber', () => {
	it.each(['blocked', 'quarantined'] as const)('%s is amber, because it needs you', (state) => {
		const { badge } = render({ state });

		expect(badge.classList.contains(`state-${state}`)).toBe(true);
		expect(badge.classList.contains('state-failed')).toBe(false);
	});
});

describe('what is red', () => {
	it('only failure', () => {
		const { badge } = render({ state: 'failed' });

		expect(badge.classList.contains('state-failed')).toBe(true);
	});

	it('not a job somebody stopped on purpose', () => {
		// The Jobs dashboard has a Cancel button, so this is a state a person can put a row into
		// themselves. Red would tell them their own click broke something.
		const { badge } = render({ state: 'canceled' });

		expect(badge.classList.contains('state-canceled')).toBe(true);
		expect(badge.classList.contains('state-failed')).toBe(false);
	});
});

describe('every state', () => {
	it.each(['queued', 'running', 'done', 'blocked', 'quarantined', 'canceled', 'failed'] as const)(
		'%s renders as itself',
		(state) => {
			// The whole set is here because two screens render it. A state that only one of them knows
			// about is the drift this component exists to prevent.
			const { badge } = render({ state });

			expect(badge.classList.contains(`state-${state}`)).toBe(true);
			expect(words(badge).length).toBeGreaterThan(0);
		}
	);
});

describe('the mark', () => {
	/*
	 * A glyph, not a dot, and the redundancy with the colour is the point.
	 *
	 * A dot would be five colours doing all of the work: the two AMBER states indistinguishable
	 * from each other except by their word, and anybody who does not separate those colours easily
	 * seeing a row of identical grey circles. Shape and colour say the same thing, so either one
	 * alone is enough.
	 */
	it.each([
		['queued', 'playlist_add_check'],
		['done', 'check_circle'],
		['blocked', 'do_not_disturb_on'],
		['quarantined', 'gpp_maybe'],
		['canceled', 'stop_circle'],
		['failed', 'cancel']
	] as const)('%s draws %s', (state, name) => {
		const { badge } = render({ state });

		expect(badge.querySelector('.icon')?.textContent).toBe(glyph(name));
	});

	it('quarantine is the shield with a QUESTION in it, not the plain one', () => {
		/* The Organize board's card for the same pile wears a plain `shield`, and that is right for
		   what it is: the PLACE these files are kept, which Sift is looking after. A plain shield
		   says "protected". This is the STATE of one file, and what that state means is that
		   something about it is unresolved, so it is the shield with a question in it.

		   Asserted as a difference rather than as a name, because the point is that the two marks are
		   not the same one. */
		const { badge } = render({ state: 'quarantined' });

		expect(badge.querySelector('.icon')?.textContent).toBe(glyph('gpp_maybe'));
		expect(badge.querySelector('.icon')?.textContent).not.toBe(glyph('shield'));
	});

	it('is a turning arc for running, because a static mark cannot say "right now"', () => {
		// The one state with no glyph. A still mark beside the words "In progress" reads the same whether
		// the queue is turning or wedged, which is the one thing that badge has to be able to say.
		const { badge } = render({ state: 'running' });

		expect(badge.querySelector('.icon')).toBeNull();
		expect(badge.querySelector('.spinner')).not.toBeNull();
	});

	it('can be said more precisely when the screen knows more, without moving the colour', () => {
		// The download queue's blocked state: only ever cookies, so it says so and draws the waiting
		// glyph. Amber either way: an override replaces the mark, never the meaning.
		const { badge } = render({ state: 'blocked', icon: 'chronic', iconFilled: true });

		expect(badge.querySelector('.icon')?.textContent).toBe(glyph('chronic'));
		expect(badge.classList.contains('state-blocked')).toBe(true);
	});
});
