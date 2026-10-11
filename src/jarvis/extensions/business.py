"""Deterministic CSV finances and expected net value; no inferred financial facts."""

import csv
from decimal import Decimal, InvalidOperation, localcontext

from .common import ExtensionError, regular_file


def number(value):
    try:
        if len(str(value)) > 64:
            raise ValueError
        result = Decimal(str(value))
        if (not result.is_finite() or not -12 <= result.as_tuple().exponent <= 15
                or result.copy_abs() > Decimal('1e15')):
            raise ValueError
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise ExtensionError('Expected a finite decimal within the supported range') from None


def summarize_csv(path, *, revenue_column='revenue', cost_column='cost', max_rows=10000):
    if not 1 <= max_rows <= 100000:
        raise ExtensionError('CSV row limit must be between 1 and 100000')
    revenue = cost = Decimal(0)
    rows = 0
    try:
        with regular_file(path, 10_000_000).open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames or any(reader.fieldnames.count(c) != 1
                                           for c in (revenue_column, cost_column)):
                raise ExtensionError('CSV needs unique revenue and cost columns')
            with localcontext() as context:
                context.prec = 50
                for row in reader:
                    rows += 1
                    if rows > max_rows or None in row:
                        raise ExtensionError('CSV exceeds its row limit or has inconsistent columns')
                    revenue += number(row[revenue_column])
                    cost += number(row[cost_column])
                profit = revenue - cost
                margin = profit / revenue if revenue else None
    except (OSError, UnicodeError, csv.Error):
        raise ExtensionError('CSV could not be read as bounded UTF-8 data') from None
    return {'rows': rows, 'revenue': str(revenue), 'cost': str(cost), 'profit': str(profit),
            'margin': str(margin) if margin is not None else None,
            'currency': 'unspecified; caller must use one currency per file'}


def score_returns(probability, benefit, cost, expected_loss, *, privacy_allowed=True, budget=None):
    if not isinstance(privacy_allowed, bool):
        raise ExtensionError('Privacy constraint must be a boolean')
    probability, benefit, cost, expected_loss = map(number, (probability, benefit, cost, expected_loss))
    if not Decimal(0) <= probability <= Decimal(1) or any(x < 0 for x in (benefit, cost, expected_loss)):
        raise ExtensionError('Probability must be in [0,1]; benefits and costs must be nonnegative')
    allowed = bool(privacy_allowed) and (budget is None or cost <= number(budget))
    with localcontext() as context:
        context.prec = 50
        score = probability * benefit - cost - expected_loss
    return {'allowed': allowed, 'net_value': str(score),
            'decision': 'eligible' if allowed and score > 0 else 'decline',
            'assumptions': 'Caller-supplied probability and values; no calibrated prediction'}


def score_returns_range(probability, benefit, cost, expected_loss):
    """Monotonic bounds for uncertain independent caller-supplied inputs."""
    ranges = []
    for pair in (probability, benefit, cost, expected_loss):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ExtensionError('Uncertain inputs require minimum and maximum pairs')
        low, high = map(number, pair)
        if low > high:
            raise ExtensionError('Minimum must not exceed maximum')
        ranges.append((low, high))
    p, b, c, loss = ranges
    minimum = score_returns(p[0], b[0], c[1], loss[1])['net_value']
    maximum = score_returns(p[1], b[1], c[0], loss[0])['net_value']
    return {'min_net_value': minimum, 'max_net_value': maximum, 'currency': 'caller_supplied_same_unit'}
