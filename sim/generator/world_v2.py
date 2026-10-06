"""
Simulacao do mundo `sim.scenario.v2` (SIM-1.3 Design Freeze V1).

Herda de `sim.generator.world.Simulator` toda a mecanica do MUNDO (clientes,
titulos, pagamentos, evidencia bancaria, injecoes de emissao) e substitui
os corredores pelos corredores REAIS descobertos no SIM-1.3:

* baixa      = mark_paid governado: request (gerente) -> decision
               (coordenador) -> execution (gerente); `reported_at` = instante
               da EXECUCAO; `expected_status` previsto aqui e congelado na
               agenda (o Driver nunca consulta o produto para escolhe-lo);
* atraso     = corredor HUMANO de mark_overdue (F1 OUT): materialize
               (gerente) -> decision (coordenador) -> execution (gerente);
               aprovacao expirada/rejeitada = episodio sem saida;
* expiracao  = derivada de `expires_at` (TTL 1440 min);
* cobranca   = coordenador consulta NBA e escalona; analistas so registram
               human_assessment.

O modulo v1 nao e alterado: a v1 continua byte-identica (T-16).
"""

from __future__ import annotations

from sim.generator.rng import chance_bps
from sim.generator.rng import pick_weighted
from sim.generator.rng import substream
from sim.generator.timeline import to_minutes
from sim.generator.world import Simulator
from sim.oracle import rules
from sim.oracle import rules_v2

OPEN, APPROVED, DONE, FAILED, REJECTED, EXPIRED = (
    "open", "approved", "done", "failed", "rejected", "expired",
)
RUNTIME_CASES = ("C-RACE-1", "C-AUTH-1", "C-AUTH-2")


class SimulatorV2(Simulator):
    def __init__(self, inputs, variant: str, seed: int, injector, scenario_id: str) -> None:
        super().__init__(inputs, variant, seed, injector)
        personas = {p["user"]: p for p in inputs.company["personas"]}
        self.personas = personas
        self.coord = personas["sim-coordenador-fin"]
        self.analysts = [
            p["user"] for p in inputs.company["personas"] if p["function"] == "collections"
        ]
        chains = inputs.company["authority_chains"]
        self.standard = dict(chains["standard"])
        self.reserve = dict(chains["reserve"])
        self.ttl = int(inputs.company["approval_ttl_minutes"])
        self.scenario_id = scenario_id
        minimum = int(self.spec["min_case_instances"])
        self.quota = {case: minimum + 1 for case in RUNTIME_CASES}
        self.runtime_cases: dict[str, list] = {case: [] for case in RUNTIME_CASES}
        self.counters = {"MP": 0, "MO": 0, "ESC": 0}
        self.governed: list = []
        self.by_title: dict[str, list] = {}
        self.reconciled: dict[str, tuple] = {}
        self.mo_episodes: dict[tuple, dict] = {}
        self.esc_refs: dict[tuple, str] = {}
        self.race_titles: set = set()
        self.world.scenario_id = scenario_id
        self.world.voided_overrides = set()
        self.world.governed = self.governed
        self.world.reconciled = self.reconciled
        self.world.runtime_cases = self.runtime_cases
        self.world.esc_refs = self.esc_refs
        self.world.mo_episodes = self.mo_episodes

    # -- presenca e conhecimento -------------------------------------------
    def present(self, persona: dict, day: int) -> bool:
        if not self.cal.is_business(day):
            return False
        return not any(a["from_day"] <= day <= a["to_day"] for a in persona.get("absences", []))

    def known_absent(self, persona: dict, day: int) -> bool:
        """O que o GERENTE sabe: ausencia planejada e conhecida; nao
        planejada so e conhecida a partir do 2o dia."""
        for absence in persona.get("absences", []):
            first = absence["from_day"] if absence.get("planned", True) else absence["from_day"] + 1
            if first <= day <= absence["to_day"]:
                return True
        return False

    def gerente_can_submit(self, day: int) -> bool:
        return self.present(self.gerente, day) and not self.known_absent(self.coord, day)

    def idem(self, ref: str, step: str) -> str:
        return f"sim:{self.scenario_id}:{ref}:{step}"

    def next_ref(self, prefix: str) -> str:
        self.counters[prefix] += 1
        return f"{prefix}-{self.counters[prefix]:06d}"

    # -- pedidos governados ----------------------------------------------------
    def refresh_expiry(self, now) -> None:
        for request in self.governed:
            if request["state"] in (OPEN, APPROVED) and rules_v2.effectively_expired(request["expires"], now):
                request["state"] = EXPIRED
                request["expired_at"] = request["expires"]

    def live_mark_paid(self, title) -> dict | None:
        for request in self.by_title.get(title.ref, []):
            if request["kind"] == "mark_paid" and request["state"] in (OPEN, APPROVED):
                return request
        return None

    def new_request(self, kind: str, title, now, chain: dict, **extra) -> dict:
        prefix = "MP" if kind == "mark_paid" else "MO"
        ref = self.next_ref(prefix)
        request = {
            "ref": ref,
            "kind": kind,
            "rec_ref": title.ref,
            "requester": chain["requester"],
            "planned_decider": chain["decider"],
            "planned_executor": chain["executor"],
            "chain": "reserve" if chain == self.reserve else "standard",
            "created": now,
            "expires": rules_v2.expiry_instant(now, self.ttl),
            "state": OPEN,
            "decider": None,
            "decision": None,
            "decided_at": None,
            "executions": [],
            "violations": [],
            "deferred": False,
            **extra,
        }
        self.governed.append(request)
        self.by_title.setdefault(title.ref, []).append(request)
        return request

    # -- conciliacao (mundo) + request mark_paid ---------------------------
    def settlement_slot(self, day: int, slot_name: str) -> None:
        now = (day, self.cal.slot(slot_name))
        self.refresh_expiry(now)
        # Horario fixado so vale se a cadeia rodou no slot-base; senao o
        # titulo volta ao fluxo normal (registrado como override anulado).
        for title in self.world.titles:
            override = title.settle_override
            if override is None or title.reported is not None:
                continue
            base_day = override[0] if override[1] > 0 else override[0] - 1
            base = (base_day, self.cal.slot("settlement_day"))
            if now > base and self.live_mark_paid(title) is None:
                title.settle_override = None
                self.world.voided_overrides = getattr(self.world, "voided_overrides", set()) | {title.ref}
        if self.present(self.receber, day):
            ready = []
            for title in self.world.titles:
                if title.ref in self.reconciled or title.evidence is None or title.evidence > now:
                    continue
                if title.settle_override is not None and not self._override_base(title, now):
                    continue
                if title.hold_until is not None and title.hold_until > now:
                    continue
                if title.queue_delay and not title.queue_skipped:
                    title.queue_skipped = True
                    continue
                ready.append(title)
            ready.sort(key=lambda t: (t.evidence, t.ref))
            for title in ready[: int(self.receber["capacity_per_slot"])]:
                self.reconciled[title.ref] = now
                self.world.event(now, "payment_reconciled", "persona",
                                 {"rec_ref": title.ref, "cust_ref": title.customer.ref},
                                 actor=self.receber["user"])
        if not self.gerente_can_submit(day):
            return
        for title in self.world.titles:
            if title.ref not in self.reconciled or title.reported is not None:
                continue
            if self.live_mark_paid(title) is not None:
                continue
            if title.settle_override is not None and not self._override_base(title, now):
                continue
            chain = self.standard
            if self._take_runtime("C-AUTH-1", title, now, extra_ok=title.settle_override is None):
                chain = self.reserve
            request = self.new_request("mark_paid", title, now, chain, expected_status=title.status)
            if chain is self.reserve:
                request["cases"] = ["C-AUTH-1"]
            self.world.op(now, chain["requester"], "request_mark_paid", rec_ref=title.ref,
                          ref=request["ref"], expected_status=request["expected_status"],
                          idempotency_key=self.idem(request["ref"], "request"))
            # C-RACE-1: falha humana deliberada -- decisao adiada para a tarde
            # e mark_overdue NAO bloqueado pela baixa em andamento.
            if (
                slot_name == "settlement_bank_return"
                and chain is self.standard
                and title.status == "aberto"
                and title.venc < day
                and (title.ref, title.venc) not in self.mo_episodes
                and self._take_runtime("C-RACE-1", title, now)
            ):
                request["deferred"] = True
                request.setdefault("cases", []).append("C-RACE-1")
                self.race_titles.add(title.ref)

    def _override_base(self, title, now) -> bool:
        """Titulos com execucao em horario fixado (C-TZ/C-ADV-7): a cadeia
        roda no slot das 16:00 do dia-base e a EXECUCAO no horario fixado."""
        override = title.settle_override
        base_day = override[0] if override[1] > 0 else override[0] - 1
        return now == (base_day, self.cal.slot("settlement_day"))

    def _take_runtime(self, case: str, title, now, extra_ok: bool = True) -> bool:
        used = self.runtime_cases[case]
        if not extra_ok or len(used) >= self.quota[case]:
            return False
        if any(ref == title.ref for ref, _ in used):
            return False
        day = now[0]
        spacing = max(6, 120 // self.quota[case])
        if day < 20 + spacing * len(used):
            return False
        if not (self.present(self.gerente, day) and self.present(self.coord, day)):
            return False
        used.append((title.ref, now))
        return True

    def decide_mark_paid(self, day: int, slot_name: str) -> None:
        now = (day, self.cal.slot(slot_name))
        self.refresh_expiry(now)
        afternoon = slot_name == "mark_paid_decide_2"
        for request in self.governed:
            if request["kind"] != "mark_paid" or request["state"] != OPEN:
                continue
            if request["created"] > now:
                continue
            if request["deferred"] and not afternoon:
                continue
            decider = request["planned_decider"]
            if not self.present(self.personas[decider], day):
                continue
            if self._take_auth_negative(request, now, "decider_is_requester"):
                self.world.op(now, request["requester"], "decide_mark_paid",
                              ref=request["ref"], decision="approve")
                request["violations"].append({"at": now, "actor": request["requester"],
                                              "attempt": "decider_is_requester"})
            request["state"] = APPROVED
            request["decider"] = decider
            request["decision"] = "approved"
            request["decided_at"] = now
            self.world.op(now, decider, "decide_mark_paid", ref=request["ref"], decision="approve")

    def _take_auth_negative(self, request, now, attempt: str) -> bool:
        if request.get("chain") != "standard" or request.get("deferred"):
            return False
        title = next(t for t in self.world.titles if t.ref == request["rec_ref"])
        if title.settle_override is not None:
            return False
        used = self.runtime_cases["C-AUTH-2"]
        wanted = [a for _, a in used]
        if attempt == "executor_is_decider" and wanted.count("executor_is_decider") >= (self.quota["C-AUTH-2"] + 1) // 2:
            return False
        if attempt == "decider_is_requester" and wanted.count("decider_is_requester") >= self.quota["C-AUTH-2"] // 2 + 1:
            return False
        if len(used) >= self.quota["C-AUTH-2"] + 1:
            return False
        day = now[0]
        spacing = max(6, 120 // (self.quota["C-AUTH-2"] + 1))
        if day < 25 + spacing * len(used):
            return False
        used.append((request["ref"], attempt))
        request.setdefault("cases", []).append("C-AUTH-2")
        return True

    def execute_mark_paid(self, day: int, slot_name: str | None, now=None) -> None:
        now = now or (day, self.cal.slot(slot_name))
        self.refresh_expiry(now)
        for request in list(self.governed):
            if request["kind"] != "mark_paid" or request["state"] != APPROVED:
                continue
            title = next(t for t in self.world.titles if t.ref == request["rec_ref"])
            override = title.settle_override
            if slot_name is not None and override is not None:
                continue  # executa no horario fixado
            if slot_name is None and override != now:
                continue
            executor = request["planned_executor"]
            if slot_name is not None and not self.present(self.personas[executor], day):
                continue
            if slot_name is not None and self._take_auth_negative(request, now, "executor_is_decider"):
                self.world.op(now, request["decider"], "execute_mark_paid", rec_ref=title.ref,
                              ref=request["ref"], expected_status=request["expected_status"])
                request["violations"].append({"at": now, "actor": request["decider"],
                                              "attempt": "executor_is_decider"})
            self.world.op(now, executor, "execute_mark_paid", rec_ref=title.ref,
                          ref=request["ref"], expected_status=request["expected_status"])
            reason = rules_v2.mark_paid_execution_result(title.status, request["expected_status"])
            if reason is None:
                request["state"] = DONE
                request["executions"].append({"at": now, "actor": executor, "result": "succeeded"})
                self._apply_paid(title, now, executor)
            else:
                request["state"] = FAILED
                request["executions"].append({"at": now, "actor": executor, "result": "failed",
                                              "reason": reason})

    def _apply_paid(self, title, now, actor: str) -> None:
        title.reported = now
        title.status_log.append((now, "pago"))
        subject = {"rec_ref": title.ref, "cust_ref": title.customer.ref}
        self.world.event(now, "receivable_settled", "persona", subject, actor=actor)
        snapshot = to_minutes("18:00")
        self.world.event((now[0], snapshot) if now[1] <= snapshot else now,
                         "reconciliation_result", "erp-sim", subject, result="matched")

    # -- mark_overdue humano ---------------------------------------------------
    def materialize_mark_overdue(self, day: int) -> None:
        now = (day, self.cal.slot("mark_overdue_materialize"))
        self.refresh_expiry(now)
        if not self.gerente_can_submit(day):
            return
        for title in self.world.titles:
            if title.issue > now or title.reported is not None or title.status != "aberto":
                continue
            if rules.mark_overdue_reason(title.status, title.venc, day) is not None:
                continue
            if (title.ref, title.venc) in self.mo_episodes:
                continue  # episodio ja materializado (inclusive sem saida)
            if self.live_mark_paid(title) is not None and title.ref not in self.race_titles:
                continue  # regra da empresa: baixa em andamento
            if title.ref in self.reconciled and title.ref not in self.race_titles:
                continue
            chain = self.standard
            request = self.new_request("mark_overdue", title, now, chain, venc=title.venc)
            if title.ref in self.race_titles:
                request.setdefault("cases", []).append("C-RACE-1")
            self.mo_episodes[(title.ref, title.venc)] = {"created": now, "ref": request["ref"]}
            self.world.op(now, chain["requester"], "materialize_mark_overdue", rec_ref=title.ref,
                          ref=request["ref"], vencimento_day=title.venc,
                          idempotency_key=self.idem(request["ref"], "materialize"))

    def decide_mark_overdue(self, day: int) -> None:
        now = (day, self.cal.slot("approvals"))
        self.refresh_expiry(now)
        for request in self.governed:
            if request["kind"] != "mark_overdue" or request["state"] != OPEN or request["created"] > now:
                continue
            decider = request["planned_decider"]
            if not self.present(self.personas[decider], day):
                continue
            knows_paid = request["rec_ref"] in self.reconciled and request["rec_ref"] not in self.race_titles
            decision = "rejected" if knows_paid else "approved"
            request["state"] = REJECTED if knows_paid else APPROVED
            request["decider"] = decider
            request["decision"] = decision
            request["decided_at"] = now
            self.world.op(now, decider, "decide_mark_overdue", ref=request["ref"],
                          decision="reject" if knows_paid else "approve")

    def execute_mark_overdue(self, day: int) -> None:
        now = (day, self.cal.slot("mark_overdue_execute"))
        self.refresh_expiry(now)
        for request in self.governed:
            if request["kind"] != "mark_overdue" or request["state"] != APPROVED:
                continue
            executor = request["planned_executor"]
            if not self.present(self.personas[executor], day):
                continue
            title = next(t for t in self.world.titles if t.ref == request["rec_ref"])
            self.world.op(now, executor, "execute_mark_overdue", rec_ref=title.ref, ref=request["ref"])
            reason = rules_v2.mark_overdue_execution_result(title.status, title.venc, request["venc"], day)
            if reason is None:
                request["state"] = DONE
                request["executions"].append({"at": now, "actor": executor, "result": "succeeded"})
                title.status_log.append((now, "atrasado"))
            else:
                request["state"] = FAILED
                request["executions"].append({"at": now, "actor": executor, "result": "failed",
                                              "reason": reason})

    # -- cobranca ----------------------------------------------------------------
    def escalation(self, day: int, slot_name: str) -> None:
        now = (day, self.cal.slot(slot_name))
        if not self.present(self.coord, day):
            return
        threshold = int(self.policy["escalate_at_days_overdue"])
        candidates = []
        for title in self.world.titles:
            if title.issue > now or title.reported is not None or title.status == "pago":
                continue
            if title.ref in self.reconciled:
                continue
            if rules.days_overdue(title.venc, day) < threshold:
                continue
            if title.workitem_for(title.venc) is not None:
                continue
            candidates.append(title)
        candidates.sort(key=lambda t: (-t.valor, -rules.days_overdue(t.venc, day), t.ref))
        actor = self.coord["user"]
        for title in candidates:
            self._consult(title, now, actor)
            last = self.world.ops[-1][3]
            last["vencimento_day"] = title.venc
        for title in candidates:
            ref = self.next_ref("ESC")
            self.esc_refs[(title.ref, title.venc)] = ref
            title.workitems.append({"venc": title.venc, "created": now, "ref": ref})
            self.world.op(now, actor, "materialize_escalation", rec_ref=title.ref, ref=ref,
                          vencimento_day=title.venc, idempotency_key=self.idem(ref, "materialize"))

    def assessments(self, day: int, slot_name: str) -> None:
        if not self.cal.is_business(day):
            return
        now = (day, self.cal.slot(slot_name))
        max_contacts = int(self.policy["max_contacts_per_episode"])
        gap = int(self.policy["recontact_after_business_days"])
        candidates = []
        for title in self.world.titles:
            if title.reported is not None or title.ref in self.reconciled:
                continue
            workitem = title.workitem_for(title.venc, now)
            if workitem is None:
                continue
            episode = [c for c in title.contacts if c["venc"] == title.venc]
            if not episode:
                candidates.append((0, title))
                continue
            if len(episode) >= max_contacts:
                continue
            if self.cal.add_business_days(episode[-1]["at"][0], gap) <= day:
                candidates.append((1, title))
        candidates.sort(key=lambda item: (item[0], -item[1].valor,
                                          -rules.days_overdue(item[1].venc, day), item[1].ref))
        capacity = {user: int(self.personas[user]["contacts_per_day"]) // 2 for user in self.analysts}
        position = 0
        for _, title in candidates:
            assigned = None
            for step in range(len(self.analysts)):
                user = self.analysts[(position + step) % len(self.analysts)]
                if capacity[user] > 0:
                    assigned = user
                    position = (position + step + 1) % len(self.analysts)
                    break
            if assigned is None:
                break
            capacity[assigned] -= 1
            self._contact_v2(title, now, assigned)

    def _contact_v2(self, title, now, actor: str) -> None:
        """Mesma semantica de resultado/efeito do `_contact` v1; muda so o
        payload publico (ref do escalonamento + chave idempotente)."""
        plan = title.plan
        number = len(title.contacts) + 1
        rng = substream(self.seed, "contact", title.ref, number)
        outcomes = self.inputs.population["contact_outcomes"]
        forced = plan.get("forced_codes") or []
        already_paid = title.fact is not None and title.fact <= now
        if already_paid:
            code = "contact_made"
        elif number <= len(forced):
            code = forced[number - 1]
        else:
            table = outcomes.get(title.profile_at_issue, outcomes["default"])
            if plan.get("kind") == "after_contact" and title.profile_at_issue not in outcomes:
                table = outcomes["P4"]
            code = pick_weighted(rng, [(int(w), k) for k, w in table.items()])
        esc_ref = self.esc_refs[(title.ref, title.venc)]
        title.contacts.append({"at": now, "actor": actor, "code": code, "venc": title.venc})
        self.world.op(now, actor, "record_assessment", rec_ref=title.ref, ref=esc_ref, code=code,
                      idempotency_key=self.idem(esc_ref, f"assessment-{number}"))
        subject = {"rec_ref": title.ref, "cust_ref": title.customer.ref}
        self.world.event(now, "customer_contact_outcome", "persona", subject, code=code)
        if code == "payment_promised":
            self.world.event(now, "promise_made", "customer", subject)
        if already_paid or title.fact is not None:
            return
        if plan.get("kind") == "after_contact":
            if plan.get("never_pay"):
                return
            if plan.get("pay_after_refusal") and code == "payment_refused":
                self.set_fact(title, self.fact_instant(title, now[0] + rng.randint(2, 4), rng))
                return
            if chance_bps(rng, int(plan.get("keep_pct", 70)) * 100):
                lo, hi = plan.get("pay_after", [1, 5])
                self.set_fact(title, self.fact_instant(title, now[0] + rng.randint(lo, hi), rng))

    # -- laco principal ------------------------------------------------------------
    def run(self):
        cal = self.cal
        for day in range(cal.days):
            self._override_executions(day, 0, 1)
            self.refresh_expiry((day, cal.slot("clock")))
            if day == 0:
                self.injector.opening(self)
            self.billing(day, "billing_1")
            self.settlement_slot(day, "settlement_bank_return")
            self.renegotiation(day)
            if (day, cal.slot("collections_1")) == cal.restart_backend:
                self.world.op(cal.restart_backend, "harness", "restart_backend")
            self.escalation(day, "collections_1")
            self.assessments(day, "assessments_1")
            self.decide_mark_paid(day, "mark_paid_decide_1")
            self.execute_mark_paid(day, "mark_paid_execute_1")
            self.materialize_mark_overdue(day)
            self.decide_mark_overdue(day)
            self.execute_mark_overdue(day)
            self.escalation(day, "collections_2")
            self.assessments(day, "assessments_2")
            self.injector.same_day_corrections(self, day)
            self.billing(day, "billing_2")
            self.settlement_slot(day, "settlement_day")
            self.decide_mark_paid(day, "mark_paid_decide_2")
            self.execute_mark_paid(day, "mark_paid_execute_2")
            self._override_executions(day, cal.slot("mark_paid_execute_2") + 1, 1440)
        self.refresh_expiry((cal.last_day, 1439))
        return self.world

    def _override_executions(self, day: int, lo: int, hi: int) -> None:
        instants = sorted({
            t.settle_override for t in self.world.titles
            if t.settle_override is not None and t.reported is None
            and t.settle_override[0] == day and lo <= t.settle_override[1] < hi
        })
        for instant in instants:
            self.execute_mark_paid(day, None, now=instant)
