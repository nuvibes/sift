/**
 * The corner panel's own keys, from a table like the player's (`$lib/shell/shortcuts`).
 *
 * The player inside the panel keeps out of the keyboard, so the panel answers for it. Only a clip
 * answers the transport: binding Space for a picture or a wall would take the press from whatever
 * would have used it.
 */

import { claim } from '$lib/player/keys';
import { mini } from '$lib/player/mini.svelte';
import { SKIP_SECONDS } from '$lib/player/skip';
import type { Actions, Asked, PlayerAction } from '$lib/shell/shortcuts';
import { muteEcho, playEcho, skipEcho, type Echo } from '$lib/theater/echoes';

/** The part of the player inside the panel the keys drive. */
interface PanelPlayer {
	togglePlayback(): void;
	isPaused(): boolean;
	toggleSound(): void;
	isMuted(): boolean;
	jumpBy(seconds: number): void;
}

/** What the table reads and drives in the panel. */
interface PanelKeys {
	/** Whether the panel holds a clip, the only thing with a transport. */
	isClip(): boolean;
	player(): PanelPlayer | null;
	/** The file the panel holds, or null. */
	assetId(): string | null;
	/** Whether the panel is docked as a strip above a phone's tabs. */
	docked(): boolean;
	save(id: string): void;
	/** Back to full size, filling the screen as it arrives when asked. */
	expand(fill?: boolean): void;
	/** Say in the corner what a key did. */
	echo(what: Echo): void;
	/** Wake the panel's chrome. */
	flash(): void;
}

/** The panel's table of keys. */
export function panelKeys(panel: PanelKeys): Actions<PlayerAction> {
	function jumpWith({ key }: Asked, by: number): boolean {
		if (!panel.isClip()) return false;
		if (key !== null) claim(key);
		panel.player()?.jumpBy(by);
		if (key !== null) panel.echo(skipEcho(by));
		panel.flash();
		return true;
	}

	return {
		'player.back': (asked) => jumpWith(asked, -SKIP_SECONDS),
		'player.forward': (asked) => jumpWith(asked, SKIP_SECONDS),
		'player.playPause': ({ key }) => {
			if (!panel.isClip()) return false;
			// Space is how a focused button is pressed, so it belongs to one when there is one.
			const on = key?.target as HTMLElement | null;
			if (on?.tagName === 'BUTTON' || on?.tagName === 'A') return false;
			if (key !== null) claim(key);
			const player = panel.player();
			player?.togglePlayback();
			if (key !== null && player) panel.echo(playEcho(player.isPaused()));
			panel.flash();
			return true;
		},
		'player.save': ({ key }) => {
			const id = panel.assetId();
			if (id === null) return false;
			// The browser's own Ctrl-S would save the application's shell. Taken before it can.
			if (key !== null) claim(key);
			panel.save(id);
			return true;
		},
		'player.corner': ({ key }) => {
			// Claimed, so the full-size player about to mount does not send the clip straight back.
			if (key !== null) claim(key);
			// One size up: the strip to the panel, the panel (and the docked strip, which has no
			// panel above it) to full size.
			if (mini.bar && !panel.docked()) mini.toPanel();
			else panel.expand();
			return true;
		},
		/* M on the way down only: the full-size player's hold-M-for-volume has no arrows to pair
		   with here, where the arrows step through the clip. */
		'player.mute': ({ key }) => {
			const player = panel.player();
			if (!panel.isClip() || !player) return false;
			if (key !== null) claim(key);
			if (!key?.repeat) player.toggleSound();
			if (key !== null && !key.repeat) panel.echo(muteEcho(player.isMuted()));
			panel.flash();
			return true;
		},
		/* A: the panel down to the strip, the strip back up to the panel, or to full size from the
		   docked strip, as the corner key does. */
		'player.audioOnly': ({ key }) => {
			if (!panel.isClip()) return false;
			if (key !== null) claim(key);
			if (!mini.bar && !panel.docked()) mini.toBar();
			else if (panel.docked()) panel.expand();
			else mini.toPanel();
			panel.flash();
			return true;
		},
		/* F: back to full size, filling the screen as it arrives; the panel has no stage to fill. */
		'player.fill': ({ key }) => {
			if (!panel.isClip()) return false;
			if (key !== null) claim(key);
			panel.expand(true);
			return true;
		}
	};
}
