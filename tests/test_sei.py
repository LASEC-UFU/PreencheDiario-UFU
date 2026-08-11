from pathlib import Path
import unittest

from services.sei import (
    FichaDisciplina,
    STATUS_ATUALIZADA,
    STATUS_CRIADA,
    STATUS_SEM_ALTERACAO,
    AutomacaoSEI,
    html_equivalente,
    normalizar_codigo,
)


class ComparacaoHtmlTests(unittest.TestCase):
    def test_codigo_preserva_pontuacao(self):
        self.assertNotEqual(normalizar_codigo("FEELT!MRII"), normalizar_codigo("FEELT_MRII"))

    def test_ignora_formatacao_sem_significado(self):
        atual = '<div class="segunda primeira" data-cke-filler="true">\n<p>Texto</p>\n</div>'
        novo = '<div class="primeira segunda"><p>Texto</p></div>'
        self.assertTrue(html_equivalente(atual, novo))

    def test_detecta_mudanca_de_texto(self):
        self.assertFalse(html_equivalente("<p>Conteúdo antigo</p>", "<p>Conteúdo novo</p>"))

    def test_detecta_mudanca_de_formatacao_relevante(self):
        self.assertFalse(html_equivalente("<p>Texto</p>", "<p><strong>Texto</strong></p>"))


class AutomacaoLoteTests(unittest.TestCase):
    def test_abre_todas_as_pastas_antes_da_busca(self):
        class AutomacaoFalsa(AutomacaoSEI):
            def __init__(self):
                super().__init__(object(), lambda _mensagem: None, timeout=0.1)
                self.estado = "fechadas"
                self.cliques = 0

            def _achar(self, script, *args):
                if "const pastas" in script:
                    return self.estado
                if "abrir.click()" in script:
                    self.cliques += 1
                    self.estado = "abertas"
                    return True
                return None

        automacao = AutomacaoFalsa()
        automacao._abrir_todas_pastas()

        self.assertEqual(automacao.cliques, 1)
        self.assertEqual(automacao.estado, "abertas")

    def test_contabiliza_os_tres_resultados(self):
        class AutomacaoFalsa(AutomacaoSEI):
            def _prevalidar_lote(self, fichas):
                return len(fichas), 0

            def _verificar_resultado_lote(self, fichas):
                return None

            def carregar_ficha(self, ficha):
                return {
                    "A": STATUS_CRIADA,
                    "B": STATUS_ATUALIZADA,
                    "C": STATUS_SEM_ALTERACAO,
                }[ficha.codigo]

        ficha = lambda codigo: FichaDisciplina(
            caminho=Path(f"{codigo}.html"),
            codigo=codigo,
            nome=codigo,
            html="<html></html>",
            conteudo_editor="<p></p>",
        )
        automacao = AutomacaoFalsa(object(), lambda _mensagem: None)
        concluidas, erros = automacao.carregar_lote([ficha("A"), ficha("B"), ficha("C")])

        self.assertEqual(concluidas, 3)
        self.assertEqual(erros, [])
        self.assertEqual(automacao.estatisticas[STATUS_CRIADA], 1)
        self.assertEqual(automacao.estatisticas[STATUS_ATUALIZADA], 1)
        self.assertEqual(automacao.estatisticas[STATUS_SEM_ALTERACAO], 1)

    def test_pre_varredura_interrompe_antes_de_escrever(self):
        class AutomacaoFalsa(AutomacaoSEI):
            def _prevalidar_lote(self, fichas):
                raise RuntimeError("código duplicado")

            def carregar_ficha(self, ficha):
                raise AssertionError("não deveria tentar escrever")

        ficha = FichaDisciplina(
            caminho=Path("A.html"),
            codigo="A",
            nome="Teste",
            html="<html></html>",
            conteudo_editor="<p></p>",
        )
        automacao = AutomacaoFalsa(object(), lambda _mensagem: None)
        concluidas, erros = automacao.carregar_lote([ficha])

        self.assertEqual(concluidas, 0)
        self.assertIn("pré-varredura", erros[0])

    def test_pre_varredura_conta_existentes_e_ausentes(self):
        class AutomacaoFalsa(AutomacaoSEI):
            def __init__(self):
                super().__init__(object(), lambda _mensagem: None)
                self.abriu_pastas = False

            def _abrir_todas_pastas(self):
                self.abriu_pastas = True

            def _localizar_documentos_existentes(self, ficha):
                return [{"element": object()}] if ficha.codigo == "A" else []

        def ficha(codigo):
            return FichaDisciplina(
                caminho=Path(f"{codigo}.html"),
                codigo=codigo,
                nome=codigo,
                html="<html></html>",
                conteudo_editor="<p></p>",
            )

        automacao = AutomacaoFalsa()
        existentes, ausentes = automacao._prevalidar_lote([ficha("A"), ficha("B")])

        self.assertTrue(automacao.abriu_pastas)
        self.assertEqual((existentes, ausentes), (1, 1))

    def test_recusa_documentos_duplicados_no_processo(self):
        class AutomacaoFalsa(AutomacaoSEI):
            def _localizar_documentos_existentes(self, ficha):
                return [{"pontos": 100}, {"pontos": 101}]

        ficha = FichaDisciplina(
            caminho=Path("A.html"),
            codigo="A",
            nome="Teste",
            html="<html></html>",
            conteudo_editor="<p></p>",
        )
        automacao = AutomacaoFalsa(object(), lambda _mensagem: None)

        with self.assertRaisesRegex(RuntimeError, "2 documentos"):
            automacao._localizar_documento_existente(ficha)


if __name__ == "__main__":
    unittest.main()
