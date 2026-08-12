from pathlib import Path
import unittest

from services.sei import (
    DocumentoArvore,
    FichaDisciplina,
    STATUS_ATUALIZADA,
    STATUS_CRIADA,
    STATUS_SEM_ALTERACAO,
    AutomacaoSEI,
    extrair_codigo_sei,
    extrair_data,
    extrair_resumo,
    extrair_tipo_numero,
    formatar_relatorio_arvore,
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


class ExtracaoTextoTests(unittest.TestCase):
    def test_extrai_codigo_sei_do_rodape(self):
        texto = "informando o código verificador 6908398 e o código CRC 73E9C798"
        self.assertEqual(extrair_codigo_sei(texto), "6908398")

    def test_extrai_codigo_sei_via_referencia(self):
        self.assertEqual(
            extrair_codigo_sei("Referência: Processo nº 23117.058216/2025-19 SEI nº 6908398"),
            "6908398",
        )

    def test_extrai_data_por_extenso(self):
        texto = "Resolução do Conselho de Graduação, de 18 de outubro de 2019, que regulamenta..."
        self.assertEqual(extrair_data(texto), "18 de outubro de 2019")

    def test_extrai_data_curta_quando_nao_ha_data_por_extenso(self):
        self.assertEqual(extrair_data("assinado eletronicamente em 16/12/2025, às 10:03"), "16/12/2025")

    def test_extrai_tipo_numero_da_primeira_linha_forte(self):
        texto = "UNIVERSIDADE FEDERAL DE UBERLÂNDIA\nPARECER Nº 105/2025/CONGRAD\nSenhor Presidente,"
        self.assertEqual(extrair_tipo_numero(texto), "PARECER Nº 105/2025/CONGRAD")

    def test_extrai_tipo_numero_vazio_sem_linha_forte(self):
        self.assertEqual(extrair_tipo_numero("apenas texto normal em minúsculas"), "")

    def test_extrai_resumo_apos_o_titulo(self):
        texto = (
            "PARECER Nº 105/2025/CONGRAD\n"
            "Senhor Presidente,\n"
            "Em atenção à designação recebida pelo Despacho, apresento a seguir a análise."
        )
        resumo = extrair_resumo(texto, "PARECER Nº 105/2025/CONGRAD")
        self.assertTrue(resumo.startswith("Em atenção"))

    def test_extrai_resumo_trunca_no_limite(self):
        paragrafo = "X" * 300
        texto = f"TÍTULO\n{paragrafo}"
        resumo = extrair_resumo(texto, "TÍTULO", limite=50)
        self.assertEqual(resumo, "X" * 50 + "…")


class FormatarRelatorioArvoreTests(unittest.TestCase):
    def test_formata_com_todos_os_campos(self):
        documentos = [
            DocumentoArvore(
                ordem=1,
                nome_arvore="Relatório nº 11",
                codigo_sei="6616433",
                tipo_numero="Relatório nº 11/2025/DIREN",
                data="25/08/2025",
                resumo="trata da revisão",
            ),
            DocumentoArvore(
                ordem=2,
                nome_arvore="Portaria de Pessoal",
                codigo_sei="",
                tipo_numero="",
                data="",
                resumo="",
            ),
        ]
        self.assertEqual(
            formatar_relatorio_arvore(documentos),
            "1. Relatório nº 11/2025/DIREN (6616433), de 25/08/2025 – trata da revisão\n"
            "2. Portaria de Pessoal",
        )


class _SwitchToFalso:
    def default_content(self):
        pass

    def window(self, _handle):
        pass


class _DriverFalso:
    def __init__(self):
        self.window_handles = ["janela1"]
        self.current_window_handle = "janela1"
        self.switch_to = _SwitchToFalso()


class ExtrairArvoreProcessoTests(unittest.TestCase):
    def test_percorre_nos_na_ordem_e_monta_inventario(self):
        conteudos = {
            "Relatório nº 11": (
                "RELATÓRIO Nº 11/2025/DIREN\n"
                "Que trata da revisão e atualização das resoluções, de 25 de agosto de 2025."
            ),
            "Portaria de Pessoal": (
                "PORTARIA DE PESSOAL Nº 3952\n"
                "Institui comissão responsável, de 12 de junho de 2025."
            ),
        }

        class AutomacaoFalsa(AutomacaoSEI):
            def __init__(self):
                super().__init__(_DriverFalso(), lambda _m: None, timeout=0.1)
                self.abriu_pastas = False
                self.selecionado_atual = None

            def _abrir_todas_pastas(self):
                self.abriu_pastas = True

            def _listar_nos_documento(self):
                return [
                    {"texto": "Relatório nº 11", "href": "a", "id": ""},
                    {"texto": "Portaria de Pessoal", "href": "b", "id": ""},
                ]

            def _selecionar_no_arvore(self, href, id_):
                nomes = {"a": "Relatório nº 11", "b": "Portaria de Pessoal"}
                self.selecionado_atual = nomes[href]
                return True

            def _ler_texto_documento_atual(self):
                return {"texto": conteudos[self.selecionado_atual]}

        automacao = AutomacaoFalsa()
        documentos = automacao.extrair_arvore_processo()

        self.assertTrue(automacao.abriu_pastas)
        self.assertEqual([d.ordem for d in documentos], [1, 2])
        self.assertEqual(documentos[0].tipo_numero, "RELATÓRIO Nº 11/2025/DIREN")
        self.assertEqual(documentos[0].data, "25 de agosto de 2025")
        self.assertEqual(documentos[1].tipo_numero, "PORTARIA DE PESSOAL Nº 3952")

    def test_continua_apos_falha_de_leitura_em_um_documento(self):
        class AutomacaoFalsa(AutomacaoSEI):
            def __init__(self):
                super().__init__(_DriverFalso(), lambda _m: None, timeout=0.1)

            def _abrir_todas_pastas(self):
                pass

            def _listar_nos_documento(self):
                return [{"texto": "Doc bloqueado", "href": "a", "id": ""}]

            def _selecionar_no_arvore(self, href, id_):
                raise RuntimeError("documento bloqueado")

        automacao = AutomacaoFalsa()
        documentos = automacao.extrair_arvore_processo()

        self.assertEqual(len(documentos), 1)
        self.assertEqual(documentos[0].nome_arvore, "Doc bloqueado")
        self.assertEqual(documentos[0].codigo_sei, "")

    def test_continua_quando_no_nao_e_relocalizado_na_arvore(self):
        class AutomacaoFalsa(AutomacaoSEI):
            def __init__(self):
                super().__init__(_DriverFalso(), lambda _m: None, timeout=0.1)

            def _abrir_todas_pastas(self):
                pass

            def _listar_nos_documento(self):
                return [{"texto": "Doc sumido", "href": "a", "id": ""}]

            def _selecionar_no_arvore(self, href, id_):
                return False

        automacao = AutomacaoFalsa()
        documentos = automacao.extrair_arvore_processo()

        self.assertEqual(len(documentos), 1)
        self.assertEqual(documentos[0].nome_arvore, "Doc sumido")
        self.assertEqual(documentos[0].codigo_sei, "")

    def test_reporta_progresso_por_documento(self):
        class AutomacaoFalsa(AutomacaoSEI):
            def __init__(self):
                super().__init__(_DriverFalso(), lambda _m: None, timeout=0.1)

            def _abrir_todas_pastas(self):
                pass

            def _listar_nos_documento(self):
                return [
                    {"texto": "Doc A", "href": "a", "id": ""},
                    {"texto": "Doc B", "href": "b", "id": ""},
                ]

            def _selecionar_no_arvore(self, href, id_):
                return True

            def _ler_texto_documento_atual(self):
                return {"texto": "TÍTULO\nresumo qualquer com mais de quarenta caracteres aqui"}

        chamadas = []
        automacao = AutomacaoFalsa()
        automacao.extrair_arvore_processo(
            progresso=lambda indice, total, nome: chamadas.append((indice, total, nome))
        )

        self.assertEqual(chamadas, [(1, 2, "Doc A"), (2, 2, "Doc B")])


if __name__ == "__main__":
    unittest.main()
