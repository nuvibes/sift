/*
 * A table of figures as text a spreadsheet reads: a line to a row, a tab between cells. What the
 * Stats view's Copy puts on the clipboard, from the same rows the table draws.
 */
export interface TableRow {
	label: string;
	cells: readonly string[];
}

/** Tabs and line breaks inside a cell would split it, so they become spaces. */
const flat = (cell: string) => cell.replace(/[\t\r\n]+/g, ' ');

export function tableText(
	heads: readonly string[],
	rows: readonly TableRow[],
	ranked = false
): string {
	const lines = [
		heads,
		...rows.map((row, index) => [...(ranked ? [String(index + 1)] : []), row.label, ...row.cells])
	];
	return lines.map((cells) => cells.map(flat).join('\t')).join('\n');
}
