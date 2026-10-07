import os
import re
import sys

from sim.driver import cli as C
from sim.driver.http import UrllibTransport

day, at, pattern = int(sys.argv[1]), sys.argv[2], sys.argv[3]
real = UrllibTransport()


class CrashAfterProductProcessed:
    """Morte instantanea do PROCESSO (sem flush, sem excecao) DEPOIS de o produto processar a requisicao."""

    def request(self, method, url, headers, body, timeout):
        response = real.request(method, url, headers, body, timeout)
        if re.search(pattern, f"{method} {url}"):
            os._exit(137)
        return response


driver = C.build_driver(os.environ, transport=CrashAfterProductProcessed())
driver.run_slot(day, at)
print("no-crash")
