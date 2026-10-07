"""
Concorrencia REAL (HG-2): threads reais disparam as chamadas atras de uma
`threading.Barrier`. Os instantes reais de envio/resposta entram na
evidencia. Resultado aceito: um unico efeito; os demais saem `duplicate` ou
409 (o Driver registra, o Evaluator julga).
"""

from __future__ import annotations

import threading
import time

from sim.driver.errors import HarnessError


def run_concurrently(tasks: list, clock=time.time) -> list:
    """`tasks` = callables sem argumentos. Devolve, na ordem, dicts
    {"result": ..., "t_start": float, "t_end": float}. Qualquer excecao e
    reempacotada em HarnessError DEPOIS de todas terminarem."""
    count = len(tasks)
    if count == 1:
        started = clock()
        result = tasks[0]()
        return [{"result": result, "t_start": started, "t_end": clock()}]
    barrier = threading.Barrier(count)
    outcomes: list = [None] * count
    errors: list = [None] * count

    def worker(index: int) -> None:
        try:
            barrier.wait(timeout=30)
            started = clock()
            result = tasks[index]()
            outcomes[index] = {"result": result, "t_start": started, "t_end": clock()}
        except BaseException as error:  # noqa: BLE001 - reempacotado abaixo
            errors[index] = error

    threads = [threading.Thread(target=worker, args=(index,), name=f"sim-driver-{index}") for index in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    failures = [error for error in errors if error is not None]
    if failures:
        first = failures[0]
        if isinstance(first, HarnessError):
            raise first
        raise HarnessError(f"falha em execucao concorrente: {type(first).__name__}: {first}") from first
    return outcomes
