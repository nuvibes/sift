// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * THE THREE TABS OF TASKS AND ACTIVITY, and which one an address opens.
 *
 * Tasks (when each task runs, and under it Activity, the queue), App History (what happened) and
 * Logs (what the program wrote down) answer "what does Sift do, and when", read from different
 * stores, so tabs side by side rather than one merged list.
 *
 * ## An address, not only a press
 *
 * A tab is carried in the address as `?show=`, the settings frame's one shape for a tab
 * (`settingsPath` in `settings-ui/sections.ts`, the entity pages' `?show=` before it): Tasks is
 * `/settings/tasks`, App History `?show=history`, Logs `?show=log`; `?show=now`, Activity's old
 * address, opens Tasks, where Activity is now.
 *
 * A KEY lands too. Every row drawn on a tab is OWNED by that tab through the frame's mechanism for
 * "this key lives somewhere not yet on screen" (`drilldown.own`): a deep link asks the key's owner
 * to open its place before it hunts for the row. So `#download.verbose` opens the Log tab and
 * `#activity.saved` opens History narrowed to saves, whatever `?show=` says or does not say.
 *
 * One table, read by the screen and by its test, so the address a tab writes and the address that
 * opens it cannot come to disagree.
 */
import { settingsPath } from '$lib/settings-ui/sections';

export type ActivityTab = 'tasks' | 'history' | 'log';

const TABS: readonly ActivityTab[] = ['tasks', 'history', 'log'];

/** Where a door lands: the tab, and for History the act it opens filtered to, or the decisions. */
interface Landing {
	tab: ActivityTab;
	verb?: string;
	decisions?: boolean;
}

/** The keys drawn on a tab other than Tasks, which takes every key not listed here. */
export const OWNED: Readonly<Record<string, Landing>> = {
	'activity.history': { tab: 'history' },
	/* The two lists History absorbed, as the filters they became. */
	'activity.saved': { tab: 'history', verb: 'saved' },
	'activity.runs': { tab: 'history', verb: 'ran' },
	/* What was decided on Organize and what Sift filed by itself: Organize's Decisions lands here. */
	'activity.decisions': { tab: 'history', decisions: true },
	/* The key an older address of "How long tasks take" carries. An address never stops working. */
	'performance.scan_history': { tab: 'history', verb: 'ran' },
	'activity.log': { tab: 'log' },
	/* The settings the Log tab draws. */
	'logs.detail': { tab: 'log' },
	'logs.keep_mb': { tab: 'log' },
	'logs.hide_personal': { tab: 'log' },
	'download.verbose': { tab: 'log' }
};

/**
 * The tab an address opens: the row's own tab first, since a row is drawn on exactly one, and a
 * row no other tab owns is a task's on Tasks; then `?show=`; then Tasks, the first tab.
 */
export function landingFor(search: string, hash: string): Landing {
	const key = decodeURIComponent(hash.replace(/^#/, ''));
	if (key !== '') return OWNED[key] ?? { tab: 'tasks' };
	const show = new URLSearchParams(search).get('show');
	return { tab: TABS.includes(show as ActivityTab) ? (show as ActivityTab) : 'tasks' };
}

/** A tab's own address, for the bar: what a copied link or a refresh opens again. */
export function addressOf(tab: ActivityTab): string {
	return settingsPath({ section: 'tasks', show: tab === 'tasks' ? undefined : tab });
}
