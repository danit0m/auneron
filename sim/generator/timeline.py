"""
Tempo do mundo SIM (Design Freeze v2, secao 6.0).

Dia de negocio `n` (inteiro, 0..179) com data civil DERIVADA de D0 + n;
hora local em minutos (0..1439). Instante = (dia, minuto), ordenavel. O
offset UTC e fixo e normativo (calendar.yaml).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from datetime import timedelta

Instant = tuple[int, int]

MINUTES_PER_DAY = 1440


def to_minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    value = int(hours) * 60 + int(minutes)
    if not 0 <= value < MINUTES_PER_DAY:
        raise ValueError(f"hora invalida: {hhmm}")
    return value


def to_hhmm(minute: int) -> str:
    if not 0 <= minute < MINUTES_PER_DAY:
        raise ValueError(f"minuto invalido: {minute}")
    return f"{minute // 60:02d}:{minute % 60:02d}"


@dataclass(frozen=True)
class Calendar:
    d0: date
    days: int
    utc_offset_minutes: int
    business_weekdays: tuple[int, ...]
    non_business_days: tuple[int, ...]
    intraday: dict
    restart_backend: Instant
    stress_week: tuple[int, int]
    evidence_floor: Instant
    seasonal_due_windows: tuple[tuple[int, int], ...]

    @property
    def last_day(self) -> int:
        return self.days - 1

    def date_of(self, day: int) -> date:
        return self.d0 + timedelta(days=day)

    def iso(self, day: int) -> str:
        return self.date_of(day).isoformat()

    def is_business(self, day: int) -> bool:
        return (
            self.date_of(day).weekday() in self.business_weekdays
            and day not in self.non_business_days
        )

    def next_business_day(self, day: int) -> int:
        """Primeiro dia util ESTRITAMENTE depois de `day`."""
        candidate = day + 1
        while not self.is_business(candidate):
            candidate += 1
        return candidate

    def business_on_or_after(self, day: int) -> int:
        return day if self.is_business(day) else self.next_business_day(day)

    def add_business_days(self, day: int, count: int) -> int:
        current = day
        for _ in range(count):
            current = self.next_business_day(current)
        return current

    def effective_due(self, due_day: int) -> int:
        """Regra da empresa: vencimento em dia nao util pode ser pago no
        proximo dia util sem encargos."""
        return self.business_on_or_after(due_day)

    def utc_day(self, instant: Instant) -> int:
        """Indice do dia CIVIL UTC correspondente ao instante local."""
        day, minute = instant
        return day + (minute - self.utc_offset_minutes) // MINUTES_PER_DAY

    def slot(self, name: str) -> int:
        return to_minutes(self.intraday[name])

    def in_seasonal_window(self, due_day: int) -> bool:
        return any(lo <= due_day <= hi for lo, hi in self.seasonal_due_windows)


def calendar_from_config(raw: dict) -> Calendar:
    special = raw["special"]
    restart = special["restart_backend"]
    floor = special["evidence_floor"]
    stress = special["stress_week"]
    return Calendar(
        d0=date.fromisoformat(raw["d0"]),
        days=int(raw["days"]),
        utc_offset_minutes=int(raw["utc_offset_minutes"]),
        business_weekdays=tuple(int(x) for x in raw["business_weekdays"]),
        non_business_days=tuple(int(x) for x in raw["non_business_days"]),
        intraday=dict(raw["intraday"]),
        restart_backend=(int(restart["day"]), to_minutes(restart["at"])),
        stress_week=(int(stress["from_day"]), int(stress["to_day"])),
        evidence_floor=(int(floor["day"]), to_minutes(floor["at"])),
        seasonal_due_windows=tuple(
            (int(lo), int(hi)) for lo, hi in raw["seasonal_due_windows"]
        ),
    )
