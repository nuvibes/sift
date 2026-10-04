/* The shape of a folder tree, and the one operation worth having on its own.
 *
 * The rows are rendered flat rather than as components inside components. A tree drawn by nesting
 * has to move focus across that nesting to answer an arrow key, and every level is somewhere for
 * that to go wrong. Flattened, the arrow keys are a step through a list, and the structure is said
 * out loud by aria-level instead of by the shape of the markup, which is what a screen reader
 * reads anyway.
 */

export interface TreeNode {
	id: string;
	label: string;
	/** Shown right-aligned. Omit it when there is nothing to count. */
	count?: number;
	/** Somebody can reach this, or is kept from it. Drawn as the same mark the tiles carry. */
	shared?: boolean;
	restricted?: boolean;
	/** ...and whether that was decided on this row, or on a folder above it. */
	shared_here?: boolean;
	restricted_here?: boolean;
	/** In the vault. Only ever true with the vault open, which is the only time it is listed. */
	hidden?: boolean;
	refused?: 'local' | 'swap';
	children?: TreeNode[];
}

export interface FlatNode {
	node: TreeNode;
	/** 0 at the roots. Drives both the indent and aria-level, which is 1-based. */
	level: number;
	expanded: boolean;
	hasChildren: boolean;
}

/**
 * The rows that are actually on screen, in order, with the depth each one sits at.
 *
 * A collapsed node's children are not here at all: they are not rendered, so they are not rows, and
 * an arrow key must not walk onto something nobody can see.
 */
export function flattenTree(
	nodes: readonly TreeNode[],
	expandedIds: ReadonlySet<string>,
	level = 0
): FlatNode[] {
	const rows: FlatNode[] = [];

	for (const node of nodes) {
		const hasChildren = Boolean(node.children && node.children.length > 0);
		const expanded = hasChildren && expandedIds.has(node.id);

		rows.push({ node, level, expanded, hasChildren });

		if (expanded && node.children) {
			rows.push(...flattenTree(node.children, expandedIds, level + 1));
		}
	}

	return rows;
}
