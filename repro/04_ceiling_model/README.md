# Working text-only ceiling

This directory reproduces the arithmetic for the paper's explicitly sensitivity-based
working ceiling. The model is

`P* = q*h + (1-q)*c`

with central illustration `q=.30`, `h=.93`, `c=.82`, yielding 85.3%. The stated
sensitivity ranges `q=.25-.33`, `h=.90-.95`, and `c=.79-.87` give corner values
81.75%-89.64%, reported in the paper as an approximate 82%-90% envelope.

`S85 = (p-.50)/(.85-.50)` is a descriptive normalization, not an information-theoretic
bound.
