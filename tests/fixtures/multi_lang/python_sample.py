import os
import sys

def memoize(func):
    """Decorator to cache results."""
    cache = {}
    def wrapper(*args):
        if args not in cache:
            cache[args] = func(*args)
        return cache[args]
    return wrapper

@memoize
def compute_factor(base: int, exp: int) -> int:
    # Compute base raised to exp
    f_str = f"computing_{base}_{exp}"
    return base ** exp

if __name__ == "__main__":
    result = compute_factor(2, 10)
    print(f"Result: {result}")
