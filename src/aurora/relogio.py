"""Relógio de alta resolução e estritamente crescente para os eventos do ADK.

O DatabaseSessionService devolve os eventos da sessão ordenados pelo
timestamp. No Windows com Python 3.12, ``time.time()`` avança em passos de
~15,6 ms, então eventos gravados no mesmo passo empatam e voltam do banco em
ordem arbitrária (a resposta de uma tool antes da chamada, por exemplo), o que
embaralha o histórico que o ADK remonta a cada mensagem e a rota de eventos.

Este relógio usa ``perf_counter`` (resolução de microssegundos) ancorado no
relógio de parede e nunca repete nem volta um valor.
"""

import threading
import time

from google.adk.platform import time as platform_time

_BASE_PAREDE = time.time()
_BASE_CONTADOR = time.perf_counter()
_trava = threading.Lock()
_ultimo = 0.0


def agora() -> float:
    global _ultimo
    with _trava:
        valor = round(_BASE_PAREDE + (time.perf_counter() - _BASE_CONTADOR), 6)
        if valor <= _ultimo:
            valor = round(_ultimo + 1e-6, 6)
        _ultimo = valor
        return valor


def instalar() -> None:
    # O ADK lê o horário sempre por `platform_time.get_time()`.
    platform_time.get_time = agora
