"""Volta reservas e visitantes ao estado de dados/ e apaga as sessões.

Uso: `uv run aurora-restaurar`.
"""

import asyncio

from google.adk.sessions import DatabaseSessionService

from . import config, repositorio


async def _apagar_sessoes() -> int:
    if not config.BANCO_SESSOES.exists():
        return 0
    servico = DatabaseSessionService(config.URL_BANCO_SESSOES, connect_args={"timeout": 30})
    try:
        resposta = await servico.list_sessions(app_name=config.NOME_APP)
        for sessao in resposta.sessions:
            await servico.delete_session(app_name=config.NOME_APP, user_id=sessao.user_id, session_id=sessao.id)
        return len(resposta.sessions)
    finally:
        await servico.close()


def main() -> None:
    repositorio.restaurar()
    apagadas = asyncio.run(_apagar_sessoes())
    print(f"Reservas e visitantes restaurados a partir de {config.DIR_DADOS}; {apagadas} sessão(ões) apagada(s).")


if __name__ == "__main__":
    main()
