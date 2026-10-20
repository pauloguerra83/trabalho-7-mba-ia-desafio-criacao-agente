"""Tool de leitura do regulamento, um capítulo por vez."""

from .. import regulamento


def ler_capitulo(numero: int) -> dict:
    """Lê um único capítulo do regulamento interno.

    Args:
        numero: número do capítulo, conforme o índice (1 a 14).

    Returns:
        O título e o texto completo daquele capítulo.
    """
    capitulo = regulamento.capitulo(numero)
    if capitulo is None:
        total = len(regulamento.capitulos())
        return {"erro": f"Capítulo inexistente. Use um número de 1 a {total}."}
    return {"capitulo": capitulo.nome, "texto": capitulo.texto}
