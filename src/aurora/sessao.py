"""De onde as tools tiram o apartamento: da sessão, nunca do modelo."""

from google.adk.tools import ToolContext

from . import config, repositorio


class ApartamentoInvalido(Exception):
    pass


def apartamento_da_sessao(tool_context: ToolContext) -> str:
    """Apartamento do morador autenticado, gravado na criação da sessão.

    O valor vem do state da sessão e precisa bater com o ``user_id``, que é
    fixado na criação e não muda depois. Nenhuma tool recebe o apartamento
    como parâmetro, então o modelo não tem como escolher outro.
    """
    apartamento = tool_context.state.get(config.CHAVE_APARTAMENTO)
    if (
        not apartamento
        or apartamento != tool_context.user_id
        or apartamento not in repositorio.apartamentos()
    ):
        raise ApartamentoInvalido("sessão sem apartamento válido")
    return apartamento
