"""Processo-alvo dos testes de codigo de saida (SIM-1.5G.2): escreve uma linha, marca o fim e sai com o codigo pedido.

Uso (so em `test_runner_exitcode.py`): `python -m sim.tests.exit_stub <codigo> [<arquivo-marcador>]`.
"""

import sys
from pathlib import Path

if __name__ == "__main__":
    print("stub-out")
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text("done", encoding="utf-8")                  # gravado ANTES de sair: prova a ordem
    sys.exit(int(sys.argv[1]))
