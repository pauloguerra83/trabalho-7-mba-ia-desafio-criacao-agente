# Assistente virtual do Residencial Aurora

Assistente do aplicativo dos moradores, construído com **Google ADK 2.9.1** e exposto por uma API **FastAPI** em `http://localhost:8000`. Pelo chat, o morador reserva e cancela reservas do salão de festas, da churrasqueira e da quadra, autoriza visitantes e tira dúvidas sobre o regulamento.

A filosofia do projeto: **o modelo decide o caminho, o código decide o que é permitido.** As cinco garantias do desafio estão no código (tools, banco e API), e nenhuma depende do que o modelo escreve ou do que o morador pede.

## Arquitetura

```
                     POST /sessoes/{id}/mensagens · /confirmacoes
                                       │
                         src/aurora/api.py  (FastAPI)
                                       │
                       src/aurora/conversa.py  (Runner + sessões + confirmações)
                                       │
                    ┌──────── assistente_aurora (agente principal) ────────┐
                    │ transferência        │ transferência      │ AgentTool │
         especialista_reservas   especialista_visitantes   especialista_regulamento
         listar_minhas_reservas  listar_meus_visitantes    ler_capitulo
         verificar_disponibilidade autorizar_visitante
         reservar_area
         cancelar_reserva
                    │                    │                        │
           src/aurora/repositorio.py (SQLite)        src/aurora/regulamento.py
           estado/condominio.db                       dados/regulamento.md
```

| Agente | Responsabilidade | Como é acionado | Por quê |
|---|---|---|---|
| `assistente_aurora` | Conversa com o morador e encaminha cada pedido. Não tem tools de dados nem o regulamento nas instruções. | Agente raiz do `App` | Um único ponto de entrada que só roteia mantém as instruções curtas e baratas em todas as mensagens. |
| `especialista_reservas` | Reservar, cancelar, listar reservas e checar disponibilidade. | Sub-agente, por **transferência** (`sub_agents`) | As chamadas de tool e os pedidos de confirmação precisam ficar gravados na sessão persistida, e a resposta de confirmação precisa voltar a este agente para retomar a tool. O `AgentTool` roda o agente num Runner filho com sessão em memória: a confirmação não sobreviveria a um reinício. |
| `especialista_visitantes` | Autorizar e listar visitantes. | Sub-agente, por **transferência** | Mesmo motivo: liberar acesso exige confirmação e retomada. |
| `especialista_regulamento` | Responder dúvidas a partir de `dados/regulamento.md`, lendo um capítulo por vez. | **`AgentTool`** do agente principal | O texto do capítulo fica dentro do Runner filho do `AgentTool`. A sessão principal guarda só a pergunta e a resposta curta, e o regulamento não acompanha as mensagens seguintes (Garantia 4). |

Outras decisões:

- **Sessões:** `DatabaseSessionService` em SQLite (`estado/sessoes.db`). O apartamento do morador é o `user_id` e também fica no state da sessão.
- **Dados do condomínio:** SQLite próprio (`estado/condominio.db`), carregado de `dados/*.json`, que nunca são alterados. Cancelar uma reserva marca `status = 'cancelada'` e mantém a linha.
- **Confirmações:** `App(resumability_config=ResumabilityConfig(is_resumable=True))`. A execução pausa no pedido de confirmação, e a rota de confirmações retoma a mesma invocação, no agente que fez o pedido, com o `invocation_id` gravado no evento.
- **Ordem dos eventos:** o `DatabaseSessionService` ordena os eventos por timestamp. No Windows com Python 3.12, `time.time()` avança de 15,6 em 15,6 ms, e eventos empatados voltavam do banco embaralhados. `src/aurora/relogio.py` instala no ADK um relógio de microssegundos, estritamente crescente.
- **Modelo:** Gemini via Google AI Studio, configurável em `MODELO_GEMINI`, com retentativas para 429 e 503. Cada mensagem tem um teto de 40 chamadas ao modelo (`RunConfig(max_llm_calls=40)`).

## Garantias

### Garantia 1: cobrança ou acesso só com confirmação

**Onde:**
- `src/aurora/tools/reservas.py`, função `reservar_area`:
  ```python
  if area_ok.gera_cobranca:
      tool_context.request_confirmation(hint=..., payload={"acao": "reservar_area", "detalhes": {...}})
      return {"status": "aguardando_confirmacao", ...}
  ```
- `src/aurora/tools/visitantes.py`, função `autorizar_visitante`: sempre chama `tool_context.request_confirmation(...)` antes de gravar.
- `src/aurora/conversa.py`, `confirmacoes_pendentes` e `Conversas.responder_confirmacao`:
  ```python
  pendencia = next((p for p in confirmacoes_pendentes(sessao.events) if p.id == confirmacao_id), None)
  if pendencia is None:
      raise ConfirmacaoNaoPendente(confirmacao_id)   # → 409 em api.py
  ```

**Como funciona:**
1. A tool decide em código se a ação gera cobrança (`Area.gera_cobranca`, `taxa > 0`, lida de `dados/areas.json`) ou libera acesso.
2. Nesse caso, pede confirmação e devolve sem gravar. O ADK grava o pedido como a chamada `adk_request_confirmation` e pausa a invocação.
3. A pendência é qualquer pedido sem `function_response` de mesmo id na sessão. Ela aparece em `confirmacoes_pendentes`, com os `detalhes` montados pela tool.
4. Só a rota `POST /sessoes/{id}/confirmacoes` envia essa `function_response`. Id desconhecido ou já respondido recebe 409 antes de chegar ao Runner.
5. Aprovar reexecuta a tool com `tool_confirmation.confirmed = True`, e a tool grava. Negar devolve `nao_confirmada` sem gravar.
6. A gravação é idempotente pelo `chamada_id` (id da chamada da tool): a mesma aprovação nunca grava duas vezes.

**Por que não depende do modelo:** a rota de mensagens monta só `types.Part(text=...)` (`Conversas.enviar_mensagem`). "Já estou confirmando aqui" é texto e nunca vira resposta de confirmação. O modelo não tem como pular `request_confirmation`, porque a decisão está dentro da tool.

### Garantia 2: cada sessão pertence a um apartamento

**Onde:**
- `src/aurora/conversa.py`, `Conversas.criar_sessao`: `create_session(user_id=apartamento, state={"apartamento": apartamento})`. É o único lugar onde o apartamento é definido.
- `src/aurora/sessao.py`, `apartamento_da_sessao`:
  ```python
  apartamento = tool_context.state.get(config.CHAVE_APARTAMENTO)
  if not apartamento or apartamento != tool_context.user_id or apartamento not in repositorio.apartamentos():
      raise ApartamentoInvalido(...)
  ```
- `src/aurora/repositorio.py`: `listar_reservas`, `cancelar_reserva` e `listar_visitantes` filtram por `WHERE apartamento = ?`.
- `src/aurora/repositorio.py`, `area_ocupada`, e `tools/reservas.py`, `verificar_disponibilidade`: devolvem só `disponivel: true/false`.

**Por que não depende do modelo:**
- **Nenhuma tool tem parâmetro `apartamento`.** Todas leem o valor da sessão por `apartamento_da_sessao`, então o modelo não tem como escolher outro apartamento.
- O SQL de leitura e escrita filtra por esse apartamento. Pedir para cancelar a reserva do 302 vira um `UPDATE ... WHERE apartamento = '101'`, que não encontra nada.
- A consulta de agenda devolve só livre ou ocupada: nem código nem dono chegam à conversa.

### Garantia 3: nada se perde no reinício

**Onde:**
- `src/aurora/conversa.py`, `Conversas.__init__`: `DatabaseSessionService(config.URL_BANCO_SESSOES, ...)`, com sessões e eventos em `estado/sessoes.db`.
- `Conversas._apartamento`: depois de um reinício, recupera do banco a qual apartamento cada sessão pertence (`list_sessions`).
- `src/aurora/repositorio.py`, `inicializar`: só carrega `dados/*.json` se `estado/condominio.db` não existir. A API nunca apaga dados ao subir.
- `src/aurora/repositorio.py`, `SCHEMA`: `codigo TEXT PRIMARY KEY`. A reserva cancelada continua na tabela, e `criar_reserva` sorteia `RSV-` com 8 dígitos hexadecimais, sorteando de novo se o código já existir. Assim nenhum código se repete, nem o de uma reserva cancelada.
- `src/aurora/relogio.py`: timestamps estritamente crescentes, para que os eventos voltem do banco na ordem em que foram gravados.

**Por que não depende do modelo:**
- Tudo o que a conversa produz é gravado em SQLite pelo ADK (eventos) ou pelas tools (reservas e visitantes).
- Uma confirmação pendente antes do reinício continua pendente e pode ser aprovada depois: o pedido e o `invocation_id` estão no banco.

### Garantia 4: o regulamento é consultado, não carregado

**Onde:**
- `src/aurora/regulamento.py`: divide `dados/regulamento.md` em capítulos (`capitulos`) e monta só o índice de títulos (`indice`).
- `src/aurora/tools/regulamento.py`, `ler_capitulo(numero)`: devolve um único capítulo.
- `src/aurora/agentes.py`: `especialista_regulamento` recebe só `regulamento.indice()` na instrução. O `assistente_aurora` o aciona como `AgentTool(agent=especialista_regulamento)`, e a instrução do agente principal não contém o regulamento.

**Por que não depende do modelo:**
- Nenhum caminho do código coloca o regulamento inteiro em instrução ou evento.
- A leitura do capítulo acontece dentro do Runner filho do `AgentTool`, cuja sessão em memória é descartada (`google/adk/tools/agent_tool.py`). Na sessão principal ficam só a chamada `especialista_regulamento` e a resposta curta.
- Mesmo que o especialista lesse um capítulo errado, esse texto não chegaria aos eventos da sessão.

### Garantia 5: dois moradores, uma reserva

**Onde:**
- `src/aurora/repositorio.py`, `SCHEMA`:
  ```sql
  CREATE UNIQUE INDEX IF NOT EXISTS ux_reserva_ativa_area_data
      ON reservas (area, data) WHERE status = 'ativa';
  ```
- `src/aurora/repositorio.py`, `criar_reserva`: o `INSERT` roda em `BEGIN IMMEDIATE`, e o `sqlite3.IntegrityError` desse índice vira `ResultadoReserva("indisponivel")`.
- `src/aurora/tools/reservas.py`, `reservar_area`: devolve `{"status": "indisponivel", ...}`, uma resposta normal, com 200 na API.

**Por que não depende do modelo:**
- A conferência `area_ocupada` antes de pedir confirmação é só conveniência, para não cobrar por uma data já ocupada.
- Quem decide é o índice único parcial, **no instante da gravação**. Duas aprovações simultâneas tentam o `INSERT` e o SQLite aceita apenas uma.
- O lock da API é por sessão (`Conversas._locks`), então as duas aprovações correm de fato em paralelo e a exclusividade fica a cargo do banco.

## Como rodar

### Pré-requisitos

- Python 3.12 ou superior.
- [uv](https://docs.astral.sh/uv/).
- Uma chave do [Google AI Studio](https://aistudio.google.com/apikey).

Nenhum serviço externo é necessário: os dois bancos são arquivos SQLite em `estado/`, criados automaticamente.

### Variáveis do `.env`

| Variável | Obrigatória | Descrição |
|---|---|---|
| `GOOGLE_API_KEY` | sim | Chave do Google AI Studio. |
| `MODELO_GEMINI` | não | Modelo Gemini usado pelos agentes. Padrão: `gemini-3.8-flash`. Confira os modelos disponíveis e os limites do seu projeto no AI Studio. |

```bash
cp .env.example .env    # e preencha GOOGLE_API_KEY
uv sync
```

### Restaurar os dados iniciais

```bash
uv run aurora-restaurar
```

Volta reservas e visitantes ao estado de `dados/*.json` e **apaga todas as sessões**. Pode rodar com a API no ar ou parada.

### Subir a API

```bash
uv run aurora-api
```

A API responde em `http://localhost:8000`. Pare com Ctrl+C. Ao subir de novo com o mesmo comando, conversas, reservas e visitantes continuam lá.

### Conferir o fluxo do avaliador (opcional)

```bash
uv run aurora-restaurar
uv run aurora-api                                  # em outro terminal
uv run python scripts/verificar_fluxo.py --fase 1  # passos 1 a 12
# pare a API com Ctrl+C e suba de novo, sem restaurar
uv run python scripts/verificar_fluxo.py --fase 2  # passos 13 e 14
```

Para ver transferências, chamadas de tool e confirmações durante o desenvolvimento: `uv run adk web src`.
