"""API HTTP do assistente, conforme o contrato do desafio."""

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.genai import errors as genai_errors
from pydantic import BaseModel

from . import repositorio
from .conversa import ConfirmacaoNaoPendente, Conversas, SessaoNaoEncontrada, apartamento_existe


class NovaSessao(BaseModel):
    apartamento: str


class NovaMensagem(BaseModel):
    texto: str


class RespostaConfirmacao(BaseModel):
    id: str
    confirmado: bool


@asynccontextmanager
async def _ciclo_de_vida(api: FastAPI):
    repositorio.inicializar()
    api.state.conversas = Conversas()
    yield
    await api.state.conversas.fechar()


app = FastAPI(title="Assistente do Residencial Aurora", lifespan=_ciclo_de_vida)


def _conversas(request: Request) -> Conversas:
    return request.app.state.conversas


@app.exception_handler(SessaoNaoEncontrada)
async def _sessao_nao_encontrada(_: Request, __: SessaoNaoEncontrada) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "Sessão não encontrada."})


@app.exception_handler(ConfirmacaoNaoPendente)
async def _confirmacao_nao_pendente(_: Request, __: ConfirmacaoNaoPendente) -> JSONResponse:
    return JSONResponse(
        status_code=409, content={"detail": "Não existe confirmação pendente com esse id nesta sessão."}
    )


@app.exception_handler(genai_errors.APIError)
async def _falha_no_modelo(_: Request, erro: genai_errors.APIError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": f"Falha ao consultar o modelo: {erro.message}"})


@app.exception_handler(LlmCallsLimitExceededError)
async def _limite_de_chamadas(_: Request, __: LlmCallsLimitExceededError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "O assistente não concluiu o pedido. Tente de novo."})


# ----------------------------------------------------------------------
# Conversa
# ----------------------------------------------------------------------


@app.post("/sessoes", status_code=201)
async def criar_sessao(corpo: NovaSessao, request: Request) -> dict:
    if not apartamento_existe(corpo.apartamento):
        raise HTTPException(status_code=422, detail="Apartamento inexistente.")
    return {"session_id": await _conversas(request).criar_sessao(corpo.apartamento)}


@app.post("/sessoes/{session_id}/mensagens")
async def enviar_mensagem(session_id: str, corpo: NovaMensagem, request: Request) -> dict:
    return await _conversas(request).enviar_mensagem(session_id, corpo.texto)


@app.post("/sessoes/{session_id}/confirmacoes")
async def responder_confirmacao(session_id: str, corpo: RespostaConfirmacao, request: Request) -> dict:
    return await _conversas(request).responder_confirmacao(session_id, corpo.id, corpo.confirmado)


@app.get("/sessoes/{session_id}/eventos")
async def eventos(session_id: str, request: Request) -> list[dict]:
    return await _conversas(request).eventos(session_id)


# ----------------------------------------------------------------------
# Verificação: leem o banco direto, sem passar pelo modelo
# ----------------------------------------------------------------------


@app.get("/apartamentos/{numero}/reservas")
async def reservas_do_apartamento(numero: str) -> list[dict]:
    return repositorio.listar_reservas(numero)


@app.get("/apartamentos/{numero}/visitantes")
async def visitantes_do_apartamento(numero: str) -> list[dict]:
    return repositorio.listar_visitantes(numero)


def main() -> None:
    uvicorn.run("aurora.api:app", host="127.0.0.1", port=8000)
