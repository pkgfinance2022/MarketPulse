"""
Shared scoring helpers.

`linear_score` turns a raw metric (e.g. ROE = 18.4%) into a 0-100 score
by interpolating between hand-picked breakpoints, instead of using hard
cliffs. This avoids two companies with ROE of 14.9% and 15.1% landing on
opposite sides of a bucket boundary.
"""


def linear_score(value, points):
    """
    points: list of (metric_value, score) tuples, sorted ascending by
    metric_value. Values outside the range are clamped to the nearest
    endpoint score.
    """

    if value is None:
        return None

    if value <= points[0][0]:
        return points[0][1]

    if value >= points[-1][0]:
        return points[-1][1]

    for (x0, s0), (x1, s1) in zip(points, points[1:]):

        if x0 <= value <= x1:

            if x1 == x0:
                return s0

            fraction = (value - x0) / (x1 - x0)

            return s0 + fraction * (s1 - s0)

    return points[-1][1]


def average(scores, default=50):

    values = [s for s in scores if s is not None]

    if not values:
        return default

    return round(sum(values) / len(values))
