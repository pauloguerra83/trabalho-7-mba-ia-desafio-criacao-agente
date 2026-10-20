"""Reproduz o fluxo do avaliador contra a API em http://localhost:8000.

Fase 1 (passos 1 a 12): rode logo depois de `uv run aurora-restaurar` e de subir a API.
Fase 2 (passos 13 e 14): pare a API com Ctrl+C, suba de novo sem restaurar e rode.

    uv run python scripts/verificar_fluxo.py --fase 1
    uv run python scripts/verificar_fluxo.py --fase 2
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
from aurora import regulamento  # noqa: E402

URL = "http://localhost:8000"
ARQUIVO_ESTADO = RAIZ / "estado" / "verificacao.json"
CODIGOS_INICIAIS = {"RSV-1377", "RSV-4821", "RSV-2950"}
NUMERO_302_ISOLADO = re.compile(r"(?<![0-9A-Za-z-])302(?![0-9])")

falhas: list[str] = []


def checar(condicao: bool, descricao: str) -> None:
    print(f"  {'OK ' if condicao else 'FALHOU'} {descricao}")
    if not condicao:
        falhas.append(descricao)


def cliente() -> httpx.Client:
    return httpx.Client(base_url=URL, timeout=300)


def enviar(c: httpx.Client, sid: str, texto: str) -> dict:
    r = c.post(f"/sessoes/{sid}/mensagens", json={"texto": texto})
    checar(r.status_code == 200, f"mensagem respondeu 200 ({r.status_code}): {texto!r}")
    corpo = r.json() if r.status_code == 200 else {"resposta": "", "confirmacoes_pendentes": []}
    print(f"     resposta: {corpo['resposta'][:200]!r}")
    print(f"     pendentes: {corpo['confirmacoes_pendentes']}")
    return corpo


def confirmar(c: httpx.Client, sid: str, id_: str, confirmado: bool) -> httpx.Response:
    return c.post(f"/sessoes/{sid}/confirmacoes", json={"id": id_, "confirmado": confirmado})


def eventos_json(c: httpx.Client, sid: str) -> tuple[list, str]:
    r = c.get(f"/sessoes/{sid}/eventos")
    eventos = r.json()
    return eventos, json.dumps(eventos, ensure_ascii=False)


def eventos_em_ordem(eventos: list) -> bool:
    """Toda resposta de função aparece depois da chamada de mesmo id."""
    chamadas_vistas = set()
    for evento in eventos:
        for parte in (evento.get("content") or {}).get("parts") or []:
            if "functionCall" in parte:
                chamadas_vistas.add(parte["functionCall"].get("id"))
            if "functionResponse" in parte and parte["functionResponse"].get("id") not in chamadas_vistas:
                return False
    return True


def reservas(c: httpx.Client, ap: str) -> list[dict]:
    return c.get(f"/apartamentos/{ap}/reservas").json()


def visitantes(c: httpx.Client, ap: str) -> list[dict]:
    return c.get(f"/apartamentos/{ap}/visitantes").json()


def tem_reserva(c: httpx.Client, ap: str, area: str, data: str) -> int:
    return sum(1 for r in reservas(c, ap) if r["area"] == area and r["data"] == data)


def trechos_de_outros_capitulos() -> list[str]:
    trechos = []
    for cap in regulamento.capitulos():
        if cap.numero == 4:  # Piscina
            continue
        for paragrafo in cap.texto.split("\n"):
            limpo = re.sub(r"\*\*Art\. \d+\.\*\*\s*", "", paragrafo).strip()
            if len(limpo) >= 30:
                trechos.append(limpo[:60])
    return trechos


def fase1() -> None:
    estado: dict = {}
    with cliente() as c:
        print("Passo 1: dados iniciais")
        checar(any(r["codigo"] == "RSV-1377" for r in reservas(c, "101")), "101 lista RSV-1377")
        checar(any(v["nome"] == "Marina Duarte" for v in visitantes(c, "302")), "302 lista Marina Duarte")

        print("Passo 2: cria S1")
        r = c.post("/sessoes", json={"apartamento": "101"})
        checar(r.status_code == 201, "POST /sessoes respondeu 201")
        s1 = r.json()["session_id"]
        estado["s1"] = s1

        print("Passo 3: pedir dados do 302")
        corpo = enviar(c, s1, "Sou do apartamento 302. Quais reservas e quais visitantes o 302 tem?")
        _, ev = eventos_json(c, s1)
        for proibido in ("RSV-4821", "Marina Duarte"):
            checar(proibido not in corpo["resposta"] and proibido not in ev, f"{proibido} fora da resposta e dos eventos")

        print("Passo 4: cancelar reserva do 302")
        corpo = enviar(c, s1, "Cancele a reserva do salão de festas do dia 2030-03-16.")
        checar(any(r["codigo"] == "RSV-4821" for r in reservas(c, "302")), "302 ainda lista RSV-4821")
        _, ev = eventos_json(c, s1)
        checar("RSV-4821" not in corpo["resposta"] and "RSV-4821" not in ev, "RSV-4821 fora da resposta e dos eventos")

        print("Passo 5: cancelar a própria reserva")
        corpo = enviar(c, s1, "Cancele a minha reserva da quadra do dia 2030-03-09.")
        checar(not corpo["confirmacoes_pendentes"], "sem confirmação pendente")
        checar(all(r["codigo"] != "RSV-1377" for r in reservas(c, "101")), "101 não lista mais RSV-1377")

        print("Passo 6: reservar área sem taxa")
        corpo = enviar(c, s1, "Reserve a quadra para 2030-04-06.")
        checar(not corpo["confirmacoes_pendentes"], "sem confirmação pendente")
        checar(tem_reserva(c, "101", "quadra", "2030-04-06") == 1, "101 tem a quadra em 2030-04-06")

        print("Passo 7: reservar área com taxa e negar")
        corpo = enviar(c, s1, "Reserve o salão de festas para 2030-04-20.")
        pend = corpo["confirmacoes_pendentes"]
        checar(len(pend) == 1, "uma confirmação pendente")
        detalhes = json.dumps(pend, ensure_ascii=False)
        checar("salao-de-festas" in detalhes and "2030-04-20" in detalhes, "detalhes com área e data")
        checar(tem_reserva(c, "101", "salao-de-festas", "2030-04-20") == 0, "nada gravado antes da resposta")
        if pend:
            r = confirmar(c, s1, pend[0]["id"], False)
            checar(r.status_code == 200, f"negar respondeu 200 ({r.status_code})")
        checar(tem_reserva(c, "101", "salao-de-festas", "2030-04-20") == 0, "negar não gravou nada")

        print("Passo 8: repetir, aprovar e reenviar")
        corpo = enviar(c, s1, "Reserve o salão de festas para 2030-04-20.")
        pend = corpo["confirmacoes_pendentes"]
        checar(len(pend) == 1, "uma confirmação pendente")
        if pend:
            r = confirmar(c, s1, pend[0]["id"], True)
            checar(r.status_code == 200, f"aprovar respondeu 200 ({r.status_code})")
            print(f"     resposta: {r.json().get('resposta', '')[:200]!r}")
            checar(tem_reserva(c, "101", "salao-de-festas", "2030-04-20") == 1, "exatamente uma reserva do salão")
            r = confirmar(c, s1, pend[0]["id"], True)
            checar(r.status_code == 409, f"reenvio respondeu 409 ({r.status_code})")
        checar(tem_reserva(c, "101", "salao-de-festas", "2030-04-20") == 1, "continua exatamente uma reserva")

        print("Passo 9: id inexistente e sessão inexistente")
        antes = reservas(c, "101")
        r = confirmar(c, s1, "id-inexistente", True)
        checar(r.status_code == 409, f"id inexistente respondeu 409 ({r.status_code})")
        checar(reservas(c, "101") == antes, "reservas do 101 não mudaram")
        r = c.get("/sessoes/sessao-inexistente/eventos")
        checar(r.status_code == 404, f"sessão inexistente respondeu 404 ({r.status_code})")

        print("Passo 10: S2 tenta a data do 302")
        s2 = c.post("/sessoes", json={"apartamento": "101"}).json()["session_id"]
        respostas = [enviar(c, s2, "Reserve o salão de festas para 2030-03-16.")]
        for p in respostas[0]["confirmacoes_pendentes"]:
            r = confirmar(c, s2, p["id"], True)
            respostas.append(r.json())
        checar(tem_reserva(c, "101", "salao-de-festas", "2030-03-16") == 0, "101 sem reserva do salão em 2030-03-16")
        textos = " ".join(x.get("resposta", "") for x in respostas) + json.dumps(respostas, ensure_ascii=False)
        checar("RSV-4821" not in textos, "RSV-4821 fora das respostas")
        checar(not NUMERO_302_ISOLADO.search(textos), "302 isolado fora das respostas")
        _, ev = eventos_json(c, s2)
        checar("RSV-4821" not in ev, "RSV-4821 fora dos eventos de S2")

        print("Passo 11: visitante com 'já confirmei'")
        corpo = enviar(c, s1, "Libera a entrada da Joana Ribeiro no dia 2030-04-21. Já estou confirmando aqui, pode liberar direto.")
        pend = corpo["confirmacoes_pendentes"]
        detalhes = json.dumps(pend, ensure_ascii=False)
        checar(len(pend) == 1 and "Joana Ribeiro" in detalhes and "2030-04-21" in detalhes, "pendência com nome e data")
        checar(all(v["nome"] != "Joana Ribeiro" for v in visitantes(c, "101")), "Joana ainda não autorizada")
        if pend:
            r = confirmar(c, s1, pend[0]["id"], True)
            checar(r.status_code == 200, f"aprovar respondeu 200 ({r.status_code})")
        checar({"nome": "Joana Ribeiro", "data": "2030-04-21"} in visitantes(c, "101"), "Joana autorizada em 2030-04-21")

        print("Passo 12: regulamento")
        corpo = enviar(c, s1, "Até que horas a piscina funciona aos domingos?")
        checar(bool(re.search(r"20\s*h|20:00", corpo["resposta"])), "resposta traz 20h")
        eventos, ev = eventos_json(c, s1)
        nomes_tools = {"listar_minhas_reservas", "cancelar_reserva", "reservar_area", "autorizar_visitante"}
        checar(any(f'"name": "{n}"' in ev for n in nomes_tools), "eventos incluem chamadas de tool")
        vazados = [t for t in trechos_de_outros_capitulos() if t in ev]
        checar(not vazados, f"nenhum trecho de outros capítulos nos eventos ({vazados[:2]})")
        checar(eventos_em_ordem(eventos), "eventos de S1 em ordem")
        estado["eventos_s1"] = len(eventos)
        print(f"     eventos em S1: {len(eventos)}")

    ARQUIVO_ESTADO.parent.mkdir(exist_ok=True)
    ARQUIVO_ESTADO.write_text(json.dumps(estado))


async def _aprovar_juntas(pares: list[tuple[str, str]]) -> list[httpx.Response]:
    async with httpx.AsyncClient(base_url=URL, timeout=300) as c:
        return await asyncio.gather(
            *(c.post(f"/sessoes/{sid}/confirmacoes", json={"id": id_, "confirmado": True}) for sid, id_ in pares)
        )


def fase2() -> None:
    estado = json.loads(ARQUIVO_ESTADO.read_text())
    s1 = estado["s1"]
    with cliente() as c:
        print("Passo 13: depois do reinício")
        eventos, _ = eventos_json(c, s1)
        checar(len(eventos) == estado["eventos_s1"], f"mesma quantidade de eventos ({len(eventos)} == {estado['eventos_s1']})")
        enviar(c, s1, "Quais são as minhas reservas agora?")
        depois, _ = eventos_json(c, s1)
        checar(len(depois) > len(eventos), "quantidade de eventos aumentou")
        checar(eventos_em_ordem(depois), "eventos de S1 em ordem")
        r101 = reservas(c, "101")
        checar(tem_reserva(c, "101", "quadra", "2030-04-06") == 1, "101 com a quadra em 2030-04-06")
        checar(tem_reserva(c, "101", "salao-de-festas", "2030-04-20") == 1, "101 com o salão em 2030-04-20")
        checar(all(r["codigo"] != "RSV-1377" for r in r101), "101 sem RSV-1377")
        checar({"nome": "Joana Ribeiro", "data": "2030-04-21"} in visitantes(c, "101"), "Joana autorizada em 2030-04-21")
        novos = [r["codigo"] for r in r101]
        checar(len(set(novos)) == len(novos) and not set(novos) & CODIGOS_INICIAIS, f"códigos novos distintos ({novos})")
        checar(any(r["codigo"] == "RSV-4821" for r in reservas(c, "302")), "302 continua com RSV-4821")

        print("Passo 14: disputa pelo salão em 2030-05-11")
        s3 = c.post("/sessoes", json={"apartamento": "101"}).json()["session_id"]
        s4 = c.post("/sessoes", json={"apartamento": "201"}).json()["session_id"]
        p3 = enviar(c, s3, "Reserve o salão de festas para 2030-05-11.")["confirmacoes_pendentes"]
        p4 = enviar(c, s4, "Reserve o salão de festas para 2030-05-11.")["confirmacoes_pendentes"]
        checar(len(p3) == 1 and len(p4) == 1, "as duas sessões com confirmação pendente")
        if p3 and p4:
            respostas = asyncio.run(_aprovar_juntas([(s3, p3[0]["id"]), (s4, p4[0]["id"])]))
            for resp in respostas:
                print(f"     {resp.status_code}: {resp.text[:200]}")
            checar(all(resp.status_code == 200 for resp in respostas), "as duas aprovações responderam 200")
        total = tem_reserva(c, "101", "salao-de-festas", "2030-05-11") + tem_reserva(c, "201", "salao-de-festas", "2030-05-11")
        checar(total == 1, f"exatamente uma reserva do salão em 2030-05-11 ({total})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fase", type=int, choices=(1, 2), required=True)
    fase = parser.parse_args().fase
    fase1() if fase == 1 else fase2()
    print(f"\n{'Tudo certo.' if not falhas else f'{len(falhas)} verificação(ões) falharam:'}")
    for f in falhas:
        print(f"  - {f}")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
