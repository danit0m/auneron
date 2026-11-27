"""One-off: extract INSTRUCTIONS from the frozen shadow module copy (AST only,
nothing is imported or executed) and print a repr to embed in frozen.py."""
import ast
import sys

src = open(sys.argv[1], encoding="utf-8").read()
tree = ast.parse(src)
for node in tree.body:
    if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "INSTRUCTIONS":
        value = ast.literal_eval(node.value)
        print(repr(value))
        break
