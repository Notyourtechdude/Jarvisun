"""Deterministic checks. Never eval model-generated arithmetic."""
import ast
from decimal import Decimal, InvalidOperation, localcontext


def calculate(expression: str, unit: str = "") -> dict:
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 256:
        raise ValueError("expression must be 1..256 characters")
    if len(unit) > 32:
        raise ValueError("unit must be <=32 characters")
    try:
        tree = ast.parse(expression, mode="eval")
        if sum(1 for _ in ast.walk(tree)) > 80:
            raise ValueError("expression too complex")

        def evaluate(node, depth=0):
            if depth > 12:
                raise ValueError("expression too deep")
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                result = Decimal(ast.get_source_segment(expression, node).replace("_", ""))
            elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                result = evaluate(node.operand, depth + 1) * (-1 if isinstance(node.op, ast.USub) else 1)
            elif isinstance(node, ast.BinOp):
                left, right = evaluate(node.left, depth + 1), evaluate(node.right, depth + 1)
                if isinstance(node.op, ast.Add):
                    result = left + right
                elif isinstance(node.op, ast.Sub):
                    result = left - right
                elif isinstance(node.op, ast.Mult):
                    result = left * right
                elif isinstance(node.op, ast.Div):
                    result = left / right
                elif isinstance(node.op, ast.Mod):
                    result = left % right
                elif isinstance(node.op, ast.Pow) and right == int(right) and abs(right) <= 12:
                    result = left ** int(right)
                else:
                    raise ValueError("unsupported arithmetic operation")
            else:
                raise ValueError("only numbers and arithmetic operators are permitted")
            if (not result.is_finite() or abs(result) > Decimal("1e100")
                    or result.as_tuple().exponent < -100):
                raise ValueError("number exceeds calculation limit")
            return result
        with localcontext() as context:
            context.prec = 28
            value = evaluate(tree.body)
            rendered = format(value, "f")
            if "." in rendered:
                rendered = rendered.rstrip("0").rstrip(".")
            return {"value": rendered, "unit": unit, "expression": expression, "verified": True,
                    "precision_digits": 28}
    except (SyntaxError, ArithmeticError, InvalidOperation, RecursionError) as exc:
        raise ValueError("invalid or unbounded arithmetic expression") from exc


def rank_options(options: list[dict]):
    if not 1 <= len(options) <= 20:
        raise ValueError("provide 1..20 options")
    results = []
    for option in options:
        if not isinstance(option, dict):
            raise ValueError("each option must be an object")
        try:
            raw_values = {k: str(option.get(k, default)) for k, default in
                          (("benefit", 0), ("probability", 1), ("cost", 0), ("expected_loss", 0))}
            if any(len(value) > 64 for value in raw_values.values()):
                raise ValueError("numeric input exceeds size limit")
            values = {k: Decimal(v) for k, v in raw_values.items()}
        except InvalidOperation as exc:
            raise ValueError("numeric inputs must be finite decimal values") from exc
        if any(not value.is_finite() or value < 0 or value > Decimal("1e15") for value in values.values()):
            raise ValueError("inputs must be finite, nonnegative, and <=1e15")
        if any(value.as_tuple().exponent < -12 for value in values.values()):
            raise ValueError("inputs support at most twelve fractional decimal places")
        if values["probability"] > 1:
            raise ValueError("probability must be within [0,1]")
        net = values["benefit"] * values["probability"] - values["cost"] - values["expected_loss"]
        results.append({"name": str(option.get("name", "Option"))[:120], "net_value": format(net.normalize(), "f"),
                        "inputs": {k: str(v) for k, v in values.items()}, "estimated": True})
    return sorted(results, key=lambda row: Decimal(row["net_value"]), reverse=True)
