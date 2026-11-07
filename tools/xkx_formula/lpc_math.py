"""LPC / C 整数语义辅助。

LPC 的 `/` 对整数是“向零截断”，Python `//` 是向下取整；两者在负数上不等价。
本模块只提供纯函数，不读全局、不写状态。
"""
from __future__ import annotations


def lpc_div(a: int, b: int) -> int:
    """复刻 LPC 整数除法：结果向零截断。

    >>> lpc_div(7, 2)
    3
    >>> lpc_div(-7, 2)
    -3
    """
    if b == 0:
        raise ZeroDivisionError("lpc_div: division by zero")
    quotient = abs(a) // abs(b)
    if (a >= 0) == (b >= 0):
        return quotient
    return -quotient
