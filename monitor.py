from flask import Blueprint, render_template, jsonify, Response, request
import requests
from datetime import datetime, timedelta
import logging
import base64
import json
import os
import time

monitor_bp = Blueprint('monitor', __name__, template_folder='templates')

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

API_URL = "https://evo-integracao-api.w12app.com.br/api/v1/entries?take=10000&skip=0&IdEntry=0"
USERNAME = "evoquefitness"
TOKEN = "5C991901-7AE5-4E17-BF9B-2AD16061E639"

auth_string = f"{USERNAME}:{TOKEN}"
auth_bytes = auth_string.encode('ascii')
base64_auth = base64.b64encode(auth_bytes).decode('ascii')

headers = {
    "Authorization": f"Basic {base64_auth}",
    "Content-Type": "application/json"
}

last_access = {}
recent_entries = []
last_status_check = datetime.now() - timedelta(minutes=1)
unit_status = {}
OFFLINE_THRESHOLD_MINUTES = 60
UNITS_FILE = "known_units.json"

def load_known_units():
    if os.path.exists(UNITS_FILE):
        try:
            with open(UNITS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Erro ao carregar {UNITS_FILE}: {e}")
    default_units = {
        "4": "DIADEMA", "5": "SHOPPING MAUÁ", "6": "RIBEIRÃO PIRES",
        "7": "HOMERO THON", "8": "AVENIDA PORTUGAL", "9": "VALO VELHO", "10": "ITAMARATI",
        "11": "AVENIDA RIO BRANCO", "12": "PEREIRA BARRETO", "13": "GIOVANNI BREDA", "14": "BOQUEIRÃO",
        "15": "PARQUE DO CARMO", "16": "ZAIRA", "17": "HELIOPOLIS", "19": "PIMENTAS",
        "20": "GUAIANASES", "21": "AVENIDA GOIÁS", "22": "BOM CLIMA", "23": "CAMPO GRANDE",
        "24": "JAGUARÉ", "25": "ITAQUERA", "26": "EXTREMA", "27": "MOGI DAS CRUZES",
        "28": "ALAMEDA", "29": "JARDIM GOIAS", "30": "PASSEIO DAS ÁGUAS", "31": "SÃO VICENTE",
        "32": "CAMILÓPOLIS", "33": "INDAIATUBA", "34": "VILA PRUDENTE", "35": "LARANJAL PAULISTA",
        "36": "SACOMÃ", "37": "VILA NOVA", "38": "SAPOPEMBA", "39": "POÁ", "40": "CURITIBA",
        "41": "FRANCA", "42": "AVENIDA AMERICANO", "130": "JARDIM SÃO PAULO", "131": "CARAPICUIBA"
    }
    save_known_units(default_units)
    return default_units

def save_known_units(units):
    try:
        with open(UNITS_FILE, 'w', encoding='utf-8') as f:
            json.dump(units, f, ensure_ascii=False, indent=2)
        logger.info(f"Unidades salvas em {UNITS_FILE}")
    except Exception as e:
        logger.error(f"Erro ao salvar {UNITS_FILE}: {e}")

KNOWN_UNITS = load_known_units()

def get_entries():
    try:
        logger.debug(f"Chamando API: {API_URL}")
        response = requests.get(API_URL, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        logger.debug(f"Dados recebidos da API: {len(data)} entradas")
        process_entries(data)
        return data
    except requests.exceptions.RequestException as error:
        logger.error(f"Erro ao chamar API externa: {str(error)}")
        return {"error": f"Falha na API externa: {str(error)}", "status": "error"}
    except ValueError as error:
        logger.error(f"Erro ao decodificar JSON: {str(error)}")
        return {"error": "Resposta inválida da API externa", "status": "error"}
    except Exception as error:
        logger.error(f"Erro inesperado na função get_entries: {str(error)}")
        return {"error": f"Erro interno: {str(error)}", "status": "error"}

def process_entries(entries):
    if not entries or not isinstance(entries, list):
        logger.warning("Nenhuma entrada recebida ou formato inválido")
        return
    
    entries_by_branch = {}
    for entry in entries:
        if 'idBranch' not in entry or 'date' not in entry or 'entryAction' not in entry:
            continue
            
        if entry['entryAction'] != 'entry':
            continue
            
        branch_id = str(entry['idBranch'])
        try:
            entry_date = datetime.fromisoformat(entry['date'].replace('Z', '+00:00'))
            
            if branch_id not in entries_by_branch or entry_date > entries_by_branch[branch_id]['timestamp']:
                entries_by_branch[branch_id] = {
                    'timestamp': entry_date,
                    'last_entry': entry['date'],
                    'name': entry.get('nameMember') or entry.get('nameProspect') or "Não identificado",
                    'action': 'entry',
                    'idMember': entry.get('idMember', 'Não disponível'),
                    'entryType': entry.get('entryType', 'Não especificado'),
                    'device': entry.get('device', 'Não especificado'),
                    'blockReason': entry.get('blockReason', 'Nenhuma mensagem'),
                    'entry': entry.get('entry', False)
                }
                
            if entry['entryAction'] == 'entry' and entry not in recent_entries:
                recent_entries.insert(0, entry)
                if len(recent_entries) > 10000:
                    recent_entries.pop()
                    
        except ValueError as error:
            logger.error(f"Erro ao parsear data para unidade {branch_id}: {error}")
            continue
    
    for branch_id, data in entries_by_branch.items():
        last_access[branch_id] = data
    
    logger.debug(f"Último acesso atualizado para {len(entries_by_branch)} unidades")

def check_unit_status():
    global last_status_check, unit_status
    
    now = datetime.now()
    if (now - last_status_check).total_seconds() < 60:
        return [], []
    
    last_status_check = now
    current_time = datetime.now()
    offline_threshold = current_time - timedelta(minutes=OFFLINE_THRESHOLD_MINUTES)
    status_changes = []
    recovered_units = []
    
    for unit_id in KNOWN_UNITS:
        current_status = unit_status.get(unit_id, "online")
        
        if unit_id not in last_access or last_access[unit_id]['timestamp'] < offline_threshold:
            new_status = "offline"
        else:
            new_status = "online"
        
        if current_status != new_status:
            status_changes.append({
                'unit_id': unit_id,
                'unit_name': f"{KNOWN_UNITS[unit_id]} - {unit_id}",
                'previous_status': current_status,
                'new_status': new_status
            })
            unit_status[unit_id] = new_status
            
            if new_status == "online" and current_status == "offline":
                recovered_units.append({
                    'unit_id': unit_id,
                    'unit_name': f"{KNOWN_UNITS[unit_id]} - {unit_id}"
                })
    
    return status_changes, recovered_units

@monitor_bp.route('/monitor')
def monitor():
    return render_template('monitor.html')

@monitor_bp.route('/api/entries')
def api_entries():
    try:
        entries = get_entries()
        if isinstance(entries, dict) and "error" in entries:
            logger.error(f"Erro na API: {entries['error']}")
            return jsonify(entries), 500

        offline_units = get_offline_units()
        status_changes, recovered_units = check_unit_status()
        
        logger.debug(f"Retornando: entries={len(entries) if isinstance(entries, list) else 'erro'}, offline_units={len(offline_units)}")

        return jsonify({
            "entries": entries if isinstance(entries, list) else [],
            "offline_units": offline_units,
            "latest_accesses": last_access,
            "stats": calculate_stats(entries) if isinstance(entries, list) else {},
            "status_changes": status_changes,
            "recovered_units": recovered_units,
            "known_units": {k: f"{v} - {k}" for k, v in KNOWN_UNITS.items()}
        })
    except Exception as error:
        logger.error(f"Erro inesperado na rota /api/entries: {str(error)}")
        return jsonify({"error": "Erro interno no servidor", "status": "error"}), 500

@monitor_bp.route('/api/realtime-updates')
def realtime_updates():
    def event_stream():
        last_sent_index = -1
        while True:
            time.sleep(1)
            if recent_entries and last_sent_index < len(recent_entries)-1:
                latest_entry = recent_entries[0]
                last_sent_index = 0
                yield f"data: {json.dumps(latest_entry)}\n\n"
    
    return Response(event_stream(), mimetype="text/event-stream")

@monitor_bp.route('/api/add_unit', methods=['POST'])
def add_unit():
    logger.debug("Recebendo requisição para adicionar nova unidade")
    data = request.json
    unit_name = data.get('name')
    unit_id = str(data.get('id'))

    if not unit_name or not unit_id:
        logger.error("Requisição inválida: nome e ID são requeridos")
        return jsonify({"error": "Nome e ID são requeridos"}), 400

    if unit_id in KNOWN_UNITS:
        logger.warning(f"Unidade já existe: ID {unit_id}")
        return jsonify({"error": "Unidade já existe"}), 400

    KNOWN_UNITS[unit_id] = unit_name
    save_known_units(KNOWN_UNITS)
    logger.info(f"Nova unidade adicionada: {unit_name} - {unit_id}")

    formatted_unit = f"{unit_name} - {unit_id}"

    return jsonify({
        "message": "Unidade adicionada com sucesso",
        "unit": {"id": unit_id, "name": unit_name, "formatted": formatted_unit}
    }), 201

def get_offline_units():
    offline_units = []
    current_time = datetime.now()
    offline_threshold = current_time - timedelta(minutes=OFFLINE_THRESHOLD_MINUTES)
    
    for unit_id in KNOWN_UNITS:
        if unit_id not in last_access or last_access[unit_id]['timestamp'] < offline_threshold:
            offline_units.append({
                'id': unit_id,
                'name': f"{KNOWN_UNITS[unit_id]} - {unit_id}",
                'last_access': last_access[unit_id]['last_entry'] if unit_id in last_access else None,
                'offline_since': offline_threshold.isoformat()
            })
    
    logger.debug(f"Unidades offline: {len(offline_units)} de {len(KNOWN_UNITS)}")
    return offline_units

def calculate_stats(entries):
    today = datetime.now().date()
    entries_today = []
    
    for entry in entries:
        if not isinstance(entry, dict):
            continue
            
        if 'date' in entry and 'entryAction' in entry and 'idBranch' in entry:
            try:
                entry_date = datetime.fromisoformat(entry['date'].replace('Z', '+00:00')).date()
                if entry_date == today and entry['entryAction'] == 'entry':
                    entries_today.append(entry)
            except (ValueError, AttributeError) as error:
                logger.error(f"Erro ao processar entrada para estatísticas: {error}")
    
    if not entries_today:
        return {
            "entradas_hoje": 0,
            "unidade_mais_movimentada": "-",
            "ultima_entrada": "-"
        }
    
    branch_counts = {}
    for entry in entries_today:
        branch_id = entry['idBranch']
        branch_counts[branch_id] = branch_counts.get(branch_id, 0) + 1
    
    most_active_branch = max(branch_counts, key=branch_counts.get) if branch_counts else "-"
    
    return {
        "entradas_hoje": len(entries_today),
        "unidade_mais_movimentada": most_active_branch,
        "ultima_entrada": entries_today[0]['date'] if entries_today else "-"
    }