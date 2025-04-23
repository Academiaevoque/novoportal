from flask import Blueprint, render_template, jsonify, request
import os
from datetime import datetime
import chardet

setorcompras_bp = Blueprint('setorcompras', __name__)
PASTA_SOLICITACOES = 'solicitacoes'

def listar_solicitacoes_compras():
    """
    Lista todas as solicitações de compra da pasta 'solicitacoes'.
    Retorna uma lista de dicionários com os dados das solicitações.
    """
    solicitacoes = []

    if not os.path.exists(PASTA_SOLICITACOES):
        os.makedirs(PASTA_SOLICITACOES, exist_ok=True)
        print(f"Pasta {PASTA_SOLICITACOES} criada em {os.getcwd()}")
        return solicitacoes

    try:
        arquivos = [f for f in os.listdir(PASTA_SOLICITACOES) if f.endswith('.txt')]
        print(f"Arquivos encontrados em {PASTA_SOLICITACOES}: {arquivos}")
    except Exception as e:
        print(f"Erro ao listar arquivos em {PASTA_SOLICITACOES}: {e}")
        return solicitacoes

    if not arquivos:
        print("Nenhum arquivo .txt encontrado.")
        return solicitacoes

    for filename in arquivos:
        caminho_arquivo = os.path.join(PASTA_SOLICITACOES, filename)
        try:
            with open(caminho_arquivo, 'rb') as file_raw:
                raw_data = file_raw.read()
                result = chardet.detect(raw_data)
                encoding = result['encoding'] or 'utf-8'

            try:
                texto = raw_data.decode(encoding)
            except UnicodeDecodeError:
                print(f"Erro ao decodificar {filename} com encoding {encoding}")
                continue

            linhas = texto.splitlines()
            dados = {}
            for linha in linhas:
                linha = linha.strip()
                if ":" in linha:
                    chave, valor = linha.split(":", 1)
                    chave_normalizada = (
                        chave.strip().lower().replace(" ", "_")
                        .replace("á", "a").replace("ã", "a").replace("â", "a")
                        .replace("é", "e").replace("ê", "e")
                        .replace("í", "i").replace("ó", "o").replace("ô", "o")
                        .replace("ú", "u").replace("ç", "c")
                    )
                    dados[chave_normalizada] = valor.strip()

            if dados:
                dados['codigo_solicitacao'] = (
                    dados.get('codigo') or
                    dados.get('código') or
                    filename[:-4]
                )
                dados['nome_solicitante'] = dados.get('nome_do_solicitante', 'Desconhecido')
                dados['item'] = dados.get('item', 'Não especificado')
                dados['produto'] = dados['item']
                dados['filial'] = dados.get('filial', 'Não especificada')
                dados['email'] = dados.get('e-mail', 'Não informado')
                dados['status'] = dados.get('status', 'Aguardando')

                if 'data' in dados:
                    try:
                        dados['data_abertura'] = datetime.strptime(dados['data'], '%d/%m/%Y %H:%M')
                    except ValueError:
                        print(f"Formato de data inválido em {filename}: {dados['data']}")
                        dados['data_abertura'] = None
                else:
                    dados['data_abertura'] = None

                solicitacoes.append(dados)
                print(f"✅ Solicitação processada de {filename}: {dados}")
            else:
                print(f"⚠️ Arquivo {filename} não contém dados válidos")

        except Exception as e:
            print(f"Erro ao ler o arquivo {caminho_arquivo}: {e}")

    solicitacoes.sort(key=lambda x: x.get('data_abertura') or datetime.min, reverse=True)
    print(f"Total de solicitações processadas: {len(solicitacoes)}")
    return solicitacoes

@setorcompras_bp.route('/setor-compras')
def setor_compras():
    try:
        solicitacoes = listar_solicitacoes_compras()

        metricas_solicitacoes = {
            'total_abertas': sum(1 for s in solicitacoes if s.get('status', '').lower() in ['aguardando', 'em análise']),
            'total_concluidas': sum(1 for s in solicitacoes if s.get('status', '').lower() == 'aprovado'),
            'total_aprovadas': sum(1 for s in solicitacoes if s.get('status', '').lower() == 'aprovado'),
            'tempo_medio': 2.5,
            'sla_atendido': 85,
            'sla_violadas': 3
        }

        dados_grafico = {
            'labels': ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun'],
            'solicitacoes': [10, 15, 13, 20, 18, 25]
        }

        print(f"🔍 Renderizando template com {len(solicitacoes)} solicitações.")
        return render_template(
            'painel/setorcompras.html',
            section='solicitacao-compras',
            solicitacoes=solicitacoes,
            metricas_solicitacoes=metricas_solicitacoes,
            dados_grafico=dados_grafico,
            now=datetime.now()
        )
    except Exception as e:
        print(f"Erro na rota /setor-compras: {e}")
        return jsonify({'error': 'Erro interno do servidor'}), 500

@setorcompras_bp.route('/buscar-solicitacao', methods=['GET'])
def buscar_solicitacao():
    try:
        codigo = request.args.get('codigo')
        if not codigo:
            return jsonify({'error': 'Código não fornecido'}), 400

        solicitacoes = listar_solicitacoes_compras()
        for solicitacao in solicitacoes:
            if solicitacao.get('codigo_solicitacao') == codigo:
                return jsonify(solicitacao)

        return jsonify({'error': 'Solicitação não encontrada'}), 404
    except Exception as e:
        print(f"Erro na rota /buscar-solicitacao: {e}")
        return jsonify({'error': 'Erro interno ao buscar solicitação'}), 500

@setorcompras_bp.route('/atualizar-status-solicitacao', methods=['POST'])
def atualizar_status_solicitacao():
    try:
        codigo = request.form.get('codigo_solicitacao')
        novo_status = request.form.get('novo_status')
        if not codigo or not novo_status:
            return jsonify({'error': 'Código ou status não fornecido'}), 400

        caminho_arquivo = os.path.join(PASTA_SOLICITACOES, f"{codigo}.txt")
        if os.path.exists(caminho_arquivo):
            with open(caminho_arquivo, 'r', encoding='utf-8') as file:
                linhas = file.readlines()
            with open(caminho_arquivo, 'w', encoding='utf-8') as file:
                for linha in linhas:
                    if linha.lower().startswith("status:"):
                        file.write(f"Status: {novo_status}\n")
                    else:
                        file.write(linha)
            print(f"✅ Status atualizado para {novo_status} no arquivo {caminho_arquivo}")
            return jsonify({'status': 'success', 'message': 'Status atualizado com sucesso', 'novo_status': novo_status})
        else:
            return jsonify({'error': 'Solicitação não encontrada'}), 404
    except Exception as e:
        print(f"Erro na rota /atualizar-status-solicitacao: {e}")
        return jsonify({'error': 'Erro interno ao atualizar status'}), 500