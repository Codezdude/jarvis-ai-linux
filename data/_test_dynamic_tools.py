"""Auto-synthesized dynamic tools managed by J.A.R.V.I.S."""


# Synthesized tool: compute_fibonacci
def compute_fibonacci(n: int) -> int:
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b
