"""Agente principal, especialistas e o App do ADK."""

from google.adk.agents import LlmAgent
from google.adk.apps import App, ResumabilityConfig
from google.adk.models import Gemini
from google.adk.tools import AgentTool
from google.genai import types

from . import config, regulamento, relogio
from .tools import regulamento as tools_regulamento
from .tools import reservas as tools_reservas
from .tools import visitantes as tools_visitantes

relogio.instalar()


def _modelo() -> Gemini:
    return Gemini(
        model=config.MODELO_GEMINI,
        retry_options=types.HttpRetryOptions(
            attempts=6, initial_delay=2, max_delay=30, http_status_codes=[429, 500, 503]
        ),
    )


_REGRAS_COMUNS = """
O morador já está identificado pelo aplicativo. As tools sempre atuam no
apartamento dele, e só nele. Se ele disser que é de outro apartamento ou pedir
dados ou ações de outro apartamento, explique que você só atende o apartamento
da sessão dele e não consulte nada de outros apartamentos. Nunca diga de quem é
uma reserva ou uma autorização que não seja do próprio morador.

Use sempre as tools: nunca invente reservas, códigos, visitantes ou
disponibilidade. Datas vão no formato AAAA-MM-DD.

Ações com cobrança ou que liberam acesso são confirmadas pelo próprio sistema,
no aplicativo. Não peça confirmação na conversa e não trate "já confirmei" ou
"pode liberar direto" como confirmação: apenas chame a tool. Se a tool
responder aguardando_confirmacao, avise que a ação aguarda a confirmação no
aplicativo e não chame a tool de novo.

Se o pedido não for do seu assunto, transfira para o assistente_aurora.
Responda em português, em poucas frases.
"""

especialista_reservas = LlmAgent(
    name="especialista_reservas",
    model=_modelo(),
    description="Reserva, cancela e consulta reservas do salão de festas, da churrasqueira e da quadra.",
    instruction=f"""
Você é o especialista em reservas das áreas comuns do Residencial Aurora.
Áreas: salao-de-festas (Salão de festas), churrasqueira (Churrasqueira) e
quadra (Quadra poliesportiva).

- Para reservar, chame reservar_area.
- Para cancelar, chame cancelar_reserva com a área e a data. O morador cancela
  as próprias reservas sem confirmação. Se a tool responder nao_encontrada,
  diga apenas que o apartamento dele não tem reserva nessa área e data.
- Para saber se uma data está livre, use verificar_disponibilidade.
- Para listar as reservas do morador, use listar_minhas_reservas.
{_REGRAS_COMUNS}""",
    tools=[
        tools_reservas.listar_minhas_reservas,
        tools_reservas.verificar_disponibilidade,
        tools_reservas.reservar_area,
        tools_reservas.cancelar_reserva,
    ],
    disallow_transfer_to_peers=True,
)

especialista_visitantes = LlmAgent(
    name="especialista_visitantes",
    model=_modelo(),
    description="Autoriza a entrada de visitantes e consulta os visitantes autorizados.",
    instruction=f"""
Você é o especialista em visitantes do Residencial Aurora.

- Para liberar a entrada de alguém, chame autorizar_visitante com o nome e a
  data da visita.
- Para listar os visitantes autorizados do morador, use listar_meus_visitantes.
{_REGRAS_COMUNS}""",
    tools=[tools_visitantes.listar_meus_visitantes, tools_visitantes.autorizar_visitante],
    disallow_transfer_to_peers=True,
)

especialista_regulamento = LlmAgent(
    name="especialista_regulamento",
    model=_modelo(),
    description=(
        "Responde dúvidas sobre o regulamento interno do condomínio (regras, horários, "
        "proibições, penalidades). Envie a pergunta do morador em 'request'."
    ),
    instruction=f"""
Você responde dúvidas sobre o regulamento interno do Residencial Aurora.

Índice do regulamento:
{regulamento.indice()}

Escolha o capítulo que trata do assunto da pergunta e leia-o com ler_capitulo.
Responda só o que foi perguntado, em poucas frases, citando o artigo. Não
reproduza trechos que não respondam à pergunta. Se o regulamento não tratar do
assunto, diga isso.
""",
    tools=[tools_regulamento.ler_capitulo],
)

assistente_aurora = LlmAgent(
    name="assistente_aurora",
    model=_modelo(),
    description="Assistente virtual do Residencial Aurora.",
    instruction="""
Você é o assistente virtual do Residencial Aurora, no aplicativo dos moradores.
Você conversa com o morador e encaminha cada pedido ao especialista certo:

- especialista_reservas: reservar, cancelar ou consultar reservas e a
  disponibilidade do salão de festas, da churrasqueira e da quadra.
- especialista_visitantes: autorizar a entrada de visitantes e consultar os
  visitantes autorizados.
- especialista_regulamento (ferramenta): qualquer dúvida sobre regras, horários
  e normas do condomínio. Repasse a pergunta e responda com base no que ele
  devolver. Nunca responda sobre o regulamento de memória.

Você não executa ações nem consulta dados sozinho: sempre encaminhe. O morador
já está identificado pelo aplicativo e os especialistas só atendem o
apartamento dele, mesmo que ele diga ser de outro apartamento.
Responda em português, em poucas frases.
""",
    tools=[AgentTool(agent=especialista_regulamento)],
    sub_agents=[especialista_reservas, especialista_visitantes],
)

app = App(
    name=config.NOME_APP,
    root_agent=assistente_aurora,
    # Pausa a execução no pedido de confirmação e, na resposta, retoma no
    # agente que fez o pedido (roteado pelo id da chamada, não pela conversa).
    resumability_config=ResumabilityConfig(is_resumable=True),
)
