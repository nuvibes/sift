// SPDX-License-Identifier: AGPL-3.0-or-later
/* What somebody can find on their own profile. None of these is a preference: one renames the
 * account and the other two set a credential, so none of them is in the registry. */
import type { Searchable } from './search';

/** The three blocks of the pane, by the words on their headings and in a search result alike. */
export const USERNAME = {
	name: 'Username',
	help: 'The username you sign in with.',
	lede: "Your username is only a label. Changing it signs nobody out, changes nothing you can see, and leaves every saved Site's cookies working."
};
export const PASSWORD = {
	name: 'Password',
	help: 'Change the password you sign in with.',
	ledeAdmin:
		'Changing it signs you out anywhere else you are signed in and keeps you signed in here. Your saved Site cookies are kept.',
	ledeGuest: 'Changing it keeps you signed in here.'
};
export const PIN = {
	name: 'PIN',
	help: 'Create or change the PIN that unlocks Hidden.',
	ledeSet: 'A PIN is set. Entering a new one replaces it.',
	ledeUnset:
		'No PIN yet. You need one before you can hide anything, because the PIN is what unlocks Hidden.',
	after:
		'The PIN unlocks Sift only while you are signed in. After Sift restarts or you sign out, you need your password.'
};
/* Who is signed in, as the pane's first row: the name, and what that kind of user can do. */
export const WHO = {
	label: 'Signed in as',
	admin: 'An admin. You can change every setting and add guests.',
	guest: 'A guest. You see what has been shared with you.'
};
/* "Other devices" is only a fact for somebody who could have any: for a guest it names sessions
   they do not have and cannot see, which reads as a warning about something. */
export const SIGN_OUT = {
	name: 'Sign out',
	row: 'Sign out of this browser',
	help: "Signs this browser out. Anywhere else you're signed in isn't affected.",
	helpGuest: 'Signs you out on this browser.',
	action: 'Sign out'
};

export const SEARCHABLE: Searchable[] = [
	{
		...USERNAME,
		key: 'profile.name',
		section: 'profile',
		keywords: 'username rename change name login who am i your name'
	},
	{
		...PASSWORD,
		key: 'profile.password',
		section: 'profile',
		keywords: 'password credentials sign in security reset change'
	},
	{
		...PIN,
		key: 'profile.pin',
		section: 'profile',
		keywords: 'pin hidden vault private unhide unlock code digits passcode your pin'
	},
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: WHO.label,
		section: 'profile',
		keywords: 'signed in as who am i account admin guest'
	},
	{
		name: SIGN_OUT.name,
		section: 'profile',
		keywords: 'sign out log out logout leave'
	},
	{
		name: SIGN_OUT.row,
		key: 'profile.sign-out',
		section: 'profile',
		keywords: 'sign out this browser log out logout'
	}
];
