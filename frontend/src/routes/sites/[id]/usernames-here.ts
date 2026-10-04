/*
 * The usernames on one Site, filed by the person behind each, for that Site's People tab.
 *
 * Two filings of one read. A Site's People wall counts people whose only presence on the Site is a
 * username with nothing filed under it (a stash-box's answer about somebody writes a username on
 * a Site and no file), so a reader that kept only the usernames that hold something
 * (`holdsSomething`) would leave nothing under such a card, and the card would read "0 files" with
 * no reason beside it. The first filing is the one `UsernameLines` draws; the second is the rest,
 * said as what they are. The third is the usernames that belong to nobody yet and hold something:
 * they have no person's card to stand under, so the tab draws each as a card of its own after
 * the people.
 */
import { holdsSomething, usernamesBy, type Username } from '$lib/people/usernames.svelte';

interface UsernamesHere {
	/** The usernames that hold files or a number, by person: `UsernameLines`' own input. */
	held: Map<string, Username[]>;
	/**
	 * The usernames that hold nothing, by person. A row with no person has no card to stand under,
	 * and a row with no name is the library's "poster unknown" (a filing's blank row), not a name
	 * anybody could read. Both are left out.
	 */
	bare: Map<string, Username[]>;
	/** The usernames with no person that hold files or a number, in the order they were read. */
	loose: Username[];
}

export function usernamesHere(all: readonly Username[]): UsernamesHere {
	const bare = new Map<string, Username[]>();
	const loose: Username[] = [];
	for (const one of all) {
		if (!one.username.trim()) continue;
		if (!one.person_id) {
			if (holdsSomething(one)) loose.push(one);
			continue;
		}
		if (holdsSomething(one)) continue;
		bare.set(one.person_id, [...(bare.get(one.person_id) ?? []), one]);
	}
	return { held: usernamesBy(all, 'person_id'), bare, loose };
}

/** What a bare username says under its person's card: the name here, and that nothing is. */
export function bareLine(one: Username): string {
	return `@${one.username.trim()}, no files here`;
}
