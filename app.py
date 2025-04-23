from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_file
import os
import math
import smtplib
import random
import string
import sqlite3
import json
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from email.mime.application import MIMEApplication
from email.mime.base import MIMEBase
from email import encoders
from functools import wraps
from urllib.parse import urlparse, urljoin
from setorcompras import setorcompras_bp
from monitor import monitor_bp

app = Flask(__name__)
app.secret_key = "supersecretkey"
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)
app.register_blueprint(setorcompras_bp)
app.register_blueprint(monitor_bp)
USUARIOS_DIR = os.path.join(os.path.dirname(__file__), 'usuario')

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario' not in session:
            return redirect(url_for('login', next=request.url))

        current_route = request.endpoint
        user_role = session.get('role', 'gerente')

        # Permissões para cada role
        PERMISSIONS = {
            'gerente': {
                'allowed_routes': [
                    'index', 'abrir_chamado', 'gerar_relatorio', 'ver_meus_chamados', 
                    'login', 'logout', 'verificar_acesso', 'compras_holding'
                ]
            },
            'gerente_regional': {
                'allowed_routes': [
                    'index', 'abrir_chamado', 'gerar_relatorio', 'ver_meus_chamados', 
                    'solicitacao_compra', 'login', 'logout', 'verificar_acesso', 'compras_holding'
                ]
            },
            'admin': {
                'allowed_routes': [
                    'index', 'abrir_chamado', 'gerar_relatorio', 'ver_meus_chamados', 
                    'solicitacao_compra', 'painel_metricas', 'admin_painel', 'administrar_chamados', 
                    'gerenciar_usuarios', 'login', 'logout', 'verificar_acesso', 'criar_usuario', 
                    'atualizar_status', 'excluir', 'enviar_ticket', 'atualizar_status_solicitacao', 
                    'excluir_solicitacao', 'enviar_ticket_solicitacao', 'compras_holding', 'listar_chamados', 
                    'bloquear_usuario', 'redefinir_senha', 'alterar_email', 'alterar_permissao', 
                    'excluir_usuario', 'buscar_chamado_rota', 'gerar_relatorio_pdf'
                ]
            }
        }

        # Verifica se o usuário tem permissão para acessar a rota atual
        if user_role in ['gerente', 'gerente_regional']:
            if current_route not in PERMISSIONS[user_role]['allowed_routes']:
                return render_template('acesso_negado.html', 
                                       message="Acesso não autorizado. O usuário não tem permissão para acessar essa página. Redirecionando para página principal em 5...4...3...2...1..."), 403
        elif user_role == 'admin':
            # Admin tem acesso total, não precisa de verificação adicional
            return f(*args, **kwargs)
        else:
            return render_template('acesso_negado.html', 
                                  message="Role inválido. Redirecionando para a página de login..."), 403

        return f(*args, **kwargs)
    return decorated_function

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

def is_safe_url(target):
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def verificar_usuario(username, password):
    arquivo_usuario = os.path.join(USUARIOS_DIR, f"{username}.json")
    if os.path.exists(arquivo_usuario):
        with open(arquivo_usuario, 'r') as f:
            dados = json.load(f)
            if dados.get('senha') == password:
                return dados
    return None

def gerar_senha(tamanho=7):
    caracteres = string.ascii_letters + string.digits
    return ''.join(random.choice(caracteres) for _ in range(tamanho))

def verificar_chamado_anterior(email_solicitante, problema_reportado, dias=7):
    data_limite = datetime.now() - timedelta(days=dias)
    for filename in os.listdir("chamados"):
        if filename.endswith(".txt"):
            codigo = filename.split('.')[0]
            chamado = buscar_chamado(codigo)
            if chamado and chamado.get('nome_solicitante') and chamado.get('problema_reportado'):
                try:
                    data_abertura = datetime.strptime(chamado['data_abertura'], '%d/%m/%Y %H:%M')
                    if (chamado['email'] == email_solicitante and 
                        chamado['problema_reportado'] == problema_reportado and 
                        data_abertura > data_limite and 
                        chamado['status'].lower() in ['concluído', 'concluido']):
                        return chamado, codigo
                except ValueError:
                    continue
    return None, None

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

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
EMAIL = "unidadegoias036@gmail.com"
EMAIL_PASSWORD = "ihymnbwykqscvfgq"
DESTINATARIO = "emerson.silva@academiaevoque.com.br"

unidades_dict = {
    "1": "GUILHERMINA - 1",
    "4": "DIADEMA - 4",
    "5": "SHOPPING MAUÁ - 5",
    "6": "RIBEIRÃO PIRES - 6",
    "7": "HOMERO THON - 7",
    "8": "AV. PORTUGAL - 8",
    "9": "VALO VELHO - 9",
    "10": "ITAMARATI - 10",
    "11": "AV. RIO BRANCO - 11",
    "12": "PEREIRA BARRETO - 12",
    "13": "GIOVANNI BREDA - 13",
    "14": "BOQUEIRÃO - 14",
    "15": "PARQUE DO CARMO - 15",
    "16": "ZAIRA - 16",
    "17": "HELIOPOLIS - 17",
    "19": "PIMENTAS - 19",
    "20": "GUAIANASES - 20",
    "21": "AV. GOIÁS - 21",
    "22": "BOM CLIMA - 22",
    "23": "CAMPO GRANDE - 23",
    "24": "JAGUARÉ - 24",
    "25": "ITAQUERA - 25",
    "26": "EXTREMA - 26",
    "27": "MOGI DAS CRUZES - 27",
    "28": "ALAMEDA - 28",
    "29": "JARDIM GOIAS - 29",
    "30": "PASSEIO DAS AGUAS - 30",
    "31": "SÃO VICENTE - 31",
    "32": "CAMILÓPOLIS - 32",
    "33": "INDAIATUBA - 33",
    "34": "VILA PRUDENTE - 34",
    "35": "LARANJAL PAULISTA - 35",
    "36": "SACOMÃ - 36",
    "37": "VILA NOVA - 37",
    "38": "SAPOPEMBA - 38",
    "39": "POÁ - 39",
    "40": "CURITIBA - 40",
    "41": "FRANCA - 41",
    "130": "JARDIM SÃO PAULO - 130",
    "131": "CARAPICUIBA - 131",
    "042": "ITAQUERA 2 - 042",
}

os.makedirs("chamados", exist_ok=True)
os.makedirs("relatorios", exist_ok=True)
os.makedirs("solicitacoes", exist_ok=True)
os.makedirs("solicitacoes_ti", exist_ok=True)

def verificar_permissao(role, pagina):
    if not role:
        return False
    if role in PERMISSIONS:
        return pagina in PERMISSIONS[role]['allowed_routes']
    return False

def gerar_codigo_sequencial(pasta="chamados", prefixo="EVQ-"):
    seq = 1
    if os.path.exists(pasta):
        for filename in os.listdir(pasta):
            if filename.startswith(prefixo) and filename.endswith(".txt"):
                try:
                    numero = int(filename[len(prefixo):-4])
                    seq = max(seq, numero + 1)
                except ValueError:
                    pass
    return f"{prefixo}{seq:04d}"

def gerar_protocolo():
    return str(random.randint(100000, 999999)) + '-' + str(random.randint(1, 9))

def enviar_email(assunto, corpo_email, destinatario):
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL
        msg['To'] = destinatario
        msg['Subject'] = assunto
        msg.attach(MIMEText(corpo_email, 'plain'))
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL, EMAIL_PASSWORD)
        server.sendmail(EMAIL, destinatario, msg.as_string())
        server.quit()
        print(f"Email enviado para {destinatario}")
    except Exception as e:
        print(f"Erro ao enviar o e-mail: {e}")

def enviar_email_confirmacao_chamado(nome_solicitante, codigo_chamado, protocolo_chamado,
                                    prioridade, cargo, email_solicitante, telefone,
                                    problema_reportado, descricao, visita_tecnica, unidade):
    try:
        assunto = f"Confirmação de Chamado - {codigo_chamado}"
        corpo_email = f"""
Seu chamado foi registrado com sucesso! Aqui estão os detalhes:

Chamado: {codigo_chamado}
Protocolo: {protocolo_chamado}
Prioridade: {prioridade}
Nome do solicitante: {nome_solicitante}
Cargo: {cargo}
Unidade: {unidade}
E-mail: {email_solicitante}
Telefone: {telefone}
Problema reportado: {problema_reportado}
Descrição: {descricao}
Visita técnica: {visita_tecnica if visita_tecnica else 'Não requisitada'}

⚠️ Caso precise acompanhar o status do chamado, utilize o código acima.

Atenciosamente,
Suporte Evoque!

Por favor, não responda este e-mail, essa é uma mensagem automática!
        """
        enviar_email(assunto, corpo_email, email_solicitante)
        enviar_email(assunto, corpo_email, DESTINATARIO)
    except Exception as e:
        print(f"Erro ao enviar o e-mail de confirmação: {e}")

def enviar_email_confirmacao_compra(filial, nome_solicitante, descricao_item, quantidade, motivo,
                                   email_solicitante, local_entrega, link_produto, codigo, protocolo):
    try:
        assunto = f"Confirmação de Solicitação de Compra - {codigo}"
        corpo_email = f"""
Sua solicitação de compra foi registrada com sucesso! Aqui estão os detalhes:

Código: {codigo}
Protocolo: {protocolo}
Filial: {filial}
Nome do Solicitante: {nome_solicitante}
Item: {descricao_item}
Quantidade: {quantidade}
Motivo: {motivo}
E-mail: {email_solicitante}
Local de Entrega: {local_entrega}
Link do Produto: {link_produto}

⚠️ Utilize o código acima para acompanhar o status da solicitação.

Atenciosamente,
Suporte Evoque!

Por favor, não responda este e-mail, essa é uma mensagem automática!
        """
        enviar_email(assunto, corpo_email, email_solicitante)
    except Exception as e:
        print(f"Erro ao enviar o e-mail de confirmação de compra: {e}")

def enviar_email_compras(filial, nome_solicitante, descricao_item, quantidade, motivo,
                        email_solicitante, local_entrega, link_produto, codigo_solicitacao, protocolo, anexo):
    try:
        assunto = f"Solicitação de Compra - {codigo_solicitacao}"
        corpo_email = f"""
Nova solicitação de compra registrada:

Código: {codigo_solicitacao}
Protocolo: {protocolo}
Filial: {filial}
Nome do Solicitante: {nome_solicitante}
Item: {descricao_item}
Quantidade: {quantidade}
Motivo: {motivo}
E-mail: {email_solicitante}
Local de Entrega: {local_entrega}
Link do Produto: {link_produto}

Por favor, analise a solicitação.

Atenciosamente,
Suporte Evoque
        """
        msg = MIMEMultipart()
        msg['From'] = EMAIL
        msg['To'] = DESTINATARIO
        msg['Subject'] = assunto
        msg.attach(MIMEText(corpo_email, 'plain'))

        if anexo and anexo.filename:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(anexo.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename="{anexo.filename}"')
            msg.attach(part)

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"E-mail de compras enviado para {DESTINATARIO}")
        return True
    except Exception as e:
        print(f"Erro ao enviar e-mail de compras: {e}")
        return False

def gerar_pdf(codigo_chamado, nome_solicitante, descricao, problema_reportado, prioridade, data_abertura, visita_tecnica, status, cargo):
    file_path = f"relatorios/{codigo_chamado}.pdf"
    image_path = os.path.join("static", "images", "logo.png")
    c = canvas.Canvas(file_path, pagesize=letter)
    c.drawImage(image_path, 100, 600, width=400, height=100)
    c.setFont("Helvetica", 8)
    c.drawString(100, 570, "Olá! Sua cópia foi gerada com sucesso, consulte os dados do seu chamado abaixo.")
    y_position = 550
    c.drawString(100, y_position, f"CHAMADO: {codigo_chamado}")
    y_position -= 15
    c.drawString(100, y_position, f"PROTOCOLO: {gerar_protocolo()}")
    y_position -= 15
    c.drawString(100, y_position, f"NOME DO SOLICITANTE: {nome_solicitante}")
    y_position -= 15
    c.drawString(100, y_position, f"CARGO: {cargo}")
    y_position -= 15
    c.drawString(100, y_position, f"DESCRIÇÃO: {descricao}")
    y_position -= 15
    c.drawString(100, y_position, f"PROBLEMA REPORTADO: {problema_reportado}")
    y_position -= 15
    c.drawString(100, y_position, f"PRIORIDADE: {prioridade}")
    y_position -= 15
    c.drawString(100, y_position, f"DATA DE ABERTURA: {data_abertura}")
    y_position -= 15
    c.drawString(100, y_position, f"VISITA TÉCNICA: {visita_tecnica if visita_tecnica else 'Não requisitada'}")
    y_position -= 15
    c.drawString(100, y_position, f"STATUS: {status}")
    c.save()
    return file_path

def contar_chamados():
    total_concluidos = 0
    total_andamento = 0
    total_pendentes = 0
    for filename in os.listdir("chamados"):
        if filename.endswith(".txt"):
            codigo = filename.split('.')[0]
            chamado = buscar_chamado(codigo)
            if chamado:
                status = chamado['status'].strip().lower()
                if status == "concluído":
                    total_concluidos += 1
                elif status == "em andamento":
                    total_andamento += 1
                else:
                    total_pendentes += 1
    return total_concluidos, total_andamento, total_pendentes

def buscar_chamados_recentes():
    chamados = []
    for filename in os.listdir("chamados"):
        if filename.endswith(".txt"):
            codigo_chamado = filename.split('.')[0]
            chamado = buscar_chamado(codigo_chamado)
            if chamado:
                try:
                    if chamado['data_abertura']:
                        chamado['data_abertura'] = datetime.strptime(chamado['data_abertura'], '%d/%m/%Y %H:%M')
                        chamados.append(chamado)
                except ValueError:
                    print(f"Data de abertura inválida para o chamado {codigo_chamado}, ignorando...")
                    continue
    chamados.sort(key=lambda x: x['data_abertura'], reverse=True)
    return chamados[:5]

def atualizar_status_chamado(codigo_chamado, novo_status):
    file_path = os.path.join('chamados', f'{codigo_chamado}.txt')
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            lines = f.readlines()
        new_lines = []
        status_atualizado = False
        dados = {}
        for line in lines:
            if line.startswith("Status:"):
                new_lines.append(f"Status: {novo_status}\n")
                status_atualizado = True
            else:
                new_lines.append(line)
            if ":" in line:
                chave, valor = line.split(":", 1)
                dados[chave.strip()] = valor.strip()
        if not status_atualizado:
            new_lines.append(f"Status: {novo_status}\n")
        with open(file_path, 'w') as f:
            f.writelines(new_lines)
        try:
            assunto = f"Status Atualizado - Chamado {codigo_chamado}"
            corpo_email = f"""
O status do seu chamado foi atualizado:

Código: {codigo_chamado}
Novo status: {novo_status}

Atenciosamente,
Suporte Evoque
            """
            enviar_email(assunto, corpo_email, dados.get('E-mail', ''))
        except Exception as e:
            print(f"Erro ao enviar e-mail de notificação: {e}")
        return True
    return False

def excluir_chamado(codigo_chamado):
    file_path = os.path.join('chamados', f'{codigo_chamado}.txt')
    if os.path.exists(file_path):
        os.remove(file_path)
        return True
    return False

def listar_solicitacoes():
    solicitacoes = []
    pasta_solicitacoes = 'solicitacoes'
    for filename in os.listdir(pasta_solicitacoes):
        if filename.endswith('.txt'):
            caminho_arquivo = os.path.join(pasta_solicitacoes, filename)
            with open(caminho_arquivo, 'r') as file:
                dados = {}
                for linha in file:
                    if ":" in linha:
                        chave, valor = linha.split(":", 1)
                        dados[chave.strip()] = valor.strip()
                solicitacoes.append(dados)
    return solicitacoes

@app.route('/painel-metricas', methods=['GET', 'POST'])
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
    
    return render_template('painel.html',
                           metricas_chamados=metricas_chamados,
                           metricas_solicitacoes=metricas_solicitacoes,
                           dados_grafico=dados_grafico,
                           solicitacoes=solicitacoes,
                           chamados=chamados,
                           usuarios=usuarios,
                           now=datetime.now(),
                           section=section)

@app.route('/criar-usuario', methods=['POST'])
@login_required
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

@app.route('/bloquear-usuario', methods=['POST'])
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
    return redirect(url_for('painel_metricas', section='usuarios'))

@app.route('/redefinir-senha', methods=['POST'])
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
    return redirect(url_for('painel_metricas', section='usuarios'))

@app.route('/alterar-email', methods=['POST'])
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
    return redirect(url_for('painel_metricas', section='usuarios'))

@app.route('/alterar-permissao', methods=['POST'])
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
    return redirect(url_for('painel_metricas', section='permissoes'))

@app.route('/excluir-usuario', methods=['POST'])
@admin_required
def excluir_usuario():
    usuario = request.form.get('usuario')
    usuario_dir = os.path.join(os.path.dirname(__file__), 'usuario')
    arquivo_usuario = os.path.join(usuario_dir, f"{usuario}.json")
    if os.path.exists(arquivo_usuario):
        os.remove(arquivo_usuario)
        section = 'usuarios' if request.referrer and 'usuarios' in request.referrer else 'bloqueios'
        return redirect(url_for('painel_metricas', section=section))
    return jsonify({'status': 'error', 'message': 'Usuário não encontrado'}), 404

@app.route('/buscar-chamado', methods=['GET'])
@admin_required
def buscar_chamado_rota():
    codigo_chamado = request.args.get('codigo')
    if not codigo_chamado:
        return jsonify({'status': 'error', 'message': 'Código do chamado não fornecido'}), 400
    
    chamado = buscar_chamado(codigo_chamado)
    if chamado:
        return jsonify(chamado)
    return jsonify({'status': 'error', 'message': 'Chamado não encontrado'}), 404

@app.route('/atualizar-status-chamado', methods=['POST'])
@admin_required
def atualizar_status():
    codigo_chamado = request.form.get('codigo_chamado')
    novo_status = request.form.get('novo_status')
    redirect_to = request.form.get('redirect_to', 'gerenciar-chamados')

    if not codigo_chamado or not novo_status:
        return jsonify({'status': 'error', 'message': 'Dados incompletos'}), 400

    arquivo_chamado = os.path.join('chamados', f'{codigo_chamado}.txt')
    if os.path.exists(arquivo_chamado):
        # Lê o conteúdo atual
        with open(arquivo_chamado, 'r') as f:
            linhas = f.readlines()
        
        # Atualiza o status e adiciona ao histórico
        novas_linhas = []
        status_atualizado = False
        historico_adicionado = False
        data_atual = datetime.now().strftime('%d/%m/%Y %H:%M')
        
        for linha in linhas:
            if linha.startswith('Status:'):
                novas_linhas.append(f'Status: {novo_status}\n')
                status_atualizado = True
            else:
                novas_linhas.append(linha)
        
        if not status_atualizado:
            novas_linhas.append(f'Status: {novo_status}\n')
        
        # Adiciona ao histórico apenas se o status mudou
        for linha in linhas:
            if linha.startswith('Status:') and linha.strip() != f'Status: {novo_status}':
                novas_linhas.append(f'Histórico: Status alterado para {novo_status} em {data_atual}\n')
                historico_adicionado = True
                break
        
        if not historico_adicionado and not any(linha.startswith('Histórico:') for linha in linhas):
            novas_linhas.append(f'Histórico: Status alterado para {novo_status} em {data_atual}\n')

        # Escreve as alterações no arquivo
        with open(arquivo_chamado, 'w') as f:
            f.writelines(novas_linhas)
        
        return redirect(url_for('painel_metricas', section=redirect_to))
    return jsonify({'status': 'error', 'message': 'Chamado não encontrado'}), 404

@app.route('/excluir-chamado', methods=['POST'])
@admin_required
def excluir():
    codigo_chamado = request.form.get('codigo_chamado')
    redirect_to = request.form.get('redirect_to', 'gerenciar-chamados')

    if not codigo_chamado:
        return jsonify({'status': 'error', 'message': 'Código do chamado não fornecido'}), 400

    arquivo_chamado = os.path.join('chamados', f'{codigo_chamado}.txt')
    if os.path.exists(arquivo_chamado):
        os.remove(arquivo_chamado)
        return redirect(url_for('painel_metricas', section=redirect_to))
    return jsonify({'status': 'error', 'message': 'Chamado não encontrado'}), 404

@app.route('/gerar-relatorio-pdf/<codigo_chamado>', methods=['GET'])
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

@app.route('/painel-administrativo')
@login_required
def painel_administrativo():
    if not verificar_permissao(session.get('role'), 'painel_administrativo'):
        return render_template('acesso_negado.html', 
                              message="Acesso não autorizado. O usuário não tem permissão para acessar essa página. Redirecionando para página principal em 5...4...3...2...1..."), 403
    solicitacoes = listar_solicitacoes()
    return render_template('painel_administrativo.html', solicitacoes=solicitacoes)

@app.route('/')
@login_required
def index():
    chamados_recentes = buscar_chamados_recentes()
    concluidos, andamento, pendentes = contar_chamados()
    usuario_info = {'nome': 'Desconhecido', 'sobrenome': '', 'role': session.get('role', 'gerente')}
    if 'usuario' in session:
        arquivo_usuario = os.path.join(USUARIOS_DIR, f"{session['usuario']}.json")
        try:
            if os.path.exists(arquivo_usuario):
                with open(arquivo_usuario, 'r') as f:
                    usuario_info = json.load(f)
        except Exception as e:
            print(f"Erro ao carregar dados do usuário {session['usuario']}: {e}")
    return render_template('index.html', 
                         chamados_recentes=chamados_recentes,
                         concluidos=concluidos, 
                         andamento=andamento, 
                         pendentes=pendentes,
                         usuario_info=usuario_info)

@app.route('/abrir-chamado', methods=['GET', 'POST'])
@login_required
def abrir_chamado():
    if request.method == 'POST':
        try:
            nome_solicitante = request.form['nome_solicitante']
            descricao = request.form.get('descricao', '')
            email_solicitante = request.form.get('email')
            telefone_solicitante = request.form.get('telefone')
            unidade_val = request.form.get('unidade')
            unidade_full = unidades_dict.get(unidade_val, unidade_val)
            problema_reportado = request.form.get('problema')
            internet_item = request.form.get('internet_item', '')
            problema_completo = f"{problema_reportado} - {internet_item}" if internet_item else problema_reportado
            cargo = request.form.get('cargo')
            visita_tecnica = request.form.get('data_visita', '')
            prioridade = "Média"
            problemas_urgentes = ['Catraca', 'Antenas (WI-FI)', 'Teclado', 'Mouse', 'Webcam (Logitech C920)']
            if problema_reportado in problemas_urgentes:
                prioridade = 'Urgente'

            chamado_anterior, codigo_chamado_anterior = verificar_chamado_anterior(email_solicitante, problema_completo)

            if chamado_anterior:
                atualizar_status_chamado(codigo_chamado_anterior, "Aguardando")
                data_reabertura = datetime.now().strftime('%d/%m/%Y %H:%M')
                with open(os.path.join('chamados', f'{codigo_chamado_anterior}.txt'), 'a') as file:
                    file.write(f"Histórico: Reaberto em {data_reabertura}\n")
                protocolo_chamado = chamado_anterior['protocolo']
                return jsonify({
                    'status': 'success',
                    'codigo_chamado': codigo_chamado_anterior,
                    'protocolo_chamado': protocolo_chamado,
                    'mensagem': 'Chamado reaberto com sucesso!'
                })

            codigo_chamado = gerar_codigo_sequencial("chamados", "EVQ-")
            protocolo_chamado = gerar_protocolo()
            data_abertura = datetime.now().strftime('%d/%m/%Y %H:%M')
            with open(f"chamados/{codigo_chamado}.txt", "w") as file:
                file.write(f"Chamado: {codigo_chamado}\n")
                file.write(f"Protocolo: {protocolo_chamado}\n")
                file.write(f"Prioridade: {prioridade}\n")
                file.write(f"Nome do Solicitante: {nome_solicitante}\n")
                file.write(f"Cargo: {cargo}\n")
                file.write(f"Unidade: {unidade_full}\n")
                file.write(f"E-mail: {email_solicitante}\n")
                file.write(f"Telefone: {telefone_solicitante}\n")
                file.write(f"Problema Reportado: {problema_completo}\n")
                file.write(f"Data de Abertura: {data_abertura}\n")
                file.write(f"Visita Técnica: {visita_tecnica if visita_tecnica else 'Não requisitada'}\n")
                file.write(f"Descricao: {descricao}\n")
                file.write("Status: Aguardando\n")
                file.write("Histórico: Criado em " + data_abertura + "\n")

            enviar_email_confirmacao_chamado(nome_solicitante, codigo_chamado, protocolo_chamado, prioridade,
                                            cargo, email_solicitante, telefone_solicitante, problema_completo,
                                            descricao, visita_tecnica, unidade_full)
            return jsonify({'status': 'success', 'codigo_chamado': codigo_chamado, 'protocolo_chamado': protocolo_chamado})
        except Exception as e:
            print(f"Erro ao abrir o chamado: {e}")
            return jsonify({'status': 'error', 'message': 'Erro ao abrir o chamado'})
    return render_template('abrir_chamado.html')

@app.route('/enviar-ticket', methods=['GET', 'POST'])
@login_required
@admin_required
def enviar_ticket():
    if request.method == 'POST':
        assunto = request.form.get('assunto')
        email_destinatario = request.form.get('email_destinatario')
        mensagem = request.form.get('mensagem')
        arquivos = request.files.getlist('arquivos')

        try:
            corpo_email = f"""
Assunto: {assunto}

{mensagem}
            """
            msg = MIMEMultipart()
            msg['From'] = EMAIL
            msg['To'] = email_destinatario
            msg['Subject'] = assunto
            msg.attach(MIMEText(corpo_email, 'html'))

            for arquivo in arquivos:
                if arquivo and arquivo.filename:
                    part = MIMEBase('application', 'octet-stream')
                    part.set_payload(arquivo.read())
                    encoders.encode_base64(part)
                    part.add_header('Content-Disposition', f'attachment; filename="{arquivo.filename}"')
                    msg.attach(part)

            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
            server.starttls()
            server.login(EMAIL, EMAIL_PASSWORD)
            server.send_message(msg)
            server.quit()

            return jsonify({'status': 'success', 'message': 'Ticket enviado com sucesso!'})
        except Exception as e:
            print(f"Erro ao enviar ticket: {e}")
            return jsonify({'status': 'error', 'message': 'Erro ao enviar o ticket'}), 500

    return render_template('enviar_ticket.html')

@app.route('/admin-painel', methods=['GET', 'POST'])
@login_required
@admin_required
def administrar_chamados():
    chamados = listar_chamados()
    return render_template('admin_painel.html', chamados=chamados)

@app.route('/listar-chamados', methods=['GET'])
@login_required
@admin_required
def listar_chamados_json():
    return jsonify({'chamados': listar_chamados()})

@app.route('/gerar-relatorio', methods=['GET', 'POST'])
@login_required
def gerar_relatorio():
    if request.method == 'POST':
        codigo_chamado = request.form.get('codigo_chamado')
        chamado = buscar_chamado(codigo_chamado)
        if chamado:
            file_path = gerar_pdf(
                chamado['codigo_chamado'],
                chamado['nome_solicitante'],
                chamado['descricao'],
                chamado['problema_reportado'],
                chamado['prioridade'],
                chamado['data_abertura'],
                chamado['visita_tecnica'],
                chamado['status'],
                chamado['cargo']
            )
            return send_file(file_path, as_attachment=True)
        else:
            return render_template('gerar_relatorio.html', erro="Chamado não encontrado!")
    return render_template('gerar_relatorio.html')

import chardet

@app.route('/ver-meus-chamados', methods=['GET', 'POST'])
@login_required
def ver_meus_chamados():
    if request.method == 'POST':
        codigo = request.form.get('codigo', '').strip()
        print(f"Buscando código: {codigo}")  # Log para depuração
        
        # Buscar chamado na pasta "chamados"
        chamado = buscar_chamado(codigo)
        if chamado:
            print("Chamado encontrado na pasta 'chamados'")
            resultado = {
                'tipo': 'chamado',
                'codigo_chamado': chamado.get('codigo_chamado', ''),
                'protocolo': chamado.get('protocolo', ''),
                'prioridade': chamado.get('prioridade', ''),
                'status': chamado.get('status', 'Pendente'),
                'nome_solicitante': chamado.get('nome_solicitante', ''),
                'cargo': chamado.get('cargo', ''),
                'problema_reportado': chamado.get('problema_reportado', ''),
                'data_abertura': chamado.get('data_abertura', ''),
                'visita_tecnica': chamado.get('visita_tecnica', 'Não requisitada')
            }
            return jsonify({'status': 'success', 'resultado': resultado})
        else:
            # Buscar solicitação de compra nas pastas "solicitacoes" e "solicitacoes_ti"
            for pasta in ['solicitacoes', 'solicitacoes_ti']:
                arquivo_solicitacao = os.path.join(pasta, f'{codigo}.txt')
                if os.path.exists(arquivo_solicitacao):
                    print(f"Arquivo encontrado em: {pasta}")
                    try:
                        with open(arquivo_solicitacao, 'rb') as file_raw:
                            result = chardet.detect(file_raw.read())
                            encoding = result['encoding'] or 'utf-8'
                            print(f"Codificação detectada: {encoding}")

                        with open(arquivo_solicitacao, 'r', encoding=encoding) as file:
                            dados = {}
                            for linha in file:
                                linha = linha.strip()
                                if ":" in linha:
                                    chave, valor = linha.split(":", 1)
                                    dados[chave.strip()] = valor.strip()

                        if 'Código' not in dados:
                            print("Código não encontrado no arquivo")
                            continue

                        prioridade = "Média"
                        if "urgente" in dados.get('Motivo', '').lower() or pasta.lower() == 'solicitacoes_ti':
                            prioridade = "Urgente"

                        resultado = {
                            'tipo': 'solicitacao',
                            'codigo': dados.get('Código', ''),
                            'protocolo': dados.get('Protocolo', ''),
                            'prioridade': prioridade,
                            'filial': dados.get('Filial', 'Não informado'),
                            'nome_solicitante': dados.get('Nome do Solicitante', 'Não informado'),
                            'item': dados.get('Item', 'Não informado'),
                            'quantidade': dados.get('Quantidade', '1'),
                            'motivo': dados.get('Motivo', 'Não informado'),
                            'data': dados.get('Data', ''),
                            'status': dados.get('Status', 'Aguardando'),
                            'local_entrega': dados.get('Local Entrega', 'Não informado'),
                            'link': dados.get('Link', 'Não informado'),
                            'pasta': pasta
                        }
                        print(f"Resultado da solicitação: {resultado}")
                        return jsonify({'status': 'success', 'resultado': resultado})
                    except Exception as e:
                        print(f"Erro ao ler o arquivo {arquivo_solicitacao}: {e}")
                        return jsonify({'status': 'error', 'message': f'Erro ao processar o arquivo: {str(e)}'}), 500
                else:
                    print(f"Arquivo {codigo}.txt não encontrado em {pasta}")

        return jsonify({'status': 'error', 'message': 'Código não encontrado!'}), 404

    return render_template('ver_meus_chamados.html')

@app.route('/atualizar-status-solicitacao', methods=['POST'])
@login_required
@admin_required
def atualizar_status_solicitacao():
    codigo_solicitacao = request.form.get('codigo_solicitacao')
    novo_status = request.form.get('novo_status')
    pasta = 'solicitacoes' if not any(item.lower() in codigo_solicitacao.lower() for item in ['ti']) else 'solicitacoes_ti'
    file_path = os.path.join(pasta, f'{codigo_solicitacao}.txt')
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            lines = f.readlines()
        new_lines = []
        status_atualizado = False
        for line in lines:
            if line.startswith("Status:"):
                new_lines.append(f"Status: {novo_status}\n")
                status_atualizado = True
            else:
                new_lines.append(line)
        if not status_atualizado:
            new_lines.append(f"Status: {novo_status}\n")
        with open(file_path, 'w') as f:
            f.writelines(new_lines)
        return jsonify({'status': 'success', 'message': 'Status atualizado com sucesso!'})
    return jsonify({'status': 'error', 'message': 'Solicitação não encontrada'}), 404

@app.route('/excluir-solicitacao', methods=['POST'])
@login_required
@admin_required
def excluir_solicitacao():
    codigo_solicitacao = request.form.get('codigo_solicitacao')
    pasta = 'solicitacoes' if not any(item.lower() in codigo_solicitacao.lower() for item in ['ti']) else 'solicitacoes_ti'
    file_path = os.path.join(pasta, f'{codigo_solicitacao}.txt')
    if os.path.exists(file_path):
        os.remove(file_path)
        return jsonify({'status': 'success', 'message': 'Solicitação excluída com sucesso!'})
    return jsonify({'status': 'error', 'message': 'Solicitação não encontrada'}), 404

@app.route('/enviar-ticket-solicitacao', methods=['POST'])
@login_required
@admin_required
def enviar_ticket_solicitacao():
    codigo_solicitacao = request.form.get('codigo_solicitacao')
    pasta = 'solicitacoes' if not any(item.lower() in codigo_solicitacao.lower() for item in ['ti']) else 'solicitacoes_ti'
    file_path = os.path.join(pasta, f'{codigo_solicitacao}.txt')
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            dados = {}
            for linha in f:
                if ":" in linha:
                    chave, valor = linha.split(":", 1)
                    dados[chave.strip()] = valor.strip()
        assunto = f"Atualização - Solicitação {codigo_solicitacao}"
        corpo_email = f"""
Atualização sobre sua solicitação:

Código: {codigo_solicitacao}
Status: {dados.get('Status', 'Aguardando')}
Detalhes: {dados.get('Item', '')}

Atenciosamente,
Suporte Evoque
        """
        enviar_email(assunto, corpo_email, dados.get('E-mail', ''))
        return jsonify({'status': 'success', 'message': 'Ticket enviado com sucesso!'})
    return jsonify({'status': 'error', 'message': 'Solicitação não encontrada'}), 404

@app.route('/sucesso')
def sucesso():
    return "Login bem-sucedido!"

@app.route('/login', methods=['GET', 'POST'])
def login():
    next_url = request.args.get('next')
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        next_url = request.form.get('next')
        usuario = verificar_usuario(username, password)
        if usuario:
            session['usuario'] = username
            session['role'] = usuario.get('role', 'gerente')
            if usuario.get('alterar_senha'):
                session['primeiro_acesso'] = True
                return render_template('login.html', primeiro_acesso=True, next=next_url)
            if next_url and is_safe_url(next_url):
                return redirect(next_url)
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error="Credenciais inválidas!", next=next_url)
    if next_url and not is_safe_url(next_url):
        next_url = url_for('index')
    return render_template('login.html', next=next_url)

def verificar_permissao_para_url(role, url_path):
    try:
        with app.test_request_context(url_path):
            endpoint = request.endpoint
            return verificar_permissao(role, endpoint)
    except:
        return False

@app.route('/logout')
def logout():
    session.pop('usuario', None)
    session.pop('role', None)
    return redirect(url_for('login'))

@app.route('/verificar-acesso', methods=['POST'])
def verificar_acesso():
    dados = request.get_json()
    pagina = dados.get('pagina')
    role = session.get('role')
    if role and verificar_permissao(role, pagina):
        return jsonify({'autorizado': True})
    return jsonify({'autorizado': False})

@app.route('/trocar-senha', methods=['POST'])
def trocar_senha():
    if 'usuario' not in session or not session.get('primeiro_acesso'):
        return redirect(url_for('login'))

    nova_senha = request.form.get('nova_senha')
    confirmar_senha = request.form.get('confirmar_senha')

    if not nova_senha or not confirmar_senha or nova_senha != confirmar_senha:
        error = "As senhas não conferem ou estão vazias!"
        return render_template('login.html', primeiro_acesso=True, error=error)

    username = session.get('usuario')
    arquivo_usuario = os.path.join(USUARIOS_DIR, f"{username}.json")

    if os.path.exists(arquivo_usuario):
        with open(arquivo_usuario, 'r') as f:
            dados = json.load(f)
        dados['senha'] = nova_senha
        dados['alterar_senha'] = False

        with open(arquivo_usuario, 'w') as f:
            json.dump(dados, f, indent=4)

        session.pop('primeiro_acesso', None)
        return render_template('login.html', senha_alterada=True)
    else:
        error = "Usuário não encontrado!"
        return render_template('login.html', primeiro_acesso=True, error=error)

@app.route('/compras-holding', methods=['GET', 'POST'])
@login_required
def compras_holding():
    if request.method == 'POST':
        try:
            filial = unidades_dict.get(request.form['filial'], request.form['filial'])
            nome_solicitante = request.form['nome_solicitante']
            descricao_item = request.form['descricao_item']
            quantidade = request.form['quantidade']
            motivo = request.form['motivo']
            email_solicitante = request.form['email']
            local_entrega = unidades_dict.get(request.form['local_entrega'], request.form['local_entrega'])
            link_produto = request.form['link_produto']
            anexo = request.files.get('anexo')

            pasta = "solicitacoes"  # Todas as solicitações vão para a pasta "solicitacoes"
            codigo_solicitacao = gerar_codigo_sequencial(pasta, "COMP-")
            protocolo = gerar_protocolo()
            data_abertura = datetime.now().strftime('%d/%m/%Y %H:%M')

            with open(f"{pasta}/{codigo_solicitacao}.txt", "w") as file:
                file.write(f"Código: {codigo_solicitacao}\n")
                file.write(f"Protocolo: {protocolo}\n")
                file.write(f"Data: {data_abertura}\n")
                file.write(f"Filial: {filial}\n")
                file.write(f"Nome do Solicitante: {nome_solicitante}\n")
                file.write(f"Item: {descricao_item}\n")
                file.write(f"Quantidade: {quantidade}\n")
                file.write(f"Motivo: {motivo}\n")
                file.write(f"E-mail: {email_solicitante}\n")
                file.write(f"Local Entrega: {local_entrega}\n")
                file.write(f"Link: {link_produto}\n")
                file.write("Status: Aguardando\n")

            if anexo and anexo.filename:
                anexo_dir = os.path.join(pasta, 'anexos')
                os.makedirs(anexo_dir, exist_ok=True)
                anexo_path = os.path.join(anexo_dir, f"{codigo_solicitacao}_{anexo.filename}")
                anexo.save(anexo_path)

            sucesso = enviar_email_compras(
                filial,
                nome_solicitante,
                descricao_item,
                quantidade,
                motivo,
                email_solicitante,
                local_entrega,
                link_produto,
                codigo_solicitacao,
                protocolo,
                anexo
            )
            if not sucesso:
                return jsonify({'status': 'error', 'message': 'Erro ao enviar e-mail de compras.'}), 500

            enviar_email_confirmacao_compra(
                filial, nome_solicitante, descricao_item, quantidade, motivo,
                email_solicitante, local_entrega, link_produto, codigo_solicitacao, protocolo
            )

            return jsonify({
                'status': 'success',
                'codigo': codigo_solicitacao,
                'protocolo': protocolo
            })

        except Exception as e:
            print(f"Erro ao processar solicitação de compra holding: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    return render_template('compras_holding.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)