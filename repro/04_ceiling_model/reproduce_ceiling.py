#!/usr/bin/env python3
from itertools import product

def ceiling(q, h, c):
    return q*h + (1-q)*c

q0, h0, c0 = 0.30, 0.93, 0.82
center = ceiling(q0, h0, c0)

q_range = (0.25, 0.33)
h_range = (0.90, 0.95)
c_range = (0.79, 0.87)
corners = [
    ceiling(q, h, c)
    for q, h, c in product(q_range, h_range, c_range)
]

print("WORKING TEXT-ONLY CEILING")
print(f"central assumptions: q={q0:.2f}, h={h0:.2f}, c={c0:.2f}")
print(f"P* = q*h + (1-q)*c = {100*center:.2f}%")
print(
    "corner sensitivity range = "
    f"{100*min(corners):.2f}% to {100*max(corners):.2f}% "
    "(reported approximately as 82-90%)"
)
print()
for p in [0.797, 0.767, 0.708, 0.647, 0.635, 0.613, 0.730]:
    s85 = (p - 0.50) / (0.85 - 0.50)
    print(f"p={100*p:.1f}% -> S85={s85:.3f}")

assert round(100*center, 1) == 85.3
assert round(100*min(corners)) == 82
assert round(100*max(corners)) == 90
