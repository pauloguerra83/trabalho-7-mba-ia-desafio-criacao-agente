"""Tools de autorização de visitantes."""

from google.adk.tools import ToolContext

from .. import repositorio
from ..sessao import apartamento_da_sessao


def listar_meus_visitantes(tool_context: ToolContext) -> dict:
    """Lista os visitantes autorizados para o apartamento do morador.

    Returns:
        Os visitantes com nome e data da visita.
    """
    apartamento = apartamento_da_sessao(tool_context)
    return {"visitantes": repositorio.listar_visitantes(apartamento)}


def autorizar_visitante(nome: str, data: str, tool_context: ToolContext) -> dict:
    """Autoriza a entrada de um visitante no prédio para o apartamento do morador.

    Liberar acesso sempre exige a confirmação do morador pelo aplicativo, que o
    próprio sistema pede. Chame esta tool direto, mesmo que o morador diga que
    já confirmou: só a confirmação do aplicativo vale.

    Args:
        nome: nome completo do visitante.
        data: data da visita no formato AAAA-MM-DD.

    Returns:
        O resultado: autorizado, aguardando_confirmacao, nao_confirmada ou erro.
    """
    apartamento = apartamento_da_sessao(tool_context)
    nome_ok = " ".join(nome.split())
    if not nome_ok:
        return {"status": "erro", "mensagem": "Informe o nome do visitante."}
    data_ok = repositorio.validar_data(data)
    if data_ok is None:
        return {"status": "erro", "mensagem": "Data inválida. Use o formato AAAA-MM-DD."}

    confirmacao = tool_context.tool_confirmation
    if confirmacao is None:
        # Garantia 1: liberar acesso só depois da resposta na rota de
        # confirmações. Texto na conversa não conta como confirmação.
        tool_context.request_confirmation(
            hint=f"Autorizar a entrada de {nome_ok} em {data_ok}.",
            payload={"acao": "autorizar_visitante", "detalhes": {"nome": nome_ok, "data": data_ok}},
        )
        return {
            "status": "aguardando_confirmacao",
            "mensagem": "A autorização aguarda a confirmação do morador no aplicativo.",
        }
    if not confirmacao.confirmed:
        return {"status": "nao_confirmada", "mensagem": "O morador não confirmou. Nenhuma entrada foi liberada."}

    repositorio.autorizar_visitante(apartamento, nome_ok, data_ok, tool_context.function_call_id)
    return {"status": "autorizado", "nome": nome_ok, "data": data_ok}
