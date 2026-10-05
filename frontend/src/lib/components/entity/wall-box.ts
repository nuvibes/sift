/** What an entity wall's search box shows: "Search sites" where it fits, the noun alone where not. */
export function boxWords(plural: string, room: number, width: (text: string) => number): string {
	const full = `Search ${plural}`;
	if (room <= 0 || width(full) <= room) return full;
	return plural.charAt(0).toUpperCase() + plural.slice(1);
}
