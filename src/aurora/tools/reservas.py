"""Tools de reserva das áreas comuns."""

from google.adk.tools import ToolContext

from .. import repositorio
from ..repositorio import Area
from ..sessao import apartamento_da_sessao

_AREAS_VALIDAS = "salao-de-festas, churrasqueira ou quadra"


def _validar(area: str, data: str) -> tuple[Area, str] | dict:
    area_resolvida = repositorio.resolver_area(area)
    if area_resolvida is None:
        return {"status": "erro", "mensagem": f"Área desconhecida. Use {_AREAS_VALIDAS}."}
    data_valida = repositorio.validar_data(data)
    if data_valida is None:
        return {"status": "erro", "mensagem": "Data inválida. Use o formato AAAA-MM-DD."}
    return area_resolvida, data_valida


def listar_minhas_reservas(tool_context: ToolContext) -> dict:
    """Lista as reservas ativas do apartamento do morador.

    Returns:
        As reservas com código, área e data.
    """
    apartamento = apartamento_da_sessao(tool_context)
    return {"reservas": repositorio.listar_reservas(apartamento)}


def verificar_disponibilidade(area: str, data: str) -> dict:
    """Informa se uma área comum está livre em uma data.

    Args:
        area: salao-de-festas, churrasqueira ou quadra.
        data: data no formato AAAA-MM-DD.

    Returns:
        Se a área está disponível na data. Nunca informa de quem é a reserva.
    """
    validado = _validar(area, data)
    if isinstance(validado, dict):
        return validado
    area_ok, data_ok = validado
    return {
        "area": area_ok.id,
        "data": data_ok,
        "disponivel": not repositorio.area_ocupada(area_ok.id, data_ok),
    }


def reservar_area(area: str, data: str, tool_context: ToolContext) -> dict:
    """Reserva uma área comum para o apartamento do morador.

    Áreas com taxa geram cobrança, e o próprio sistema pede a confirmação ao
    morador pelo aplicativo antes de gravar. Chame esta tool direto, sem pedir
    confirmação na conversa.

    Args:
        area: salao-de-festas, churrasqueira ou quadra.
        data: data no formato AAAA-MM-DD.

    Returns:
        O resultado: reservada (com o código), aguardando_confirmacao,
        nao_confirmada, indisponivel ou erro.
    """
    apartamento = apartamento_da_sessao(tool_context)
    validado = _validar(area, data)
    if isinstance(validado, dict):
        return validado
    area_ok, data_ok = validado

    confirmacao = tool_context.tool_confirmation
    if confirmacao is None:
        if repositorio.area_ocupada(area_ok.id, data_ok):
            return {"status": "indisponivel", "mensagem": f"{area_ok.nome} já está reservado(a) em {data_ok}."}
        if area_ok.gera_cobranca:
            # Garantia 1: a cobrança só acontece depois da resposta na rota de
            # confirmações. Até lá, nada é gravado.
            tool_context.request_confirmation(
                hint=f"Reservar {area_ok.nome} em {data_ok} gera cobrança de R$ {area_ok.taxa:.2f}.",
                payload={
                    "acao": "reservar_area",
                    "detalhes": {"area": area_ok.id, "data": data_ok, "taxa": area_ok.taxa},
                },
            )
            return {
                "status": "aguardando_confirmacao",
                "mensagem": "A reserva gera cobrança e aguarda a confirmação do morador no aplicativo.",
            }
    elif not confirmacao.confirmed:
        return {"status": "nao_confirmada", "mensagem": "O morador não confirmou a cobrança. Nada foi reservado."}

    # Garantia 5: a exclusividade é decidida no INSERT, pelo índice único.
    resultado = repositorio.criar_reserva(apartamento, area_ok.id, data_ok, tool_context.function_call_id)
    if resultado.status == "indisponivel":
        return {"status": "indisponivel", "mensagem": f"{area_ok.nome} já está reservado(a) em {data_ok}."}
    return {
        "status": "reservada",
        "codigo": resultado.codigo,
        "area": area_ok.id,
        "data": data_ok,
        "cobranca": area_ok.taxa if area_ok.gera_cobranca else 0,
    }


def cancelar_reserva(area: str, data: str, tool_context: ToolContext) -> dict:
    """Cancela uma reserva do apartamento do morador. Não pede confirmação.

    Args:
        area: salao-de-festas, churrasqueira ou quadra.
        data: data da reserva no formato AAAA-MM-DD.

    Returns:
        O código cancelado, ou nao_encontrada se o apartamento do morador não
        tiver reserva ativa nessa área e data.
    """
    apartamento = apartamento_da_sessao(tool_context)
    validado = _validar(area, data)
    if isinstance(validado, dict):
        return validado
    area_ok, data_ok = validado
    cancelados = repositorio.cancelar_reserva(apartamento, area_ok.id, data_ok)
    if not cancelados:
        return {
            "status": "nao_encontrada",
            "mensagem": f"O apartamento do morador não tem reserva de {area_ok.nome} em {data_ok}.",
        }
    return {"status": "cancelada", "codigos": cancelados, "area": area_ok.id, "data": data_ok}
