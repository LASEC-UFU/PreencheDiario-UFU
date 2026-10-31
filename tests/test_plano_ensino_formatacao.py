from pathlib import Path
from zipfile import ZipFile

from bs4 import BeautifulSoup

from features.plano_ensino import campo_vazio, mesclar_campos_no_modelo


def test_secoes_e_formatacao_sei_com_tabelas_de_objetivos_separadas(tmp_path: Path):
    docx = tmp_path / "modelo.docx"
    with ZipFile(docx, "w") as arquivo:
        arquivo.writestr("word/document.xml", """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
          <w:p><w:r><w:t>AVALIAÇÃO</w:t></w:r></w:p>
          <w:p><w:r><w:t>Serão realizadas </w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>duas provas teóricas</w:t></w:r><w:r><w:t>.</w:t></w:r></w:p>
          <w:p><w:r><w:t>BIBLIOGRAFIA</w:t></w:r></w:p>
        </w:body></w:document>""")
    campos = campo_vazio()
    campos.update({
        "ementa": "Ementa cadastrada.", "justificativa": "Justificativa cadastrada.",
        "objetivo_geral": "Objetivo cadastrado.", "objetivos_especificos": "Ao final:\nAnalisar instrumentos;\nProjetar sistemas.",
        "programa_teorico": "1. Sistemas\n1.1. Conceitos", "programa_pratico": "1. Prática",
        "avaliacao_geral": "Serão realizadas duas provas teóricas.",
        "avaliacao_provas": "Quanto as provas vão seguir, o seguinte, calendário:\nDia 20 de outubro de 2026: PRIMEIRA AVALIAÇÃO PRÁTICA no valor de 10 pontos.",
        "horario_atendimento": "Segunda-feira às 13h.", "atividade_recuperacao": "Prova de recuperação.",
        "atendimento_especial": "Atendimento personalizado.",
        "cronograma_teorico": [{"data": "01/09/2026", "atividade": "Aula teórica"}],
        "cronograma_pratico": [{"data": "02/09/2026", "atividade": "Aula prática"}],
    })
    cabecalho = lambda n, nome: f"<table><tr><td>{n}.</td><td>{nome}</td></tr></table>"
    modelo = ("<html><body><table><tr><td>Componente Curricular Unidade Ofertante Código Observações</td></tr></table>"
              + cabecalho(2, "EMENTA") + cabecalho(3, "JUSTIFICATIVA") + cabecalho(4, "OBJETIVO")
              + "<table><tr><td>Objetivo Geral:</td></tr><tr><td></td></tr></table>"
              + "<table><tr><td>Objetivos Específicos:</td></tr><tr><td></td></tr></table>"
              + cabecalho(5, "PROGRAMA") + cabecalho(6, "METODOLOGIA") + cabecalho(7, "AVALIAÇÃO")
              + "<p>BIBLIOGRAFIA BÁSICA</p><p>BIBLIOGRAFIA COMPLEMENTAR</p><p>9. APROVAÇÃO</p></body></html>")
    resultado, _ = mesclar_campos_no_modelo(modelo, campos, docx)
    soup = BeautifulSoup(resultado, "html.parser")
    texto = soup.get_text(" ", strip=True)
    assert texto.count("Objetivo Geral:") == 1
    assert texto.count("Objetivos Específicos:") == 1
    assert not any("Objetivo Geral:" in tabela.get_text() or "Objetivos Específicos:" in tabela.get_text()
                   for tabela in soup.find_all("table"))
    assert texto.index("METODOLOGIA") < texto.index("Cronograma das atividades curriculares:") < texto.index("AVALIAÇÃO")
    tabelas_cronograma = [t for t in soup.find_all("table") if t.find("th", string="Data")]
    assert len(tabelas_cronograma) == 2
    assert [[col.get("width") for col in t.select("colgroup col")] for t in tabelas_cronograma] == [["15%", "85%"]] * 2
    assert soup.find("strong", string="duas provas teóricas") is not None
    assert soup.find("strong", string="PRIMEIRA AVALIAÇÃO PRÁTICA") is not None
    for rotulo in ("Horário de Atendimento:", "Atividade de Recuperação:", "Atendimento Especial:"):
        assert soup.find("strong", string=rotulo) is not None
    assert "Dia 20 de outubro de 2026" in texto
