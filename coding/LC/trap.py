"""
LeetCode 42. 接雨水

题目描述:
给定 n 个非负整数表示每个宽度为 1 的柱子的高度图，计算按此排列的柱子，下雨之后能接多少雨水。

链接: https://leetcode.cn/problems/trapping-rain-water/
"""

from typing import List


class Solution:
    # ========== 解法1: 双指针 (O(n) 时间, O(1) 空间) ==========
    def trap(self, height: List[int]) -> int:
        """
        双指针法：
        用 left, right 两个指针从两端向中间移动，
        同时维护 left_max 和 right_max。
        哪一边的 max 较小，就先处理哪一边，因为接水量由较短的一边决定。
        """
        if not height:
            return 0

        left, right = 0, len(height) - 1
        left_max, right_max = 0, 0
        water = 0

        while left < right:
            if height[left] < height[right]:
                # 左边较低，由 left_max 决定接水量
                if height[left] >= left_max:
                    left_max = height[left]
                else:
                    water += left_max - height[left]
                left += 1
            else:
                # 右边较低（或相等），由 right_max 决定接水量
                if height[right] >= right_max:
                    right_max = height[right]
                else:
                    water += right_max - height[right]
                right -= 1

        return water

    # ========== 解法2: 动态规划 (O(n) 时间, O(n) 空间) ==========
    def trap_dp(self, height: List[int]) -> int:
        """
        对于每个位置 i，接水量 = min(左边最高, 右边最高) - height[i]
        预先计算每个位置的 left_max 和 right_max 数组。
        """
        if not height:
            return 0

        n = len(height)
        left_max = [0] * n
        right_max = [0] * n

        left_max[0] = height[0]
        for i in range(1, n):
            left_max[i] = max(left_max[i - 1], height[i])

        right_max[n - 1] = height[n - 1]
        for i in range(n - 2, -1, -1):
            right_max[i] = max(right_max[i + 1], height[i])

        water = 0
        for i in range(n):
            water += min(left_max[i], right_max[i]) - height[i]

        return water

    # ========== 解法3: 单调栈 (O(n) 时间, O(n) 空间) ==========
    def trap_stack(self, height: List[int]) -> int:
        """
        单调递减栈：
        栈中保存柱子的下标，对应高度单调递减。
        当遇到更高的柱子时，栈顶元素形成一个凹槽，可以计算接水量。
        """
        stack = []  # 单调递减栈，存下标
        water = 0

        for i, h in enumerate(height):
            while stack and h > height[stack[-1]]:
                top = stack.pop()
                if not stack:
                    break
                # 凹槽的宽度
                distance = i - stack[-1] - 1
                # 凹槽的高度由左右两边较矮的决定
                bounded_height = min(height[stack[-1]], h) - height[top]
                water += distance * bounded_height
            stack.append(i)

        return water


# ==================== 测试 ====================
if __name__ == "__main__":
    sol = Solution()

    # 示例1: 经典接雨水图
    height1 = [0, 1, 0, 2, 1, 0, 1, 3, 2, 1, 2, 1]
    expected1 = 6

    # 示例2: 单调递增，无法接雨水
    height2 = [4, 2, 0, 3, 2, 5]
    expected2 = 9

    print("=" * 50)
    print("LeetCode 42. 接雨水")
    print("=" * 50)

    for idx, (height, expected) in enumerate([(height1, expected1), (height2, expected2)], 1):
        print(f"\n示例 {idx}: height = {height}")
        print(f"期望输出: {expected}")

        r1 = sol.trap(height)
        r2 = sol.trap_dp(height)
        r3 = sol.trap_stack(height)

        print(f"双指针结果: {r1} {'✅' if r1 == expected else '❌'}")
        print(f"动态规划结果: {r2} {'✅' if r2 == expected else '❌'}")
        print(f"单调栈结果: {r3} {'✅' if r3 == expected else '❌'}")

    print("\n" + "=" * 50)
    print("全部测试完成!")
