/* The launch lock lands before a screen is drawn. A screen asks for its list as it mounts, and the
   framed shell is the only branch that draws one for somebody signed in, so it waits for
   `launched`, which turns true once `lockOnLaunch` has settled. Read from the source, because
   mounting the root layout is mounting the whole application. */
import { expect, it } from 'vitest';

import source from './+layout.svelte?raw';

const script = source.slice(0, source.indexOf('</script>'));

it('draws the framed shell only once the launch lock has settled', () => {
	const from = script.indexOf('const framed = $derived(');
	const framed = script.slice(from, script.indexOf(');', from));
	expect(framed).toMatch(/&&\s*launched\s*$/);
	expect(script).toMatch(
		/lockOnLaunch\(\(\) => vault\.lock\(\)\)\s*\.catch\(\(\) => undefined\)\s*\.finally\(\(\) => \(launched = true\)\)/
	);
});

it('draws a screen for somebody signed in nowhere but the framed shell', () => {
	const markup = source.slice(source.indexOf('{#if onShellScreen}'));
	const renders = (text: string) => text.split('{@render children()}').length - 1;
	const before = markup.slice(0, markup.indexOf('{:else if framed}'));
	// Before the framed shell: the screen before the server (`/connect`) and the sign-in screens.
	expect(renders(before)).toBe(2);
	expect(before).toMatch(/\{#if onShellScreen\}[\s\S]*\{:else if ready && onAuthScreen\}/);
	// Inside it: a full-bleed screen and every other one.
	expect(renders(markup)).toBe(4);
});

it('asks nothing of Hidden on a session the server reports locked', () => {
	const from = script.indexOf('let launching = false;');
	const effect = script.slice(from, script.indexOf('lockOnLaunch(', from));
	// The lock screen is a fresh page load on a locked session: its preferences read is refused,
	// the strict defaults lock, and the vault request would be refused in turn.
	expect(effect).toMatch(
		/if \(session\.viewer\?\.locked === true\) \{\s*vault\.sessionLocked\(\);\s*return;\s*\}\s*launching = true;/
	);
});

it('holds the live connection while the session is locked', () => {
	const at = script.indexOf('live.start();');
	const effect = script.slice(script.lastIndexOf('$effect(', at), at);
	expect(effect).toMatch(
		/if \(!session\.isSignedIn \|\| session\.viewer\?\.locked === true\) return;/
	);
});

it("asks a locked session for nothing of the account's: preferences, the queue, the rail or the bells", () => {
	expect(script).toMatch(
		/const locked = session\.viewer\?\.locked === true;\s*untrack\(\(\) => \{\s*if \(onShellScreen \|\| !known \|\| locked \|\| who === readFor\) return;/
	);
	const queue = script.indexOf('imports.forgetRefusal();');
	const effect = script.slice(script.lastIndexOf('$effect(', queue), script.indexOf('});', queue));
	expect(effect).toMatch(/if \(!session\.adminUnlocked \|\| account === undefined\) return;/);
	expect(effect).toMatch(/return \(\) => imports\.stop\(\);/);
	expect(script).toMatch(
		/if \(session\.viewer\?\.locked === true\) return;\s*void rail\.hydrate\(account\);/
	);
	for (const bell of ['jobChanges', 'downloadChanges', 'settingChanges']) {
		const at = script.indexOf(`whenChanged(${bell}`);
		expect(script.slice(at, script.indexOf('});', at))).toMatch(/session\.adminUnlocked/);
	}
	expect(script).not.toMatch(/whenChanged\([a-zA-Z]+, \(\) => \{\s*if \(!?session\.isAdmin\)/);
});
