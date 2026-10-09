/* The usernames on one Site, filed by the person behind each, for that Site's People tab. */
import { holdsSomething, usernamesBy, type Username } from '$lib/people/usernames.svelte';

interface UsernamesHere {
	/** The usernames that hold files or a number, by person: `UsernameLines`' own input. */
	held: Map<string, Username[]>;
	/** The usernames that hold nothing, by person. */
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
