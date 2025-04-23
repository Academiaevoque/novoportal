from flask import Blueprint, render_template, request, redirect, url_for, jsonify, send_file, session
from datetime import datetime
import os
import json
import random
import string
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from functools import wraps

painel_bp = Blueprint('painel', __name__, template_folder='templates')

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario' not in session:
            return redirect(url_for('login', next=request.url))
        user_role = session.get('role', 'gerente')
        if user_role != 'admin':
            return render_template('acesso_negado.html', 
                                 message="Acesso não autorizado. Apenas administradores podem acessar essa página. Redirecionando para página principal em 5...4...3...2...1..."), 403
        return f(*args, **kwargs)
    return decorated_function

def buscar_chamado(codigo_chamado):
    arquivo_chamado = os.path.join('chamados', f'{codigo_chamado}.txt')
    if os.path.exists(arquivo_chamado):
        dados = {}
        historico = []
        anexos = []
        mensagens_email = []
        with open(arquivo_chamado, 'r') as file:
            for linha in file:
                if ":" in linha:
                    chave, valor = linha.split(":", 1)
                    chave = chave.strip()
                    valor = valor.strip()
                    if chave == "Histórico":
                        partes = valor.split(" em ")
                        if len(partes) > 1:
                            data_str = partes[1]
                            try:
                                data = datetime.strptime(data_str, '%d/%m/%Y %H:%M')
                                historico.append({
                                    'acao': partes[0] if partes[0] else 'Reaberto',
                                    'data': data.strftime('%d/%m/%Y às %H:%M')
                                })
                            except ValueError:
                                historico.append({'acao': 'Reaberto', 'data': data_str})
                    elif chave == "Anexo":
                        partes = valor.split(" em ")
                        if len(partes) > 1:
                            anexos.append({'nome': partes[0], 'data': partes[1]})
                    elif chave == "Mensagem":
                        partes = valor.split(" em ")
                        if len(partes) > 1:
                            mensagens_email.append({'texto': partes[0], 'data': partes[1]})
                    else:
                        dados[chave] = valor
        chamado = {
            'codigo_chamado': dados.get('Chamado', ''),
            'protocolo': dados.get('Protocolo', ''),
            'prioridade': dados.get('Prioridade', ''),
            'status': dados.get('Status', ''),
            'nome_solicitante': dados.get('Nome do Solicitante', ''),
            'cargo': dados.get('Cargo', ''),
            'unidade': dados.get('Unidade', ''),
            'problema_reportado': dados.get('Problema Reportado', ''),
            'data_abertura': dados.get('Data de Abertura', ''),
            'visita_tecnica': dados.get('Visita Técnica', 'Não requisitada'),
            'descricao': dados.get('Descricao', ''),
            'historico': historico,
            'anexos': anexos,
            'mensagens_email': mensagens_email,
            'email': dados.get('E-mail', '')
        }
        return chamado
    return None

def listar_chamados():
    chamados = []
    for filename in os.listdir("chamados"):
        if filename.endswith(".txt"):
            codigo = filename.split('.')[0]
            chamado = buscar_chamado(codigo)
            if chamado and chamado.get('codigo_chamado', '').strip():
                chamados.append(chamado)
    try:
        chamados = sorted(chamados, 
                          key=lambda x: datetime.strptime(x['data_abertura'], '%d/%m/%Y %H:%M'),
                          reverse=True)
    except Exception as e:
        print(f"Erro ao ordenar chamados: {e}")
    return chamados

def calcular_metricas(pasta):
    metricas = {
        'total_abertos': 0,
        'total_concluidos': 0,
        'tempos_resolucao': [],
        'sla_violados': 0,
        'sla_atendido': 0,
        'tempo_medio': 0
    }
    
    for arquivo in os.listdir(pasta):
        if arquivo.endswith(".txt"):
            caminho = os.path.join(pasta, arquivo)
            with open(caminho, 'r') as f:
                dados = {}
                for linha in f:
                    if ':' in linha:
                        chave, valor = linha.split(':', 1)
                        dados[chave.strip()] = valor.strip()

                is_solicitacao = (pasta == 'solicitacoes')
                
                data_abertura_str = dados.get('Data de Abertura') or dados.get('Data de abertura')
                data_atualizacao_str = dados.get('Data Atualização', data_abertura_str)
                
                if not data_abertura_str:
                    continue
                
                data_abertura = datetime.strptime(data_abertura_str, '%d/%m/%Y %H:%M')
                data_conclusao = datetime.strptime(data_atualizacao_str, '%d/%m/%Y %H:%M') if data_atualizacao_str else data_abertura
                
                status = dados.get('Status', '').lower()
                status_final = (
                    (status in ['concluído', 'concluido']) or 
                    (is_solicitacao and status == 'aprovado')
                )

                if status_final:
                    tempo_resolucao = (data_conclusao - data_abertura).total_seconds() / 3600
                    metricas['tempos_resolucao'].append(tempo_resolucao)
                    metricas['total_concluidos'] += 1
                    
                    limite_sla = 3
                    if tempo_resolucao > limite_sla:
                        metricas['sla_violados'] += 1
                else:
                    metricas['total_abertos'] += 1

    if metricas['total_concluidos'] > 0:
        metricas['sla_atendido'] = round(
            ((metricas['total_concluidos'] - metricas['sla_violados']) / 
             metricas['total_concluidos']) * 100, 1
        )
        metricas['tempo_medio'] = sum(metricas['tempos_resolucao']) / len(metricas['tempos_resolucao'])
        
    return metricas

def listar_solicitacoes_recentes():
    solicitacoes = []
    for filename in os.listdir("solicitacoes"):
        if filename.endswith(".txt"):
            caminho = os.path.join("solicitacoes", filename)
            with open(caminho, 'r') as f:
                dados = {}
                for linha in f:
                    if ':' in linha:
                        chave, valor = linha.split(':', 1)
                        dados[chave.strip()] = valor.strip()
                try:
                    dados['data_abertura'] = datetime.strptime(dados['Data de abertura'], '%d/%m/%Y %H:%M')
                    solicitacoes.append(dados)
                except:
                    continue
    return sorted(solicitacoes, key=lambda x: x['data_abertura'], reverse=True)[:5]

def listar_usuarios():
    usuarios = []
    usuario_dir = os.path.join(os.path.dirname(__file__), 'usuario')
    if os.path.exists(usuario_dir):
        for filename in os.listdir(usuario_dir):
            if filename.endswith('.json'):
                caminho = os.path.join(usuario_dir, filename)
                with open(caminho, 'r') as f:
                    dados = json.load(f)
                    usuario = {
                        'nome': dados.get('nome', ''),
                        'sobrenome': dados.get('sobrenome', ''),
                        'usuario': dados.get('usuario', ''),
                        'email': dados.get('email', ''),
                        'data_criacao': dados.get('data_criacao', ''),
                        'bloqueado': dados.get('bloqueado', False),
                        'role': dados.get('role', 'gerente')
                    }
                    usuarios.append(usuario)
    return sorted(usuarios, key=lambda x: x['data_criacao'], reverse=True)

def gerar_senha(tamanho=7):
    caracteres = string.ascii_letters + string.digits
    return ''.join(random.choice(caracteres) for _ in range(tamanho))

@painel_bp.route('/painel-metricas', methods=['GET', 'POST'])
@admin_required
def painel_metricas():
    metricas_chamados = calcular_metricas('chamados')
    metricas_solicitacoes = calcular_metricas('solicitacoes')
    
    dados_grafico = {
        'labels': ['Abertos', 'Concluídos', 'SLA Violado'],
        'chamados': [
            metricas_chamados['total_abertos'],
            metricas_chamados['total_concluidos'],
            metricas_chamados['sla_violados']
        ],
        'solicitacoes': [
            metricas_solicitacoes['total_abertos'],
            metricas_solicitacoes['total_concluidos'],
            metricas_solicitacoes['sla_violados']
        ]
    }
    
    solicitacoes = listar_solicitacoes_recentes()
    chamados = listar_chamados()
    usuarios = listar_usuarios()
    
    section = request.args.get('section', 'visao-geral')
    
    return render_template('painel/painel.html',
                           metricas_chamados=metricas_chamados,
                           metricas_solicitacoes=metricas_solicitacoes,
                           dados_grafico=dados_grafico,
                           solicitacoes=solicitacoes,
                           chamados=chamados,
                           usuarios=usuarios,
                           now=datetime.now(),
                           section=section)

@painel_bp.route('/criar-usuario', methods=['POST'])
@admin_required
def criar_usuario():
    nome = request.form.get('nome')
    sobrenome = request.form.get('sobrenome')
    usuario = request.form.get('usuario')
    email = request.form.get('email')
    senha = request.form.get('senha')
    nivel_acesso = request.form.get('nivel_acesso')
    alterar_senha_primeiro_acesso = request.form.get('alterar_senha_primeiro_acesso') == 'on'

    usuario_dir = os.path.join(os.path.dirname(__file__), 'usuario')
    if not os.path.exists(usuario_dir):
        os.makedirs(usuario_dir)

    arquivo_usuario = os.path.join(usuario_dir, f"{usuario}.json")
    if os.path.exists(arquivo_usuario):
        return jsonify({'status': 'error', 'message': 'Usuário já existe'}), 400

    dados_usuario = {
        'nome': nome,
        'sobrenome': sobrenome,
        'usuario': usuario,
        'email': email,
        'senha': senha,
        'role': nivel_acesso,
        'data_criacao': datetime.now().strftime('%d/%m/%Y %H:%M'),
        'bloqueado': False,
        'alterar_senha': alterar_senha_primeiro_acesso
    }

    with open(arquivo_usuario, 'w') as f:
        json.dump(dados_usuario, f, indent=4)

    return jsonify({'status': 'success', 'message': 'Usuário criado com sucesso'})

@painel_bp.route('/bloquear-usuario', methods=['POST'])
@admin_required
def bloquear_usuario():
    usuario = request.form.get('usuario')
    usuario_dir = os.path.join(os.path.dirname(__file__), 'usuario')
    arquivo_usuario = os.path.join(usuario_dir, f"{usuario}.json")
    if os.path.exists(arquivo_usuario):
        with open(arquivo_usuario, 'r') as f:
            dados = json.load(f)
        dados['bloqueado'] = not dados.get('bloqueado', False)
        with open(arquivo_usuario, 'w') as f:
            json.dump(dados, f, indent=4)
    return redirect(url_for('painel.painel_metricas', section='usuarios'))

@painel_bp.route('/redefinir-senha', methods=['POST'])
@admin_required
def redefinir_senha():
    usuario = request.form.get('usuario')
    nova_senha = request.form.get('nova_senha') or gerar_senha()
    alterar_senha_primeiro_acesso = request.form.get('alterar_senha_primeiro_acesso') == 'on'
    usuario_dir = os.path.join(os.path.dirname(__file__), 'usuario')
    arquivo_usuario = os.path.join(usuario_dir, f"{usuario}.json")
    if os.path.exists(arquivo_usuario):
        with open(arquivo_usuario, 'r') as f:
            dados = json.load(f)
        dados['senha'] = nova_senha
        dados['alterar_senha'] = alterar_senha_primeiro_acesso
        with open(arquivo_usuario, 'w') as f:
            json.dump(dados, f, indent=4)
    return redirect(url_for('painel.painel_metricas', section='usuarios'))

@painel_bp.route('/alterar-email', methods=['POST'])
@admin_required
def alterar_email():
    usuario = request.form.get('usuario')
    novo_email = request.form.get('novo_email')
    usuario_dir = os.path.join(os.path.dirname(__file__), 'usuario')
    arquivo_usuario = os.path.join(usuario_dir, f"{usuario}.json")
    if os.path.exists(arquivo_usuario):
        with open(arquivo_usuario, 'r') as f:
            dados = json.load(f)
        dados['email'] = novo_email
        with open(arquivo_usuario, 'w') as f:
            json.dump(dados, f, indent=4)
    return redirect(url_for('painel.painel_metricas', section='usuarios'))

@painel_bp.route('/alterar-permissao', methods=['POST'])
@admin_required
def alterar_permissao():
    usuario = request.form.get('usuario')
    novo_nivel = request.form.get('novo_nivel')
    usuario_dir = os.path.join(os.path.dirname(__file__), 'usuario')
    arquivo_usuario = os.path.join(usuario_dir, f"{usuario}.json")
    if os.path.exists(arquivo_usuario):
        with open(arquivo_usuario, 'r') as f:
            dados = json.load(f)
        dados['role'] = novo_nivel
        with open(arquivo_usuario, 'w') as f:
            json.dump(dados, f, indent=4)
        # Redireciona para a mesma página (seção 'permissoes') após a alteração
        return redirect(url_for('painel.painel_metricas', section='permissoes'))
    else:
        return jsonify({'status': 'error', 'message': 'Usuário não encontrado'}), 404

@painel_bp.route('/excluir-usuario', methods=['POST'])
@admin_required
def excluir_usuario():
    usuario = request.form.get('usuario')
    usuario_dir = os.path.join(os.path.dirname(__file__), 'usuario')
    arquivo_usuario = os.path.join(usuario_dir, f"{usuario}.json")
    if os.path.exists(arquivo_usuario):
        os.remove(arquivo_usuario)
        section = 'usuarios' if request.referrer and 'usuarios' in request.referrer else 'bloqueios'
        return redirect(url_for('painel.painel_metricas', section=section))
    else:
        return jsonify({'status': 'error', 'message': 'Usuário não encontrado'}), 404

@painel_bp.route('/buscar-chamado', methods=['GET'])
@admin_required
def buscar_chamado_rota():
    codigo_chamado = request.args.get('codigo')
    if not codigo_chamado:
        return jsonify({'status': 'error', 'message': 'Código do chamado não fornecido'}), 400
    
    chamado = buscar_chamado(codigo_chamado)
    if chamado:
        return jsonify(chamado)
    else:
        return jsonify({'status': 'error', 'message': 'Chamado não encontrado'}), 404

@painel_bp.route('/gerar-relatorio-pdf/<codigo_chamado>', methods=['GET'])
@admin_required
def gerar_relatorio_pdf(codigo_chamado):
    chamado = buscar_chamado(codigo_chamado)
    if not chamado:
        return jsonify({'status': 'error', 'message': 'Chamado não encontrado'}), 404

    pdf_file = f'relatorios/{codigo_chamado}_relatorio.pdf'
    doc = SimpleDocTemplate(pdf_file, pagesize=letter)
    elements = []
    styles = getSampleStyleSheet()

    elements.append(Paragraph(f"Relatório Detalhado - Chamado {chamado['codigo_chamado']}", styles['Heading1']))

    data = [
        ['Código', chamado['codigo_chamado']],
        ['Solicitante', chamado['nome_solicitante']],
        ['E-mail', chamado['email']],
        ['Unidade', chamado['unidade']],
        ['Cargo', chamado['cargo']],
        ['Status', chamado['status']],
        ['Prioridade', chamado['prioridade']],
        ['Data de Abertura', chamado['data_abertura']],
        ['Visita Técnica', chamado['visita_tecnica']],
        ['Problema Reportado', chamado['problema_reportado']],
        ['Descrição', chamado['descricao']]
    ]

    t = Table(data)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 14),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 12),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    elements.append(t)

    if chamado['historico']:
        elements.append(Paragraph("Histórico", styles['Heading2']))
        historico_data = [['Data', 'Ação']]
        for h in chamado['historico']:
            historico_data.append([h['data'], h['acao']])
        t_historico = Table(historico_data)
        t_historico.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 14),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 12),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        elements.append(t_historico)

    if chamado['anexos']:
        elements.append(Paragraph("Anexos", styles['Heading2']))
        anexos_data = [['Nome', 'Data']]
        for a in chamado['anexos']:
            anexos_data.append([a['nome'], a['data']])
        t_anexos = Table(anexos_data)
        t_anexos.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 14),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 12),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        elements.append(t_anexos)

    if chamado['mensagens_email']:
        elements.append(Paragraph("Mensagens de E-mail", styles['Heading2']))
        mensagens_data = [['Data', 'Mensagem']]
        for m in chamado['mensagens_email']:
            mensagens_data.append([m['data'], m['texto']])
        t_mensagens = Table(mensagens_data)
        t_mensagens.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 14),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 12),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        elements.append(t_mensagens)

    doc.build(elements)
    return send_file(pdf_file, as_attachment=True)