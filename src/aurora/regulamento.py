"""Regulamento interno dividido em capítulos.

Só o índice (números e títulos) vai para as instruções do especialista. O texto
de um capítulo só é lido quando o especialista pede aquele capítulo.
"""

import re
from dataclasses import dataclass
from functools import cache

from . import config

_TITULO = re.compile(r"^## Capítulo ([IVXLC]+): (.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Capitulo:
    numero: int
    romano: str
    titulo: str
    texto: str

    @property
    def nome(self) -> str:
        return f"Capítulo {self.romano}: {self.titulo}"


@cache
def capitulos() -> tuple[Capitulo, ...]:
    texto = (config.DIR_DADOS / "regulamento.md").read_text(encoding="utf-8")
    titulos = list(_TITULO.finditer(texto))
    resultado = []
    for numero, titulo in enumerate(titulos, start=1):
        fim = titulos[numero].start() if numero < len(titulos) else len(texto)
        corpo = texto[titulo.end() : fim].strip()
        resultado.append(Capitulo(numero, titulo.group(1), titulo.group(2).strip(), corpo))
    return tuple(resultado)


def capitulo(numero: int) -> Capitulo | None:
    return next((c for c in capitulos() if c.numero == numero), None)


def indice() -> str:
    return "\n".join(f"{c.numero}. {c.nome}" for c in capitulos())
