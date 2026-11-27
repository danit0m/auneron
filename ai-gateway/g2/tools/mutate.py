"""Mutacao manual: semeia defeitos em copias do pacote e verifica se a suite os mata.

Uso: python tools/mutate.py [nome ...]   (sem nomes = todos). Resultado em mutation_results.json.
O codigo-fonte real nunca e alterado: cada mutante vive numa copia temporaria (AUNERON_GW_SRC).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
PY = Path(os.environ.get("AUNERON_GW_PY") or ROOT / ".venv" / "Scripts" / "python.exe")
FAST = ["tests/test_boundary.py", "tests/test_units.py", "tests/test_mem_mtls.py"]  # sem porta/processo
NET = ["tests/test_boundary.py", "tests/test_startup.py", "-k", "exclusive or no_listen or loopback_only"]

S = "server.py"
R3 = ["tests/test_r3.py"]  # os pontos novos do G2 r3 sao mortos pelos testes novos
M = {
    # --- identidade / TLS (mTLS antes de qualquer parsing)
    "tls_cert_optional": ("CERT_REQUIRED -> CERT_OPTIONAL", [("startup.py", "ctx.verify_mode = ssl.CERT_REQUIRED\n    ctx.verify_flags", "ctx.verify_mode = ssl.CERT_OPTIONAL\n    ctx.verify_flags")], None),
    "tls_min_12": ("TLS minimo 1.2 por padrao", [("startup.py", 'ssl.TLSVersion.TLSv1_3 if cfg.min_tls == "1.3" else ssl.TLSVersion.TLSv1_2', "ssl.TLSVersion.TLSv1_2")], None),
    "identity_gate_skipped": ("pula a porta de identidade", [(S, '        verdict = self._identity(transport)\n        if verdict != "ok":', '        verdict = "ok"\n        if verdict != "ok":')], None),
    "super_called_before_check": ("chama super().connection_made antes de conferir", [(S, "        verdict = self._identity(transport)\n", "        super().connection_made(transport)\n        verdict = self._identity(transport)\n")], None),
    "refusal_falls_through": ("recusa nao retorna (segue para super)", [(S, "            transport.abort()  # sem super(): nenhum estado HTTP e criado, nenhum byte e lido\n            return", "            transport.abort()\n            pass")], None),
    "data_received_unguarded": ("data_received nao ignora nao autenticado", [(S, "        if not self._authenticated:\n            return  # nada e interpretado\n", "        pass\n")], None),
    "fingerprint_compare_true": ("comparacao de impressao sempre verdadeira", [("identity.py", "ok |= hmac.compare_digest(fp, candidate)", "ok |= True")], None),
    "fingerprint_check_removed": ("remove checagem da impressao", [("identity.py", '    if not fingerprint_allowed(der, allowed):\n        return "client_cert_not_allowed"\n', "    pass\n")], None),
    "missing_cert_accepted": ("aceita conexao sem certificado", [("identity.py", '    if not der:\n        return "client_cert_missing"', '    if False:\n        return "client_cert_missing"')], None),
    "identity_exception_open": ("excecao na leitura do certificado = aceito", [("identity.py", '    except Exception:\n        return "client_cert_missing"', '    except Exception:\n        return "ok"')], None),
    "eof_unauth_kept": ("eof de nao autenticado nao fecha", [(S, "        if not self._authenticated:\n            return False\n        return super().eof_received()", "        return super().eof_received()")], None),
    # --- host/porta
    "accept_0_0_0_0": ("aceita 0.0.0.0 como host", [("config.py", "return host == LOOPBACK", 'return host in (LOOPBACK, "0.0.0.0")')], None),
    "bind_all_interfaces": ("bind em todas as interfaces (sem rede)", [("netbind.py", "sock.bind((host, port))", 'sock.bind(("", port))')], NET),
    "port_reuse": ("reuso de porta (SO_REUSEADDR em vez de exclusiva)", [("netbind.py", "sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)", "sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)")], NET),
    "port_reuse_behavior": ("reuso de porta, provado por comportamento (2o bind)", [("netbind.py", "sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)", "sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)")], ["tests/test_startup.py", "-k", "second_bind_fails"]),
    "exclusive_option_missing": ("sem opcao de porta exclusiva", [("netbind.py", "if hasattr(socket, \"SO_EXCLUSIVEADDRUSE\"):", "if False:")], NET),
    # --- partida
    "key_acl_skipped": ("pula permissao da chave", [("startup.py", "        key_check(cfg.key_file)\n    except", "        pass\n    except")], None),
    "acl_foreign_sid_allowed": ("ACL: aceita SID estranho", [("fsperm.py", "if sid not in allowed_sids and sid not in ALWAYS_OK_SIDS:", "if False:")], None),
    "posix_group_bits_ignored": ("POSIX: ignora bits de grupo/outros", [("fsperm.py", "if mode & (stat.S_IRWXG | stat.S_IRWXO):", "if False:")], None),
    "cert_expiry_skipped": ("pula validade do certificado", [("startup.py", "            check_currently_valid(path, now)", "            pass")], None),
    "cert_notafter_ignored": ("ignora vencimento (notAfter)", [("certinfo.py", "if now > na:", "if False:")], None),
    "ipv6_candidates_skipped": ("pula enderecos IPv6 locais", [("exposure.py", "for info in socket.getaddrinfo(name, None, socket.AF_INET6):", "for info in []:")], None),
    "linklocal_scope_skipped": ("link-local sem escopo por interface", [("exposure.py", 'out += [f"{a}%{i}" for i in indexes] or [a]', "out.append(a)")], None),
    "table_ipv6_ignored": ("tabela de escuta ignora IPv6", [("exposure.py", 'if classify(a) != "loopback":\n                raise', 'if classify(a) != "loopback" and ":" not in a:\n                raise')], None),
    "wildcard_treated_loopback": ("curinga tratado como loopback", [("exposure.py", 'if ip.is_unspecified:\n        return "wildcard"', 'if ip.is_unspecified:\n        return "loopback"')], None),
    "exposure_check_off": ("conferencia de exposicao do Ollama desligada", [("exposure.py", 'for a in mine:\n            if classify(a) != "loopback":\n                raise ExposureError("ollama escuta fora de loopback")', "for a in mine:\n            pass")], None),
    "unverifiable_always_allowed": ("nao verificavel sempre permitido", [("exposure.py", 'if on_unverifiable == "allow":', "if True:")], None),
    # --- protocolo, prazos, corpo
    "header_timer_off": ("sem prazo de cabecalho", [(S, "            self._arm(self.header_timeout)  # relogio do cabecalho comeca no 1o byte", "            pass")], None),
    "idle_timer_off": ("sem prazo ocioso inicial", [(S, "        self._arm(self.idle_timeout)\n\n    def connection_lost", "        pass\n\n    def connection_lost")], None),
    "connection_limit_1000": ("limite de conexoes 4 -> 1000", [(S, "if self._active_conns >= self.cfg.max_connections:", "if self._active_conns >= 1000:")], None),
    "proxy_headers_on": ("liga proxy_headers", [(S, "proxy_headers=False,", "proxy_headers=True,")], None),
    "date_header_on": ("liga date_header", [(S, "date_header=False,", "date_header=True,")], None),
    "access_log_on": ("liga access_log", [(S, "access_log=False,", "access_log=True,")], None),
    "no_body_limit": ("sem limite de corpo", [("contract.py", "MAX_BODY_BYTES = 32 * 1024", "MAX_BODY_BYTES = 10**9")], None),
    "stream_limit_removed": ("sem limite durante a leitura em stream", [("app.py", "if len(body) > MAX_BODY_BYTES:  # limite real, durante a leitura", "if False:")], None),
    "cl_te_both_accepted": ("aceita CL+TE / CL duplicado", [("app.py", "if (cl is not None and te is not None) or (cl is not None and len(cl) != 1):", "if False:")], None),
    "content_type_unchecked": ("nao confere Content-Type", [("app.py", 'if ctype != "application/json" or len(headers.get("content-type", [])) != 1:', "if False:")], None),
    "docs_route_added": ("expoe /docs", [("app.py", 'ROUTES: dict[str, str] = {"/v1/generate": "POST", "/v1/health": "GET"}', 'ROUTES: dict[str, str] = {"/v1/generate": "POST", "/v1/health": "GET", "/docs": "GET"}')], None),
    "path_decoded": ("rota usa path decodificado", [("app.py", 'raw = scope.get("raw_path", b"")', 'raw = scope.get("path", "").encode()')], None),
    "query_ignored": ("query string ignorada", [("app.py", 'if not scope.get("query_string"):', "if True:")], None),
    # --- contrato
    "dup_keys_allowed": ("aceita chaves duplicadas", [("contract.py", 'raise JsonError("chave duplicada")', "pass")], None),
    "nan_allowed": ("aceita NaN", [("contract.py", 'raise JsonError("constante nao permitida")', "return 0")], None),
    "invalid_unicode_allowed": ("aceita surrogate solto", [("contract.py", "    _check_strings(value)\n    return value", "    return value")], None),
    "json_depth_unlimited": ("sem limite de aninhamento", [("contract.py", "MAX_JSON_DEPTH = 32", "MAX_JSON_DEPTH = 10**6")], None),
    "extra_fields_allowed": ("extra=forbid -> ignore", [("contract.py", 'extra="forbid"', 'extra="ignore"')], None),
    "classification_any": ("aceita qualquer data_classification", [("contract.py", 'data_classification: Literal["synthetic"]', "data_classification: StrictStr")], None),
    "request_id_loose": ("request_id sem tamanho minimo", [("contract.py", "{7,63}$", "{0,63}$")], None),
    # --- limites
    "concurrency_2": ("2 simultaneos em vez de 1", [("app.py", "self._lock = asyncio.Lock()", "self._lock = asyncio.Semaphore(2)")], None),
    "queue_off_by_one": ("fila de 3", [("app.py", "if self._inflight >= 1 + cfg.queue_size:", "if self._inflight >= 2 + cfg.queue_size:")], None),
    "rate_limit_loose": ("limite de taxa x100", [("app.py", "RateLimiter(cfg.per_minute)", "RateLimiter(cfg.per_minute * 100)")], None),
    "no_cancel_on_disconnect": ("nao cancela quando o cliente desconecta", [("app.py", '        while True:\n            msg = await receive()\n            if msg["type"] == "http.disconnect":\n                return\n\n    def _log_request', '        await asyncio.Event().wait()\n\n    def _log_request')], None),
    "deadline_ignored": ("prazo total ignorado", [("app.py", "timeout=remaining,", "timeout=None,")], None),
    # --- Ollama
    "skip_digest": ("pula conferencia do digest", [("ollama.py", "if digest != profile.digest:", "if False:")], None),
    "no_post_digest_check": ("nao confere digest depois da geracao", [("ollama.py", "        await self.verify_model(profile)\n        try:\n            out", "        try:\n            out")], None),
    "model_answer_unchecked": ("nao confere modelo respondido", [("ollama.py", 'if resp.get("model") != profile.model_id:', "if False:")], None),
    "done_reason_length_ok": ("done_reason=length aceito", [("ollama.py", 'if reason == "length":', "if False:")], None),
    "output_schema_not_validated": ("saida nao validada contra o esquema", [("ollama.py", "validate_instance(output_schema, out)", "pass")], None),
    "output_size_unchecked": ("sem limite de 64 KiB na saida", [("ollama.py", "> min(MAX_OUTPUT_BYTES, profile.max_output_bytes):", "> 10**9:")], None),
    "ollama_allowlist_removed": ("remove lista de rotas do Ollama", [("ollama.py", "if (method, path) not in ALLOWED_CALLS:", "if False:")], None),
    "ollama_peer_unchecked": ("nao confere peer loopback do Ollama", [("ollama.py", "assert_loopback_peer(str(peer[0]))", "pass")], None),
    "ollama_status_unchecked": ("nao confere status HTTP do Ollama", [("ollama.py", "if resp.status != 200:  # inclui 3xx: nunca segue redirect", "if False:")], None),
    "ollama_response_unbounded": ("resposta do Ollama sem limite de leitura", [("ollama.py", "data = resp.read(max_bytes + 1)  # limite real de bytes lidos", "data = resp.read()")], None),
    "ollama_no_real_cancel": ("cancelamento nao fecha o socket do Ollama", [("ollama.py", "            cancel.cancel()  # cancelamento real: fecha o socket com o Ollama\n            raise", "            raise")], None),
    "stream_true": ("stream=True no corpo do Ollama", [("ollama.py", '"stream": False,', '"stream": True,')], None),
    # --- G2 r3: permissao de escrita do certificado/CA e reverificacao periodica do Ollama
    "r3_public_check_skipped": ("pula a permissao do certificado e da CA", [("startup.py", "            public_check(path)  # leitura publica ok; escrita por outros recusada", "            pass")], R3),
    "r3_ca_not_checked": ("so confere o certificado, nao a CA", [("startup.py", '("certificado do servidor", cfg.cert_file), ("CA de clientes", cfg.ca_file)):', '("certificado do servidor", cfg.cert_file),):')], R3),
    "r3_cert_not_checked": ("so confere a CA, nao o certificado", [("startup.py", '("certificado do servidor", cfg.cert_file), ("CA de clientes", cfg.ca_file)):', '("CA de clientes", cfg.ca_file),):')], R3),
    "r3_posix_group_write_ok": ("POSIX publico: escrita por grupo aceita", [("fsperm.py", "if mode & (stat.S_IWGRP | stat.S_IWOTH):", "if mode & stat.S_IWOTH:")], R3),
    "r3_posix_other_write_ok": ("POSIX publico: escrita por outros aceita", [("fsperm.py", "if mode & (stat.S_IWGRP | stat.S_IWOTH):", "if mode & stat.S_IWGRP:")], R3),
    "r3_posix_public_read_refused": ("POSIX publico: leitura por outros recusada", [("fsperm.py", "if mode & (stat.S_IWGRP | stat.S_IWOTH):", "if mode & (stat.S_IRWXG | stat.S_IRWXO):")], R3),
    "r3_win_write_filter_off": ("Windows publico: leitura por outros recusada", [("fsperm.py", "if write_only and mask is not None and not (mask & WRITE_MASK):", "if False:")], R3),
    "r3_win_write_only_off": ("Windows: arquivo publico conferido como chave", [("fsperm.py", "bad = windows_violations(aces, allowed, write_only=public)", "bad = windows_violations(aces, allowed)")], R3),
    "r3_win_delete_bit_dropped": ("Windows: DELETE nao conta como escrita", [("fsperm.py", "0x100 | 0x10000 | 0x40000", "0x100 | 0x40000")], R3),
    "r3_win_generic_write_dropped": ("Windows: GENERIC_WRITE nao conta como escrita", [("fsperm.py", "0x10000000 | 0x40000000", "0x10000000")], R3),
    "r3_win_no_mask_open": ("Windows: ACE sem mascara aceita", [("fsperm.py", "if write_only and mask is not None and not (mask & WRITE_MASK):", "if write_only and not ((mask or 0) & WRITE_MASK):")], R3),
    "r3_public_unverifiable_open": ("verificacao impossivel = aceito", [("fsperm.py", '        raise KeyPermissionError("permissao nao verificavel") from None', "        return")], R3),
    "r3_recheck_min_removed": ("piso de 10 s removido", [("config.py", "MIN_RECHECK = 10.0", "MIN_RECHECK = 0.0")], R3),
    "r3_recheck_max_removed": ("teto de 3600 s removido", [("config.py", "MAX_RECHECK = 3600.0", "MAX_RECHECK = 1e12")], R3),
    "r3_recheck_default_600": ("padrao 600 s em vez de 60 s", [("config.py", "DEFAULT_RECHECK = 60.0", "DEFAULT_RECHECK = 600.0")], R3),
    "r3_recheck_config_ignored": ("intervalo do TOML ignorado", [("exposure_watch.py", "self.interval = cfg.exposure_recheck_seconds if interval is None else interval", "self.interval = 60.0 if interval is None else interval")], R3),
    "r3_refusal_off": ("modo recusa nunca recusa", [("app.py", "if self.guard is not None and self.guard.refusing:", "if False:")], R3),
    "r3_refusal_health_open": ("modo recusa deixa /v1/health chamar o Ollama", [("app.py", "if self.guard is not None and self.guard.refusing:", 'if self.guard is not None and self.guard.refusing and route != "/v1/health":')], R3),
    "r3_no_recovery": ("nao volta ao normal sozinho", [("exposure_watch.py", "                self.refusing = False\n", "                pass\n")], R3),
    "r3_exposed_seen_as_ok": ("exposicao detectada e ignorada", [("exposure_watch.py", 'return REASON_UNVERIFIABLE if "nao verificavel" in str(exc) else REASON_EXPOSED', "return None")], R3),
    "r3_exception_seen_as_ok": ("excecao do verificador = ok", [("exposure_watch.py", "        except Exception:\n            return REASON_UNVERIFIABLE", "        except Exception:\n            return None")], R3),
    "r3_reason_not_logged": ("motivo da recusa nao registrado", [("exposure_watch.py", "        if not self.refusing or reason != self.reason:\n            self.log.event(\"internal\", code=reason)", "        if False:\n            self.log.event(\"internal\", code=reason)")], R3),
    "r3_interval_zero": ("verifica sem esperar o intervalo", [("exposure_watch.py", "await asyncio.sleep(self.interval)", "await asyncio.sleep(0.001)")], R3),
    "r3_wrong_ollama_port": ("verifica a porta do Gateway, nao a do Ollama", [("exposure_watch.py", "check_ollama_not_exposed(cfg.ollama_port, on_unverifiable", "check_ollama_not_exposed(cfg.port, on_unverifiable")], R3),
    "r3_watch_not_started": ("tarefa periodica nunca iniciada", [("server.py", "self.watch_task = loop.create_task(self.exposure_watch.run())", "self.watch_task = None")], R3),
    "r3_watch_not_cancelled": ("desligamento nao cancela a tarefa", [("server.py", "                self.watch_task.cancel()\n                await asyncio.gather(self.watch_task, return_exceptions=True)\n", "                pass\n")], R3),
    "r3_main_unwired": ("__main__ nao liga a tarefa periodica", [("__main__.py", "server = GatewayServer(cfg, ctx, ollama, log, exposure_watch=ExposureWatch(cfg, log))", "server = GatewayServer(cfg, ctx, ollama, log)")], R3),
    # --- perfis
    "think_profile_on_by_default": ("perfil think ligado por padrao", [("profiles.py", "DEFAULT_ENABLED = (PROFILE_ID,)", "DEFAULT_ENABLED = (PROFILE_ID, PROFILE_THINK_ID)")], None),
    "think_flag_wrong": ("think=True no perfil padrao", [("profiles.py", "        think=False,\n        temperature=0,", "        think=True,\n        temperature=0,")], None),
    # --- cliente
    "client_hostname_check_off": ("cliente sem verificacao de hostname", [("client.py", "ctx.check_hostname = True", "ctx.check_hostname = False")], None),
    "client_breaker_100": ("disjuntor so abre com 100 falhas", [("client.py", "BREAKER_FAILURES = 3", "BREAKER_FAILURES = 100")], None),
    "client_connect_timeout_60": ("prazo de conexao do cliente 60 s", [("client.py", "CONNECT_TIMEOUT = 5.0", "CONNECT_TIMEOUT = 60.0")], None),
    "client_resets_breaker_on_send_failure": ("falha apos envio zera o disjuntor", [("client.py", "            self._record_failure()\n            raise GatewayClientError(\"unavailable\") from None\n        finally:", "            self._record_success()\n            raise GatewayClientError(\"unavailable\") from None\n        finally:")], None),
}


def run_one(name: str) -> dict:
    desc, edits, tests = M[name]
    work = Path(tempfile.mkdtemp(prefix=f"aigw-mut-{name[:20]}-"))
    try:
        dst = work / "src"
        shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns("__pycache__"))
        for fname, old, new in edits:
            p = dst / "auneron_ai_gateway" / fname
            text = p.read_text(encoding="utf-8")
            if text.count(old) != 1:
                return {"name": name, "result": "INVALIDO", "detail": f"{fname}: padrao encontrado {text.count(old)}x"}
            p.write_text(text.replace(old, new), encoding="utf-8")
        env = dict(os.environ, AUNERON_GW_SRC=str(dst), PYTHONDONTWRITEBYTECODE="1")
        order = ["units", "boundary", "uvicorn_interface", "mem_mtls", "access", "contract", "routes", "model",
                 "privacy", "failures", "startup", "fuzz", "load"]
        cmd = [str(PY), "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider"] + (
            tests or [f"tests/test_{n}.py" for n in order]
        )
        t0 = time.monotonic()
        try:
            r = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=900)
            out = r.stdout
            code = r.returncode
        except subprocess.TimeoutExpired:
            return {"name": name, "result": "KILLED", "detail": "timeout (tratado como morto por travar)", "desc": desc}
        failed = [ln for ln in out.splitlines() if ln.startswith(("FAILED tests", "ERROR tests"))]
        res = "SURVIVED" if code == 0 else "KILLED"
        return {"name": name, "desc": desc, "result": res, "secs": round(time.monotonic() - t0, 1),
                "killed_by": failed[:1] if res == "KILLED" else [], "exit": code}
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    names = sys.argv[1:] or list(M)
    results = []
    for n in names:
        r = run_one(n)
        results.append(r)
        print(json.dumps(r, ensure_ascii=False), flush=True)
    out = ROOT / "mutation_results.json"
    prev = {r["name"]: r for r in json.loads(out.read_text(encoding="utf-8"))} if out.exists() and sys.argv[1:] else {}
    prev.update({r["name"]: r for r in results})  # reexecucao parcial atualiza so os mutantes rodados
    results = [prev[n] for n in M if n in prev]
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    killed = sum(1 for r in results if r["result"] == "KILLED")
    print(f"RESUMO: {killed}/{len(results)} mortos; sobreviventes: "
          f"{[r['name'] for r in results if r['result'] == 'SURVIVED']}; invalidos: "
          f"{[r['name'] for r in results if r['result'] == 'INVALIDO']}")
