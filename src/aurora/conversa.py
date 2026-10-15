"""Ponte entre a API e o ADK: sessões, execução do Runner e confirmações."""

import asyncio
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from google.adk.agents import RunConfig
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService, Session
from google.genai import types

from . import config, repositorio
from .agentes import app

CONFIRMACAO = "adk_request_confirmation"

# Teto de chamadas ao modelo por mensagem: evita que uma troca de agentes em
# loop consuma a cota inteira.
_CONFIGURACAO_DE_EXECUCAO = RunConfig(max_llm_calls=40)


class SessaoNaoEncontrada(Exception):
    pass


class ConfirmacaoNaoPendente(Exception):
    pass


@dataclass(frozen=True)
class Pendencia:
    id: str
    invocation_id: str
    acao: str
    detalhes: dict[str, Any]

    def como_json(self) -> dict[str, Any]:
        return {"id": self.id, "acao": self.acao, "detalhes": self.detalhes}


def confirmacoes_pendentes(eventos: list[Event]) -> list[Pendencia]:
    """Pedidos de confirmação da sessão que ainda não receberam resposta.

    Um pedido é a chamada ``adk_request_confirmation`` que o ADK grava quando
    uma tool chama ``request_confirmation``. Ele deixa de estar pendente quando
    a sessão recebe a ``function_response`` com o mesmo id, que só a rota de
    confirmações envia.
    """
    pedidos: dict[str, Pendencia] = {}
    respondidos: set[str] = set()
    for evento in eventos:
        for chamada in evento.get_function_calls():
            if chamada.name == CONFIRMACAO and chamada.id:
                args = chamada.args or {}
                payload = (args.get("toolConfirmation") or {}).get("payload") or {}
                original = args.get("originalFunctionCall") or {}
                pedidos[chamada.id] = Pendencia(
                    id=chamada.id,
                    invocation_id=evento.invocation_id,
                    acao=payload.get("acao") or original.get("name", ""),
                    detalhes=payload.get("detalhes") or original.get("args", {}),
                )
        for resposta in evento.get_function_responses():
            if resposta.name == CONFIRMACAO and resposta.id:
                respondidos.add(resposta.id)
    return [p for id_, p in pedidos.items() if id_ not in respondidos]


def _texto_do_agente(evento: Event) -> str:
    if evento.author == "user" or evento.partial or not evento.content or not evento.content.parts:
        return ""
    return "".join(p.text for p in evento.content.parts if p.text and not p.thought)


class Conversas:
    def __init__(self) -> None:
        config.DIR_ESTADO.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(config.BANCO_SESSOES) as con:
            con.execute("PRAGMA journal_mode = WAL")
        self.sessoes = DatabaseSessionService(config.URL_BANCO_SESSOES, connect_args={"timeout": 30})
        self.runner = Runner(app=app, session_service=self.sessoes)
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._apartamento_da_sessao: dict[str, str] = {}

    async def fechar(self) -> None:
        await self.runner.close()

    # ------------------------------------------------------------------
    # Sessões
    # ------------------------------------------------------------------

    async def criar_sessao(self, apartamento: str) -> str:
        # Garantia 2: o apartamento é fixado aqui, uma única vez, como user_id
        # e no state da sessão. Daqui em diante as tools só leem esse valor.
        sessao = await self.sessoes.create_session(
            app_name=config.NOME_APP,
            user_id=apartamento,
            state={config.CHAVE_APARTAMENTO: apartamento},
        )
        self._apartamento_da_sessao[sessao.id] = apartamento
        return sessao.id

    async def _apartamento(self, session_id: str) -> str | None:
        if session_id not in self._apartamento_da_sessao:
            # Depois de um reinício o cache está vazio: recupera do banco.
            resposta = await self.sessoes.list_sessions(app_name=config.NOME_APP)
            self._apartamento_da_sessao.update({s.id: s.user_id for s in resposta.sessions})
        return self._apartamento_da_sessao.get(session_id)

    async def _carregar(self, session_id: str) -> Session:
        apartamento = await self._apartamento(session_id)
        sessao = None
        if apartamento is not None:
            sessao = await self.sessoes.get_session(
                app_name=config.NOME_APP, user_id=apartamento, session_id=session_id
            )
        if sessao is None:
            raise SessaoNaoEncontrada(session_id)
        return sessao

    async def eventos(self, session_id: str) -> list[dict[str, Any]]:
        sessao = await self._carregar(session_id)
        return [e.model_dump(mode="json", exclude_none=True, by_alias=True) for e in sessao.events]

    # ------------------------------------------------------------------
    # Conversa
    # ------------------------------------------------------------------

    async def enviar_mensagem(self, session_id: str, texto: str) -> dict[str, Any]:
        async with self._locks[session_id]:
            sessao = await self._carregar(session_id)
            # Só texto: nada que o morador escreva vira resposta de confirmação.
            mensagem = types.Content(role="user", parts=[types.Part(text=texto)])
            resposta = await self._executar(sessao, mensagem)
            return await self._resultado(session_id, resposta)

    async def responder_confirmacao(self, session_id: str, confirmacao_id: str, confirmado: bool) -> dict[str, Any]:
        async with self._locks[session_id]:
            sessao = await self._carregar(session_id)
            pendencia = next((p for p in confirmacoes_pendentes(sessao.events) if p.id == confirmacao_id), None)
            if pendencia is None:
                # Garantia 1: id desconhecido ou já respondido não executa nada.
                raise ConfirmacaoNaoPendente(confirmacao_id)
            mensagem = types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=pendencia.id, name=CONFIRMACAO, response={"confirmed": confirmado}
                        )
                    )
                ],
            )
            # O invocation_id do pedido faz o Runner retomar a execução pausada
            # no agente que pediu a confirmação.
            resposta = await self._executar(sessao, mensagem, invocation_id=pendencia.invocation_id)
            return await self._resultado(session_id, resposta)

    async def _executar(self, sessao: Session, mensagem: types.Content, invocation_id: str | None = None) -> str:
        textos = []
        async for evento in self.runner.run_async(
            user_id=sessao.user_id,
            session_id=sessao.id,
            new_message=mensagem,
            invocation_id=invocation_id,
            run_config=_CONFIGURACAO_DE_EXECUCAO,
        ):
            if texto := _texto_do_agente(evento):
                textos.append(texto)
        return "\n".join(textos).strip()

    async def _resultado(self, session_id: str, resposta: str) -> dict[str, Any]:
        sessao = await self._carregar(session_id)
        return {
            "resposta": resposta,
            "confirmacoes_pendentes": [p.como_json() for p in confirmacoes_pendentes(sessao.events)],
        }


def apartamento_existe(apartamento: str) -> bool:
    return apartamento in repositorio.apartamentos()
