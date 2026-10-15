"""Dados do condomínio em SQLite: carga inicial, consultas e gravações.

As regras que não podem depender do modelo moram aqui, no banco:

- ``ux_reserva_ativa_area_data`` é um índice único parcial: duas reservas
  ativas para a mesma área e data não podem coexistir, nem quando duas
  gravações chegam ao mesmo tempo (Garantia 5).
- ``codigo`` é chave primária e a reserva cancelada continua na tabela com
  ``status = 'cancelada'``, então um código nunca se repete.
- ``chamada_id`` guarda o id da chamada de tool que gravou a linha. Ele é
  único, então reexecutar a mesma chamada aprovada não grava de novo.
"""

import json
import secrets
import sqlite3
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import cache
from typing import Iterator, Literal

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS reservas (
    codigo       TEXT PRIMARY KEY,
    apartamento  TEXT NOT NULL,
    area         TEXT NOT NULL,
    data         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'ativa' CHECK (status IN ('ativa', 'cancelada')),
    chamada_id   TEXT UNIQUE,
    criada_em    TEXT NOT NULL,
    cancelada_em TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_reserva_ativa_area_data
    ON reservas (area, data) WHERE status = 'ativa';

CREATE TABLE IF NOT EXISTS visitantes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    apartamento TEXT NOT NULL,
    nome        TEXT NOT NULL,
    data        TEXT NOT NULL,
    chamada_id  TEXT UNIQUE,
    criado_em   TEXT NOT NULL
);
"""


# --------------------------------------------------------------------------
# Catálogo (dados/*.json, somente leitura)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Area:
    id: str
    nome: str
    taxa: float

    @property
    def gera_cobranca(self) -> bool:
        return self.taxa > 0


def _ler_json(nome: str):
    return json.loads((config.DIR_DADOS / nome).read_text(encoding="utf-8"))


@cache
def apartamentos() -> frozenset[str]:
    return frozenset(a["numero"] for a in _ler_json("apartamentos.json"))


@cache
def areas() -> dict[str, Area]:
    return {a["id"]: Area(a["id"], a["nome"], float(a["taxa"])) for a in _ler_json("areas.json")}


def _slug(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return "-".join("".join(c if c.isalnum() else " " for c in sem_acento.lower()).split())


def resolver_area(texto: str) -> Area | None:
    """Aceita o id ("salao-de-festas") ou o nome ("Salão de festas")."""
    alvo = _slug(texto)
    if not alvo:
        return None
    for area in areas().values():
        if alvo in (area.id, _slug(area.nome)):
            return area
    candidatas = [a for a in areas().values() if alvo in a.id or a.id.split("-")[0] in alvo]
    return candidatas[0] if len(candidatas) == 1 else None


def validar_data(texto: str) -> str | None:
    """Devolve a data no formato AAAA-MM-DD, ou None se for inválida."""
    texto = texto.strip()
    try:
        return date.fromisoformat(texto).isoformat() if len(texto) == 10 else None
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Conexão
# --------------------------------------------------------------------------


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conectar() -> sqlite3.Connection:
    config.DIR_ESTADO.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.BANCO_CONDOMINIO, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 30000")
    return con


@contextmanager
def _transacao() -> Iterator[sqlite3.Connection]:
    """Transação de escrita: BEGIN IMMEDIATE pega o lock de escrita na hora."""
    con = _conectar()
    try:
        con.execute("BEGIN IMMEDIATE")
        yield con
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


@contextmanager
def _leitura() -> Iterator[sqlite3.Connection]:
    con = _conectar()
    try:
        yield con
    finally:
        con.close()


# --------------------------------------------------------------------------
# Carga inicial e restauração
# --------------------------------------------------------------------------


def restaurar() -> None:
    """Volta reservas e visitantes ao estado de dados/*.json."""
    with _transacao() as con:
        con.execute("DROP TABLE IF EXISTS reservas")
        con.execute("DROP TABLE IF EXISTS visitantes")
        for comando in SCHEMA.split(";"):
            if comando.strip():
                con.execute(comando)
        agora = _agora()
        con.executemany(
            "INSERT INTO reservas (codigo, apartamento, area, data, criada_em) VALUES (?, ?, ?, ?, ?)",
            [(r["codigo"], r["apartamento"], r["area"], r["data"], agora) for r in _ler_json("reservas.json")],
        )
        con.executemany(
            "INSERT INTO visitantes (apartamento, nome, data, criado_em) VALUES (?, ?, ?, ?)",
            [(v["apartamento"], v["nome"], v["data"], agora) for v in _ler_json("visitantes.json")],
        )


def inicializar() -> None:
    """Cria o banco com os dados iniciais só se ele ainda não existir."""
    if not config.BANCO_CONDOMINIO.exists():
        restaurar()


# --------------------------------------------------------------------------
# Reservas
# --------------------------------------------------------------------------


def listar_reservas(apartamento: str) -> list[dict]:
    with _leitura() as con:
        linhas = con.execute(
            "SELECT codigo, area, data FROM reservas"
            " WHERE apartamento = ? AND status = 'ativa' ORDER BY data, codigo",
            (apartamento,),
        ).fetchall()
    return [dict(linha) for linha in linhas]


def area_ocupada(area: str, data: str) -> bool:
    with _leitura() as con:
        linha = con.execute(
            "SELECT 1 FROM reservas WHERE area = ? AND data = ? AND status = 'ativa'",
            (area, data),
        ).fetchone()
    return linha is not None


@dataclass(frozen=True)
class ResultadoReserva:
    status: Literal["reservada", "indisponivel"]
    codigo: str | None = None


def criar_reserva(apartamento: str, area: str, data: str, chamada_id: str) -> ResultadoReserva:
    """Grava a reserva. A exclusividade é decidida pelo índice único, no INSERT."""
    for _ in range(10):
        codigo = f"RSV-{secrets.token_hex(4).upper()}"
        try:
            with _transacao() as con:
                # Mesma chamada aprovada reexecutada: devolve o que já foi gravado.
                ja_gravada = con.execute(
                    "SELECT codigo FROM reservas WHERE chamada_id = ?", (chamada_id,)
                ).fetchone()
                if ja_gravada:
                    return ResultadoReserva("reservada", ja_gravada["codigo"])
                con.execute(
                    "INSERT INTO reservas (codigo, apartamento, area, data, chamada_id, criada_em)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (codigo, apartamento, area, data, chamada_id, _agora()),
                )
            return ResultadoReserva("reservada", codigo)
        except sqlite3.IntegrityError as erro:
            mensagem = str(erro)
            if "reservas.codigo" in mensagem:
                continue  # código sorteado já existe: sorteia outro
            if "reservas.area" in mensagem:
                return ResultadoReserva("indisponivel")
            raise
    raise RuntimeError("não foi possível gerar um código de reserva único")


def cancelar_reserva(apartamento: str, area: str, data: str) -> list[str]:
    """Cancela as reservas ativas do apartamento naquela área e data."""
    with _transacao() as con:
        linhas = con.execute(
            "UPDATE reservas SET status = 'cancelada', cancelada_em = ?"
            " WHERE apartamento = ? AND area = ? AND data = ? AND status = 'ativa'"
            " RETURNING codigo",
            (_agora(), apartamento, area, data),
        ).fetchall()
    return [linha["codigo"] for linha in linhas]


# --------------------------------------------------------------------------
# Visitantes
# --------------------------------------------------------------------------


def listar_visitantes(apartamento: str) -> list[dict]:
    with _leitura() as con:
        linhas = con.execute(
            "SELECT nome, data FROM visitantes WHERE apartamento = ? ORDER BY data, id",
            (apartamento,),
        ).fetchall()
    return [dict(linha) for linha in linhas]


def autorizar_visitante(apartamento: str, nome: str, data: str, chamada_id: str) -> None:
    try:
        with _transacao() as con:
            con.execute(
                "INSERT INTO visitantes (apartamento, nome, data, chamada_id, criado_em)"
                " VALUES (?, ?, ?, ?, ?)",
                (apartamento, nome, data, chamada_id, _agora()),
            )
    except sqlite3.IntegrityError as erro:
        if "visitantes.chamada_id" not in str(erro):
            raise
        # Mesma chamada aprovada reexecutada: a autorização já está gravada.
