"""Caminhos, variáveis de ambiente e constantes do projeto."""

import os
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[2]
load_dotenv(RAIZ / ".env")

NOME_APP = "aurora"

# Estado inicial do condomínio (somente leitura).
DIR_DADOS = RAIZ / "dados"

# Estado mutável: reservas, visitantes e sessões do ADK.
DIR_ESTADO = RAIZ / "estado"
BANCO_CONDOMINIO = DIR_ESTADO / "condominio.db"
BANCO_SESSOES = DIR_ESTADO / "sessoes.db"
URL_BANCO_SESSOES = f"sqlite+aiosqlite:///{BANCO_SESSOES.as_posix()}"

MODELO_GEMINI = os.getenv("MODELO_GEMINI") or "gemini-3.8-flash"

# Chave da sessão onde fica o apartamento do morador autenticado.
CHAVE_APARTAMENTO = "apartamento"
