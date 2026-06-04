"""
LeetCode 114. 二叉树展开为链表

题目描述:
给你二叉树的根结点 root ，请你将它展开为一个单链表：
- 展开后的单链表应该与二叉树 先序遍历 顺序相同。
- 单链表中的每个节点使用 TreeNode 的 right 指针作为链表中的 next 指针。
- 单链表中的每个节点的左指针必须为 null 。

链接: https://leetcode.cn/problems/flatten-binary-tree-to-linked-list/
"""

from typing import Optional


class TreeNode:
    """二叉树节点定义"""
    def __init__(self, val=0, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right

    def __repr__(self):
        return f"TreeNode({self.val})"


class Solution:
    # ========== 解法1: 先序遍历 + 数组保存节点 (O(n) 时间, O(n) 空间) ==========
    def flatten_preorder_list(self, root: Optional[TreeNode]) -> None:
        """
        先序遍历将所有节点保存到列表，然后依次连接。
        直观易懂，但需要额外 O(n) 空间。
        """
        if not root:
            return

        nodes = []

        def preorder(node: Optional[TreeNode]):
            if not node:
                return
            nodes.append(node)
            preorder(node.left)
            preorder(node.right)

        preorder(root)

        for i in range(len(nodes) - 1):
            nodes[i].left = None
            nodes[i].right = nodes[i + 1]
        nodes[-1].left = None
        nodes[-1].right = None

    # ========== 解法2: 原地递归展开 (O(n) 时间, O(h) 空间) ==========
    def flatten_recursive(self, root: Optional[TreeNode]) -> None:
        """
        递归思路：
        1. 将左子树展开为链表
        2. 将右子树展开为链表
        3. 把展开后的左子树插入到 root 和右子树之间
        """
        if not root:
            return

        self.flatten_recursive(root.left)
        self.flatten_recursive(root.right)

        left = root.left
        right = root.right

        # 左子树置空，右子树接上原来的左子树
        root.left = None
        root.right = left

        # 找到当前链表的末尾，接上原来的右子树
        cur = root
        while cur.right:
            cur = cur.right
        cur.right = right

    # ========== 解法3: Morris 遍历 (O(n) 时间, O(1) 空间) ==========
    def flatten(self, root: Optional[TreeNode]) -> None:
        """
        Morris 遍历实现 O(1) 空间复杂度：
        对于当前节点，如果左子树不为空：
        - 找到左子树的最右节点（前驱）
        - 将当前节点的右子树挂到前驱的右指针上
        - 将左子树移到右子树的位置，左子树置空
        - 继续处理下一个右节点
        """
        cur = root
        while cur:
            if cur.left:
                # 找左子树的最右节点
                predecessor = cur.left
                while predecessor.right:
                    predecessor = predecessor.right

                # 将当前右子树接到前驱的右边
                predecessor.right = cur.right

                # 左子树移到右边
                cur.right = cur.left
                cur.left = None

            cur = cur.right


# ==================== 辅助函数 ====================
def build_tree(values):
    """从层序遍历列表构建二叉树 (None 表示空节点)"""
    if not values or values[0] is None:
        return None

    root = TreeNode(values[0])
    queue = [root]
    i = 1
    while queue and i < len(values):
        node = queue.pop(0)
        if i < len(values) and values[i] is not None:
            node.left = TreeNode(values[i])
            queue.append(node.left)
        i += 1
        if i < len(values) and values[i] is not None:
            node.right = TreeNode(values[i])
            queue.append(node.right)
        i += 1
    return root


def linked_list_to_list(root: Optional[TreeNode]):
    """将展开后的链表转换为 Python 列表，方便验证"""
    result = []
    cur = root
    while cur:
        result.append(cur.val)
        if cur.left is not None:
            raise ValueError("链表节点的左指针必须为 None")
        cur = cur.right
    return result


# ==================== 测试 ====================
if __name__ == "__main__":
    sol = Solution()

    # 示例1: [1,2,5,3,4,null,6]
    # 先序遍历: 1 -> 2 -> 3 -> 4 -> 5 -> 6
    root1 = build_tree([1, 2, 5, 3, 4, None, 6])
    sol.flatten(root1)
    result1 = linked_list_to_list(root1)
    print(f"示例1结果: {result1}")  # [1, 2, 3, 4, 5, 6]

    # 示例2: []
    root2 = build_tree([])
    sol.flatten(root2)
    result2 = linked_list_to_list(root2)
    print(f"示例2结果: {result2}")  # []

    # 示例3: [0]
    root3 = build_tree([0])
    sol.flatten(root3)
    result3 = linked_list_to_list(root3)
    print(f"示例3结果: {result3}")  # [0]
