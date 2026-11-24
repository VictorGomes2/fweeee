# =======================================================================
# CM REURB v2.5 - Backend Flask COMPLETO (Com IPTU, Histórico, Arrecadação e Cadastrador)
# =======================================================================
# VERSÃO ATUALIZADA: Inclui controle de Situação de Pagamento (Pago/Em Aberto),
# rotas para cálculo de estatísticas financeiras, compressão de documentos em Base64
# para sincronização offline e registro de auditoria de quem realizou o cadastro (Cadastrador).
# INCLUI ATUALIZAÇÃO NO SERVIÇO DE CÁLCULO TRIBUTÁRIO (VVT, VVC, VVI, Fallback de Uso).
# =======================================================================

import os
import datetime
import threading
import time
import math
import json
import urllib.request
from functools import wraps
import jwt  # PyJWT
import io  # Necessário para a função de exportar
import csv
import base64

import pandas as pd
from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, text as sql_text  # Importação para uso em estatísticas e cálculos
from sqlalchemy.orm import joinedload
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# =======================================================================
# ⚙️ CONFIGURAÇÃO DA APLICAÇÃO
# =======================================================================

app = Flask(__name__)

# 🔹 CORS configurado para aceitar requisições de qualquer origem.
CORS(app)

# 🔹 Carregando variáveis de ambiente (essencial para o Render)
SECRET_KEY = os.environ.get('SECRET_KEY', 'uma-chave-secreta-forte-para-desenvolvimento')
DATABASE_URI = os.environ.get('DATABASE_URL')

if DATABASE_URI and DATABASE_URI.startswith("postgres://"):
    DATABASE_URI = DATABASE_URI.replace("postgres://", "postgresql://", 1)

# A conexão deve vir do ambiente (Render -> Neon).
# Não mantenha credenciais do banco gravadas no código.
if not DATABASE_URI:
    raise RuntimeError(
        "DATABASE_URL não configurada. Configure a URL do PostgreSQL do Neon "
        "nas Environment Variables do Render."
    )


UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')

app.config['SECRET_KEY'] = SECRET_KEY
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Opções importantes para PostgreSQL/Neon:
# - pool_pre_ping: verifica conexões que ficaram inválidas.
# - pool_recycle: recicla conexões antigas e reduz problemas de conexão ociosa.
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': int(os.environ.get('DB_POOL_RECYCLE', '300')),
}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
migrate = Migrate(app, db)

# =======================================================================
# MODELS (ESTRUTURA DE DADOS DO BANCO)
# =======================================================================

class Usuario(db.Model):
    __tablename__ = 'usuarios'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    usuario = db.Column(db.String(50), unique=True, nullable=False)
    senha_hash = db.Column(db.String(1024), nullable=False)
    acesso = db.Column(db.String(20), nullable=False, default='Usuario')

    def __init__(self, nome, usuario, senha, acesso='Usuario'):
        self.nome = nome
        self.usuario = usuario
        self.senha_hash = generate_password_hash(senha, method="scrypt")
        self.acesso = acesso

    def verificar_senha(self, senha):
        return check_password_hash(self.senha_hash, senha)


class CadastroReurb(db.Model):
    __tablename__ = 'cadastros_reurb'
    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(50), default='Em Análise')
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    data_criacao = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    data_atualizacao = db.Column(db.DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    
    # Registro de auditoria do Cadastrador
    cadastrador = db.Column(db.String(100), nullable=True, default='Não Identificado')

    # Etapa 1: Requerente e Cônjuge
    req_nome = db.Column(db.String(150))
    req_cpf = db.Column(db.String(20))
    req_rg = db.Column(db.String(20))
    req_data_nasc = db.Column(db.String(20))
    req_nacionalidade = db.Column(db.String(50))
    req_profissao = db.Column(db.String(100))
    req_telefone = db.Column(db.String(30))
    req_email = db.Column(db.String(150))
    req_estado_civil = db.Column(db.String(30))
    req_regime_bens = db.Column(db.String(50), nullable=True)
    conj_nome = db.Column(db.String(150))
    conj_cpf = db.Column(db.String(20))
    conj_rg = db.Column(db.String(20), nullable=True)
    conj_data_nasc = db.Column(db.String(20), nullable=True)
    conj_nacionalidade = db.Column(db.String(50), nullable=True)
    conj_profissao = db.Column(db.String(100), nullable=True)
    conj_telefone = db.Column(db.String(30), nullable=True)
    conj_email = db.Column(db.String(150), nullable=True)

    # Documentos Comprimidos em Base64
    doc_rg_cnh_url = db.Column(db.Text, nullable=True)
    doc_comprovante_url = db.Column(db.Text, nullable=True)
    doc_contrato_url = db.Column(db.Text, nullable=True)

    # Etapa 2: Imóvel
    inscricao_imobiliaria = db.Column(db.String(30), index=True)
    imovel_cep = db.Column(db.String(15))
    imovel_logradouro = db.Column(db.String(150))
    imovel_numero = db.Column(db.String(20))
    imovel_complemento = db.Column(db.String(100))
    imovel_bairro = db.Column(db.String(100))
    imovel_cidade = db.Column(db.String(100))
    imovel_uf = db.Column(db.String(2))
    confrontante_frente = db.Column(db.String(150), nullable=True)
    confrontante_fundo = db.Column(db.String(150), nullable=True)
    confrontante_ld = db.Column(db.String(150), nullable=True)
    confrontante_le = db.Column(db.String(150), nullable=True)
    imovel_medida_frente = db.Column(db.Float, nullable=True)
    imovel_medida_fundo = db.Column(db.Float, nullable=True)
    imovel_medida_ld = db.Column(db.Float, nullable=True)
    imovel_medida_le = db.Column(db.Float, nullable=True)
    imovel_area_total = db.Column(db.Float)
    # Geometria do lote em GeoJSON EPSG:4326 (longitude/latitude).
    geometria_lote = db.Column(db.Text, nullable=True)
    imovel_data_ocupacao = db.Column(db.String(20), nullable=True)
    imovel_infra_agua = db.Column(db.String(10))
    imovel_infra_esgoto = db.Column(db.String(10))
    imovel_infra_iluminacao = db.Column(db.String(10))
    imovel_infra_pavimentacao = db.Column(db.String(10))
    imovel_infra_lixo = db.Column(db.String(10))
    imovel_medidor_agua = db.Column(db.String(50), nullable=True)
    imovel_medidor_luz = db.Column(db.String(50), nullable=True)
    foto_fachada_url = db.Column(db.Text, nullable=True)
    imovel_num_habitantes = db.Column(db.Integer, nullable=True)
    imovel_muro = db.Column(db.String(10), nullable=True)
    imovel_portoes = db.Column(db.String(10), nullable=True)
    imovel_cerca_eletrica = db.Column(db.String(10), nullable=True)
    imovel_piscina = db.Column(db.String(10), nullable=True)
    risco_inundacao = db.Column(db.String(10), nullable=True)
    risco_deslizamento = db.Column(db.String(10), nullable=True)
    grau_area_risco = db.Column(db.String(50), nullable=True)
    motivo_risco = db.Column(db.Text, nullable=True)
    sensacao_termica = db.Column(db.String(50), nullable=True)
    ventilacao_natural = db.Column(db.String(50), nullable=True)
    poluicao_sonora = db.Column(db.String(20), nullable=True)
    
    # Etapa 3: REURB
    reurb_renda_familiar = db.Column(db.Float)
    reurb_outro_imovel = db.Column(db.String(10))
    reurb_cadunico = db.Column(db.String(10))
    reurb_propriedade = db.Column(db.String(20), nullable=True)
    
    # NOVA COLUNA: Guarda a alíquota associada ao lote/terreno
    uso_imovel = db.Column(db.String(50), nullable=True)
    
    # Relacionamentos
    construcoes = db.relationship("Construcao", backref="cadastro", lazy=True, cascade="all, delete-orphan")
    guias_iptu = db.relationship("GuiaIPTU", backref="cadastro", lazy=True, cascade="all, delete-orphan")


class Construcao(db.Model):
    __tablename__ = 'construcoes'
    id = db.Column(db.Integer, primary_key=True)
    cadastro_id = db.Column(db.Integer, db.ForeignKey('cadastros_reurb.id'), nullable=False)
    
    nome = db.Column(db.String(150), nullable=False)
    area_construida = db.Column(db.Float)
    # Geometria da edificação em GeoJSON EPSG:4326.
    geometria = db.Column(db.Text, nullable=True)
    uso_principal = db.Column(db.String(50))
    padrao_construtivo = db.Column(db.String(100))
    tipo_imovel = db.Column(db.String(50))
    
    estrutura = db.Column(db.String(50), nullable=True)
    cobertura = db.Column(db.String(50), nullable=True)
    instalacao_sanitaria = db.Column(db.String(50), nullable=True)
    forro = db.Column(db.String(50), nullable=True)
    piso = db.Column(db.String(50), nullable=True)
    portas = db.Column(db.String(50), nullable=True)
    janelas = db.Column(db.String(50), nullable=True)
    revestimento = db.Column(db.String(50), nullable=True)


class GuiaIPTU(db.Model):
    __tablename__ = 'guias_iptu'
    id = db.Column(db.Integer, primary_key=True)
    cadastro_id = db.Column(db.Integer, db.ForeignKey('cadastros_reurb.id'), nullable=False)
    ano_exercicio = db.Column(db.Integer, nullable=False)
    valor_emitido = db.Column(db.Float, nullable=False) 
    data_emissao = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    situacao = db.Column(db.String(20), default='Em aberto', nullable=False)  # 'Pago' ou 'Em aberto'

    def to_dict(self):
        return {
            'id_guia': self.id,
            'cadastro_id': self.cadastro_id,
            'ano_exercicio': self.ano_exercicio,
            'valor_emitido': self.valor_emitido,
            'data_emissao': self.data_emissao.strftime('%d/%m/%Y %H:%M:%S'),
            'situacao': self.situacao,
        }

class Documento(db.Model):
    __tablename__ = 'documentos'
    id = db.Column(db.Integer, primary_key=True)
    cadastro_id = db.Column(db.Integer, db.ForeignKey('cadastros_reurb.id'), nullable=False)
    nome_arquivo = db.Column(db.String(255), nullable=False)
    path_arquivo = db.Column(db.String(512), nullable=False)
    tipo_documento = db.Column(db.String(100))
    data_upload = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    cadastro = db.relationship("CadastroReurb", backref=db.backref("documentos", lazy=True, cascade="all, delete-orphan"))


class PadraoConstrutivo(db.Model):
    __tablename__ = 'padroes_construtivos'
    id = db.Column(db.Integer, primary_key=True)
    descricao = db.Column(db.String(150), nullable=False)
    valor_m2 = db.Column(db.Float, nullable=False)


class ValorLogradouro(db.Model):
    __tablename__ = 'valores_logradouro'
    id = db.Column(db.Integer, primary_key=True)
    logradouro = db.Column(db.String(150), unique=True, nullable=False)
    valor_m2 = db.Column(db.Float, nullable=False)


class AliquotaIPTU(db.Model):
    __tablename__ = 'aliquotas_iptu'
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(150), unique=True, nullable=False)
    aliquota = db.Column(db.Float, nullable=False)

# =======================================================================
# SERVIÇOS E UTILIDADES
# =======================================================================

class CalculoTributarioService:
    @staticmethod
    def calcular_valores(cadastro: CadastroReurb):
        vvt, vvc, vvi, iptu = 0.0, 0.0, 0.0, 0.0
        tipo_uso_aplicado = None
        try:
            # 1. Calcular VVT (Valor Venal do Terreno)
            area_total_terreno = float(cadastro.imovel_area_total or 0.0)

            if cadastro.imovel_logradouro and area_total_terreno > 0:
                # Busca ignorando case
                logradouro = ValorLogradouro.query.filter(func.lower(ValorLogradouro.logradouro) == func.lower(cadastro.imovel_logradouro)).first()
                if logradouro:
                    vvt = area_total_terreno * logradouro.valor_m2
            
            # 2. Calcular VVC (Valor Venal das Construções somadas)
            if cadastro.construcoes:
                for construcao in cadastro.construcoes:
                    area_construida = float(construcao.area_construida or 0.0)
                    if construcao.padrao_construtivo and area_construida > 0:
                        # Busca ignorando case
                        padrao = PadraoConstrutivo.query.filter(func.lower(PadraoConstrutivo.descricao) == func.lower(construcao.padrao_construtivo)).first()
                        if padrao:
                            vvc += (area_construida * padrao.valor_m2)
            
            # 3. Calcular VVI (Valor Venal do Imóvel)
            vvi = vvt + vvc

            # 4. Descobrir o Tipo de Uso (Lógica de fallback)
            tipo_uso = getattr(cadastro, 'const_uso', None) or                        getattr(cadastro, 'uso_principal', None) or                        getattr(cadastro, 'uso_imovel', None)

            # Fallback para a primeira construção se não encontrar no imóvel principal
            if not tipo_uso and cadastro.construcoes and len(cadastro.construcoes) > 0:
                uso_const = getattr(cadastro.construcoes[0], 'uso_principal', None)
                if uso_const and uso_const != 'N/D':
                    tipo_uso = uso_const
                else:
                    tipo_uso = getattr(cadastro.construcoes[0], 'const_uso', None)
            
            tipo_uso_aplicado = tipo_uso

            # 5. Calcular IPTU
            if vvi > 0 and tipo_uso:
                # Busca a alíquota exata ignorando case e espaços nas bordas
                aliquota_data = AliquotaIPTU.query.filter(func.lower(func.trim(AliquotaIPTU.tipo)) == func.lower(tipo_uso.strip())).first()
                if aliquota_data:
                    iptu = vvi * aliquota_data.aliquota

        except Exception as e:
            print(f"Erro no cálculo: {e}")
            
        return {
            "vvt": vvt, 
            "vvc": vvc, 
            "vvi": vvi, 
            "iptu": iptu, 
            "tipo_uso_aplicado": tipo_uso_aplicado
        }

# =======================================================================
# DECORADORES E FUNÇÕES AUXILIARES
# =======================================================================
def to_float(value):
    if value is None or value == '': return None
    try:
        if isinstance(value, str): return float(value.replace(',', '.'))
        return float(value)
    except (ValueError, TypeError): return None

def to_int(value):
    if value is None or value == '': return None
    try:
        return int(float(value))
    except (ValueError, TypeError): return None


# =======================================================================
# HEALTH CHECK / KEEP-ALIVE - RENDER + NEON
# =======================================================================
#
# O endpoint /healthz faz uma consulta simples no PostgreSQL.
# Assim, quando o Render/UptimeRobot acessar esta URL, o Neon também
# recebe atividade e pode manter/reabrir a conexão.
#
# A rotina interna abaixo é opcional e funciona enquanto o processo do
# Render estiver executando. Para evitar que um serviço gratuito do Render
# permaneça dormindo, recomenda-se também um monitor externo (ex.: UptimeRobot)
# acessando /healthz a cada 5 minutos.
# =======================================================================

KEEP_ALIVE_ENABLED = os.environ.get('KEEP_ALIVE_ENABLED', 'true').strip().lower() == 'true'
KEEP_ALIVE_INTERVAL = max(30, int(os.environ.get('KEEP_ALIVE_INTERVAL', '120')))
KEEP_ALIVE_URL = os.environ.get('KEEP_ALIVE_URL', '').strip()
KEEP_ALIVE_TIMEOUT = max(1, int(os.environ.get('KEEP_ALIVE_TIMEOUT', '5')))

KEEP_ALIVE_DB_ENABLED = os.environ.get('KEEP_ALIVE_DB_ENABLED', 'true').strip().lower() == 'true'
KEEP_ALIVE_DB_INTERVAL = max(30, int(os.environ.get('KEEP_ALIVE_DB_INTERVAL', '120')))


@app.route('/healthz', methods=['GET'])
def healthz():
    """Health check usado pelo Render/monitor externo e pelo keep-alive."""
    try:
        db_ok = False

        if KEEP_ALIVE_DB_ENABLED:
            # Consulta mínima que acorda/verifica o PostgreSQL do Neon.
            db.session.execute(sql_text('SELECT 1'))
            db.session.remove()
            db_ok = True

        return jsonify({
            'status': 'ok',
            'database': 'ok' if db_ok else 'disabled',
            'timestamp': datetime.datetime.utcnow().isoformat() + 'Z'
        }), 200

    except Exception as e:
        db.session.remove()
        app.logger.warning(f'Health check do banco falhou: {e}')
        return jsonify({
            'status': 'degraded',
            'database': 'error',
            'timestamp': datetime.datetime.utcnow().isoformat() + 'Z'
        }), 503


def _keep_alive_loop():
    """Mantém o serviço ativo enquanto o processo do backend estiver vivo."""
    # Pequeno atraso para deixar o Gunicorn/Flask terminar a inicialização.
    time.sleep(10)

    while True:
        try:
            # 1) Mantém/verifica a conexão com o Neon diretamente.
            if KEEP_ALIVE_DB_ENABLED:
                with app.app_context():
                    db.session.execute(sql_text('SELECT 1'))
                    db.session.remove()

            # 2) Faz uma requisição HTTP ao próprio backend, se configurado.
            # Isso também gera atividade no Render.
            if KEEP_ALIVE_URL:
                req = urllib.request.Request(
                    KEEP_ALIVE_URL,
                    headers={'User-Agent': 'CM-REURB-KeepAlive/1.0'}
                )
                with urllib.request.urlopen(req, timeout=KEEP_ALIVE_TIMEOUT) as response:
                    response.read(64)

        except Exception as e:
            app.logger.warning(f'Keep-alive falhou: {e}')

        time.sleep(KEEP_ALIVE_INTERVAL)


def start_keep_alive():
    """Inicia a rotina em background uma única vez por processo."""
    if not KEEP_ALIVE_ENABLED:
        app.logger.info('Keep-alive desativado por KEEP_ALIVE_ENABLED.')
        return

    # Evita duplicação do thread no Flask development reloader.
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'false':
        return

    thread = threading.Thread(
        target=_keep_alive_loop,
        name='cm-reurb-keep-alive',
        daemon=True
    )
    thread.start()
    app.logger.info(
        f'Keep-alive iniciado: intervalo={KEEP_ALIVE_INTERVAL}s, '
        f'db={KEEP_ALIVE_DB_ENABLED}, url={KEEP_ALIVE_URL or "não configurada"}'
    )


# ------------------ CORS PRE-FLIGHT HANDLER ------------------
@app.before_request
def handle_preflight_cors():
    if request.method == 'OPTIONS':
        response = app.make_response('')
        response.status_code = 200
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Authorization, Content-Type'
        return response

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Authorization, Content-Type'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    return response

# ------------------------------------------------------------

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method == 'OPTIONS': return jsonify({'status': 'ok'}), 200
        token = None
        if 'Authorization' in request.headers:
            try:
                auth_header = request.headers['Authorization']
                token = auth_header.split(" ")[1]
            except IndexError: return jsonify({'mensagem': 'Token inválido!'}), 401
        if not token: return jsonify({'mensagem': 'Token de autenticação ausente!'}), 401
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            current_user = Usuario.query.filter_by(id=data['public_id']).first()
            if not current_user: return jsonify({'mensagem': 'Usuário do token não encontrado!'}), 401
        except jwt.ExpiredSignatureError: return jsonify({'mensagem': 'Token expirado!'}), 401
        except jwt.InvalidTokenError: return jsonify({'mensagem': 'Token inválido!'}), 401
        return f(current_user, *args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(current_user, *args, **kwargs):
        if current_user.acesso != 'Administrador':
            return jsonify({'mensagem': 'Permissão de administrador necessária.'}), 403
        return f(current_user, *args, **kwargs)
    return decorated


# Compatibilidade com bancos já existentes: cria apenas as duas colunas espaciais novas.
def garantir_colunas_geometrias():
    try:
        with db.engine.begin() as conexao:
            conexao.execute(sql_text("ALTER TABLE cadastros_reurb ADD COLUMN IF NOT EXISTS geometria_lote TEXT"))
            conexao.execute(sql_text("ALTER TABLE construcoes ADD COLUMN IF NOT EXISTS geometria TEXT"))
    except Exception as erro:
        app.logger.warning(f"Não foi possível verificar/criar colunas de geometria: {erro}")


_geometrias_schema_verificado = False

@app.before_request
def verificar_schema_geometrias():
    global _geometrias_schema_verificado
    if not _geometrias_schema_verificado:
        garantir_colunas_geometrias()
        _geometrias_schema_verificado = True


# =======================================================================
# ROTAS DA API
# =======================================================================

# ------------------- AUTENTICAÇÃO -------------------
@app.route('/api/login', methods=['POST', 'OPTIONS'])
def login():
    if request.method == 'OPTIONS': return jsonify({'status': 'ok'}), 200
    data = request.get_json()
    if not data or not data.get('usuario') or not data.get('senha'): return jsonify({'mensagem': 'Não foi possível verificar'}), 401
    user = Usuario.query.filter_by(usuario=data['usuario']).first()
    if not user: return jsonify({'mensagem': 'Usuário não encontrado.'}), 401
    if user.verificar_senha(data['senha']):
        token = jwt.encode({
            'public_id': user.id, 'usuario': user.usuario, 'acesso': user.acesso,
            'exp': datetime.datetime.utcnow() + datetime.timedelta(days=1)
        }, app.config['SECRET_KEY'], algorithm="HS256")
        return jsonify({'mensagem': 'Login bem-sucedido!', 'token': token, 'nome_usuario': user.nome, 'acesso': user.acesso})
    return jsonify({'mensagem': 'Login ou senha incorretos.'}), 401
    
@app.route('/api/redefinir-senha', methods=['POST'])
def redefinir_senha():
    data = request.get_json()
    usuario_str, senha_atual, senha_nova = data.get('usuario'), data.get('senha_atual'), data.get('senha_nova')
    if not all([usuario_str, senha_atual, senha_nova]): return jsonify({'erro': 'Todos os campos são obrigatórios'}), 400
    user = Usuario.query.filter_by(usuario=usuario_str).first()
    if not user or not user.verificar_senha(senha_atual): return jsonify({'erro': 'Usuário ou senha atual incorreta'}), 401
    user.senha_hash = generate_password_hash(senha_nova, method="scrypt")
    db.session.commit()
    return jsonify({'sucesso': True, 'mensagem': 'Senha redefinida com sucesso!'})


# ------------------- CADASTRO REURB -------------------
@app.route('/api/cadastrar_reurb', methods=['POST'])
@token_required
def cadastrar_reurb(current_user):
    data = request.get_json()
    try:
        novo_cadastro = CadastroReurb(
            cadastrador=data.get('cadastrador') or current_user.nome,
            req_nome=data.get('req_nome'), req_cpf=data.get('req_cpf'), req_rg=data.get('req_rg'),
            req_data_nasc=data.get('req_data_nasc'), req_nacionalidade=data.get('req_nacionalidade'),
            req_profissao=data.get('req_profissao'), req_telefone=data.get('req_telefone'),
            req_email=data.get('req_email'), req_estado_civil=data.get('req_estado_civil'),
            req_regime_bens=data.get('req_regime_bens'), conj_nome=data.get('conj_nome'),
            conj_cpf=data.get('conj_cpf'), conj_rg=data.get('conj_rg'),
            conj_data_nasc=data.get('conj_data_nasc'), conj_nacionalidade=data.get('conj_nacionalidade'),
            conj_profissao=data.get('conj_profissao'), conj_telefone=data.get('conj_telefone'),
            conj_email=data.get('conj_email'),
            inscricao_imobiliaria=data.get('inscricao_imobiliaria'), imovel_cep=data.get('imovel_cep'),
            imovel_logradouro=data.get('imovel_logradouro'), imovel_numero=data.get('imovel_numero'),
            imovel_complemento=data.get('imovel_complemento'), imovel_bairro=data.get('imovel_bairro'),
            imovel_cidade=data.get('imovel_cidade'), imovel_uf=data.get('imovel_uf'),
            confrontante_frente=data.get('confrontante_frente'), confrontante_fundo=data.get('confrontante_fundo'),
            confrontante_ld=data.get('confrontante_ld'), confrontante_le=data.get('confrontante_le'),
            imovel_medida_frente=to_float(data.get('imovel_medida_frente')),
            imovel_medida_fundo=to_float(data.get('imovel_medida_fundo')),
            imovel_medida_ld=to_float(data.get('imovel_medida_ld')),
            imovel_medida_le=to_float(data.get('imovel_medida_le')),
            imovel_area_total=to_float(data.get('imovel_area_total')),
            imovel_data_ocupacao=data.get('imovel_data_ocupacao'),
            imovel_infra_agua=data.get('imovel_infra_agua'), imovel_infra_esgoto=data.get('imovel_infra_esgoto'),
            imovel_infra_iluminacao=data.get('imovel_infra_iluminacao'),
            imovel_infra_pavimentacao=data.get('imovel_infra_pavimentacao'),
            imovel_infra_lixo=data.get('imovel_infra_lixo'),
            latitude=to_float(data.get('latitude')), longitude=to_float(data.get('longitude')),
            imovel_medidor_agua=data.get('imovel_medidor_agua'), imovel_medidor_luz=data.get('imovel_medidor_luz'),
            imovel_num_habitantes=to_int(data.get('imovel_num_habitantes')),
            imovel_muro=data.get('imovel_muro'), imovel_portoes=data.get('imovel_portoes'),
            imovel_cerca_eletrica=data.get('imovel_cerca_eletrica'), imovel_piscina=data.get('imovel_piscina'),
            risco_inundacao=data.get('risco_inundacao'), risco_deslizamento=data.get('risco_deslizamento'),
            grau_area_risco=data.get('grau_area_risco'), motivo_risco=data.get('motivo_risco'),
            sensacao_termica=data.get('sensacao_termica'), ventilacao_natural=data.get('ventilacao_natural'),
            poluicao_sonora=data.get('poluicao_sonora'),
            reurb_renda_familiar=to_float(data.get('reurb_renda_familiar')),
            reurb_outro_imovel=data.get('reurb_outro_imovel'), reurb_cadunico=data.get('reurb_cadunico'),
            reurb_propriedade=data.get('reurb_propriedade'),
            uso_imovel=data.get('uso_imovel') or data.get('const_uso') or data.get('uso_principal'),
            foto_fachada_url=data.get('foto_fachada_url'),
            doc_rg_cnh_url=data.get('doc_rg_cnh_url'),
            doc_comprovante_url=data.get('doc_comprovante_url'),
            doc_contrato_url=data.get('doc_contrato_url')
        )
        
        construcoes_data = data.get('construcoes', [])
        for const_data in construcoes_data:
            nova_construcao = Construcao(
                nome=const_data.get('nome'),
                area_construida=to_float(const_data.get('area_construida')),
                geometria=(json.dumps(const_data.get('geometria'), ensure_ascii=False, separators=(',', ':'))
                           if isinstance(const_data.get('geometria'), dict) and const_data.get('geometria', {}).get('type') in ('Polygon', 'MultiPolygon') else None),
                uso_principal=const_data.get('uso_principal'),
                padrao_construtivo=const_data.get('padrao_construtivo'),
                tipo_imovel=const_data.get('tipo_imovel'),
                estrutura=const_data.get('estrutura'),
                cobertura=const_data.get('cobertura'),
                instalacao_sanitaria=const_data.get('instalacao_sanitaria'),
                forro=const_data.get('forro'),
                piso=const_data.get('piso'),
                portas=const_data.get('portas'),
                janelas=const_data.get('janelas'),
                revestimento=const_data.get('revestimento')
            )
            novo_cadastro.construcoes.append(nova_construcao)

        db.session.add(novo_cadastro)
        db.session.commit()
        return jsonify({'mensagem': 'Cadastro REURB criado com sucesso!', 'id': novo_cadastro.id}), 201
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Erro ao criar cadastro: {str(e)}")
        return jsonify({'mensagem': f'Erro ao criar cadastro: {str(e)}'}), 400


# ------------------- ATUALIZAÇÃO DE ÁREA VINDO DO QGIS -------------------
@app.route('/api/qgis/atualizar-area-lote/<int:cadastro_id>', methods=['PUT', 'OPTIONS'])
@token_required
def atualizar_area_lote_qgis(current_user, cadastro_id):
    """Atualiza exclusivamente a área do lote a partir da geometria vinculada no QGIS."""
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200

    data = request.get_json(silent=True) or {}
    area = to_float(data.get('imovel_area_total'))

    if area is None or not math.isfinite(area) or area <= 0:
        return jsonify({'erro': 'Área inválida. Informe uma área maior que zero, em m².'}), 400

    cadastro = CadastroReurb.query.get(cadastro_id)
    if not cadastro:
        return jsonify({'erro': 'Cadastro não encontrado.'}), 404

    try:
        # Recebe e valida as coordenadas calculadas pelo plugin QGIS.
        # Se ambas forem enviadas, persiste o centroide no cadastro.
        latitude = to_float(data.get('latitude'))
        longitude = to_float(data.get('longitude'))

        if latitude is not None or longitude is not None:
            if (latitude is None or longitude is None or
                    not math.isfinite(latitude) or not math.isfinite(longitude) or
                    latitude < -90 or latitude > 90 or
                    longitude < -180 or longitude > 180):
                return jsonify({'erro': 'Coordenadas do centroide inválidas.'}), 400

        geometria = data.get('geometria_lote')
        geometria_json = None
        if geometria is not None:
            if isinstance(geometria, str):
                geometria = json.loads(geometria)
            if not isinstance(geometria, dict) or geometria.get('type') not in ('Polygon', 'MultiPolygon'):
                return jsonify({'erro': 'Geometria do lote inválida. Envie GeoJSON Polygon ou MultiPolygon.'}), 400
            geometria_json = json.dumps(geometria, ensure_ascii=False, separators=(',', ':'))

        # Só altera os campos espaciais recebidos; preserva os demais dados cadastrais.
        cadastro.imovel_area_total = area
        if latitude is not None and longitude is not None:
            cadastro.latitude = latitude
            cadastro.longitude = longitude
        if geometria_json is not None:
            cadastro.geometria_lote = geometria_json
        cadastro.data_atualizacao = datetime.datetime.utcnow()
        db.session.commit()

        return jsonify({
            'sucesso': True,
            'mensagem': 'Área, geometria e centroide do lote atualizados a partir do QGIS.',
            'cadastro_id': cadastro.id,
            'imovel_area_total': cadastro.imovel_area_total,
            'latitude': cadastro.latitude,
            'longitude': cadastro.longitude
        }), 200
    except Exception as e:
        db.session.rollback()
        app.logger.error(f'Erro ao atualizar área do lote {cadastro_id} via QGIS: {str(e)}')
        return jsonify({'erro': 'Não foi possível salvar a área do lote.'}), 500


# ------------------- INTEGRAÇÃO DE CONSTRUÇÕES COM O QGIS -------------------
@app.route('/api/qgis/construcoes/<int:cadastro_id>', methods=['GET', 'POST', 'OPTIONS'])
@token_required
def gerenciar_construcoes_qgis(current_user, cadastro_id):
    """Lista ou cria uma construção sem substituir as demais informações do cadastro."""
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200

    cadastro = CadastroReurb.query.get(cadastro_id)
    if not cadastro:
        return jsonify({'erro': 'Cadastro/proprietário não encontrado.'}), 404

    if request.method == 'GET':
        construcoes = Construcao.query.filter_by(cadastro_id=cadastro_id).order_by(Construcao.id.asc()).all()
        resultado = []
        for construcao in construcoes:
            item = {col.name: getattr(construcao, col.name) for col in construcao.__table__.columns}
            resultado.append(item)
        return jsonify({'sucesso': True, 'cadastro_id': cadastro_id, 'construcoes': resultado}), 200

    data = request.get_json(silent=True) or {}
    nome = (data.get('nome') or '').strip()
    padrao = (data.get('padrao_construtivo') or '').strip()
    area = to_float(data.get('area_construida'))
    if not nome:
        return jsonify({'erro': 'Informe o nome/identificação da construção.'}), 400
    if area is None or not math.isfinite(area) or area <= 0:
        return jsonify({'erro': 'Área construída inválida. Informe uma área maior que zero.'}), 400
    if not padrao:
        return jsonify({'erro': 'Informe o padrão construtivo.'}), 400

    try:
        nova = Construcao(
            cadastro_id=cadastro_id,
            nome=nome,
            area_construida=area,
            geometria=(json.dumps(data.get('geometria'), ensure_ascii=False, separators=(',', ':'))
                       if isinstance(data.get('geometria'), dict) and data.get('geometria', {}).get('type') in ('Polygon', 'MultiPolygon') else None),
            uso_principal=data.get('uso_principal'),
            padrao_construtivo=padrao,
            tipo_imovel=data.get('tipo_imovel'),
            estrutura=data.get('estrutura'),
            cobertura=data.get('cobertura'),
            instalacao_sanitaria=data.get('instalacao_sanitaria'),
            forro=data.get('forro'),
            piso=data.get('piso'),
            portas=data.get('portas'),
            janelas=data.get('janelas'),
            revestimento=data.get('revestimento')
        )
        db.session.add(nova)
        db.session.commit()
        return jsonify({
            'sucesso': True,
            'mensagem': 'Construção cadastrada com sucesso.',
            'construcao': {col.name: getattr(nova, col.name) for col in nova.__table__.columns}
        }), 201
    except Exception as e:
        db.session.rollback()
        app.logger.error(f'Erro ao cadastrar construção via QGIS para cadastro {cadastro_id}: {str(e)}')
        return jsonify({'erro': 'Não foi possível cadastrar a construção.'}), 500


@app.route('/api/qgis/atualizar-area-construcao/<int:construcao_id>', methods=['PUT', 'OPTIONS'])
@token_required
def atualizar_area_construcao_qgis(current_user, construcao_id):
    """Atualiza somente a área construída de uma construção vinculada no QGIS."""
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200

    data = request.get_json(silent=True) or {}
    area = to_float(data.get('area_construida'))
    if area is None or not math.isfinite(area) or area <= 0:
        return jsonify({'erro': 'Área construída inválida. Informe um valor maior que zero, em m².'}), 400

    construcao = Construcao.query.get(construcao_id)
    if not construcao:
        return jsonify({'erro': 'Construção não encontrada.'}), 404

    try:
        construcao.area_construida = area
        geometria = data.get('geometria')
        if geometria is not None:
            if isinstance(geometria, str):
                geometria = json.loads(geometria)
            if not isinstance(geometria, dict) or geometria.get('type') not in ('Polygon', 'MultiPolygon'):
                return jsonify({'erro': 'Geometria da construção inválida. Envie GeoJSON Polygon ou MultiPolygon.'}), 400
            construcao.geometria = json.dumps(geometria, ensure_ascii=False, separators=(',', ':'))
        cadastro = CadastroReurb.query.get(construcao.cadastro_id)
        if cadastro:
            cadastro.data_atualizacao = datetime.datetime.utcnow()
        db.session.commit()
        return jsonify({
            'sucesso': True,
            'mensagem': 'Área da construção atualizada a partir do QGIS.',
            'construcao_id': construcao.id,
            'cadastro_id': construcao.cadastro_id,
            'area_construida': construcao.area_construida
        }), 200
    except Exception as e:
        db.session.rollback()
        app.logger.error(f'Erro ao atualizar área da construção {construcao_id} via QGIS: {str(e)}')
        return jsonify({'erro': 'Não foi possível atualizar a área da construção.'}), 500


@app.route('/api/cadastros', methods=['GET'])
@token_required
def get_cadastros(current_user):
    cadastros = CadastroReurb.query.options(joinedload(CadastroReurb.construcoes)).order_by(CadastroReurb.id.desc()).all()
    output = []
    
    for c in cadastros:
        cadastro_data = {col.name: getattr(c, col.name) for col in c.__table__.columns if col.name not in ['imovel_estrutura', 'imovel_cobertura', 'imovel_instalacao_sanitaria', 'imovel_forro', 'imovel_piso', 'imovel_portas', 'imovel_janelas', 'imovel_revestimento']}

        valores = CalculoTributarioService.calcular_valores(c)
        renda, outro_imovel = c.reurb_renda_familiar or 0, c.reurb_outro_imovel or ''
        tipo_reurb = 'REURB-S' if renda <= 7500 and outro_imovel.lower() == 'nao' else 'REURB-E'

        cadastro_data['tipo_reurb'] = tipo_reurb
        cadastro_data.update(valores)

        total_area_construida = 0
        construcoes_list = []
        for construcao in c.construcoes:
            total_area_construida += construcao.area_construida or 0
            construcoes_list.append({col.name: getattr(construcao, col.name) for col in construcao.__table__.columns})
        
        cadastro_data['construcoes'] = construcoes_list
        cadastro_data['imovel_area_construida'] = total_area_construida

        for key, value in cadastro_data.items():
            if isinstance(value, (datetime.datetime, datetime.date)):
                cadastro_data[key] = value.isoformat()

        output.append(cadastro_data)
        
    return jsonify({'cadastros': output})

@app.route('/api/cadastros/por_inscricao/<inscricao_imobiliaria>', methods=['GET'])
@token_required
def get_cadastro_por_inscricao(current_user, inscricao_imobiliaria):
    cadastro = CadastroReurb.query.filter_by(inscricao_imobiliaria=inscricao_imobiliaria).first()
    if not cadastro:
        return jsonify({'erro': 'Inscrição Imobiliária não encontrada'}), 404
    
    return jsonify({
        'id': cadastro.id,
        'req_nome': cadastro.req_nome,
        'req_cpf': cadastro.req_cpf,
        'inscricao_imobiliaria': cadastro.inscricao_imobiliaria
    })

@app.route('/api/cadastros/<int:id>', methods=['GET', 'PUT', 'DELETE'])
@token_required
def gerenciar_cadastro_por_id(current_user, id):
    cadastro = CadastroReurb.query.options(joinedload(CadastroReurb.documentos), joinedload(CadastroReurb.construcoes)).get_or_404(id)
    
    if request.method == 'GET':
        cadastro_data = {col.name: getattr(cadastro, col.name) for col in cadastro.__table__.columns if col.name not in ['imovel_estrutura', 'imovel_cobertura', 'imovel_instalacao_sanitaria', 'imovel_forro', 'imovel_piso', 'imovel_portas', 'imovel_janelas', 'imovel_revestimento']}
        
        # Cálculo tributário e tipo_reurb inseridos para o Visualizador por ID
        valores = CalculoTributarioService.calcular_valores(cadastro)
        renda = cadastro.reurb_renda_familiar or 0
        outro_imovel = cadastro.reurb_outro_imovel or ''
        tipo_reurb = 'REURB-S' if renda <= 7500 and outro_imovel.lower() == 'nao' else 'REURB-E'

        cadastro_data['tipo_reurb'] = tipo_reurb
        cadastro_data.update(valores)

        cadastro_data['documentos'] = [{'id': d.id, 'nome_arquivo': d.nome_arquivo, 'tipo_documento': d.tipo_documento} for d in cadastro.documentos]
        
        total_area_construida = 0
        construcoes_list = []
        for construcao in cadastro.construcoes:
            total_area_construida += construcao.area_construida or 0
            construcoes_list.append({col.name: getattr(construcao, col.name) for col in construcao.__table__.columns})
        
        cadastro_data['construcoes'] = construcoes_list
        cadastro_data['imovel_area_construida'] = total_area_construida

        for key, value in cadastro_data.items():
            if isinstance(value, (datetime.datetime, datetime.date)):
                cadastro_data[key] = value.isoformat()
        return jsonify(cadastro_data)

    if request.method == 'PUT':
        data = request.get_json()
        for key, value in data.items():
            if hasattr(cadastro, key) and key not in ['id', 'construcoes', 'documentos']:
                if key in ['latitude', 'longitude', 'imovel_medida_frente', 'imovel_medida_fundo', 'imovel_medida_ld', 'imovel_medida_le', 'imovel_area_total', 'reurb_renda_familiar']:
                    setattr(cadastro, key, to_float(value))
                elif key == 'imovel_num_habitantes':
                    setattr(cadastro, key, to_int(value))
                else:
                    setattr(cadastro, key, value)
        
        Construcao.query.filter_by(cadastro_id=id).delete()
        
        construcoes_data = data.get('construcoes', [])
        for const_data in construcoes_data:
            nova_construcao = Construcao(
                cadastro_id=id,
                nome=const_data.get('nome'),
                area_construida=to_float(const_data.get('area_construida')),
                geometria=(json.dumps(const_data.get('geometria'), ensure_ascii=False, separators=(',', ':'))
                           if isinstance(const_data.get('geometria'), dict) and const_data.get('geometria', {}).get('type') in ('Polygon', 'MultiPolygon') else None),
                uso_principal=const_data.get('uso_principal'),
                padrao_construtivo=const_data.get('padrao_construtivo'),
                tipo_imovel=const_data.get('tipo_imovel'),
                estrutura=const_data.get('estrutura'),
                cobertura=const_data.get('cobertura'),
                instalacao_sanitaria=const_data.get('instalacao_sanitaria'),
                forro=const_data.get('forro'),
                piso=const_data.get('piso'),
                portas=const_data.get('portas'),
                janelas=const_data.get('janelas'),
                revestimento=const_data.get('revestimento')
            )
            db.session.add(nova_construcao)

        db.session.commit()

        # Retorna também os valores atualizados no PUT
        valores = CalculoTributarioService.calcular_valores(cadastro)
        renda = cadastro.reurb_renda_familiar or 0
        outro_imovel = cadastro.reurb_outro_imovel or ''
        tipo_reurb = 'REURB-S' if renda <= 7500 and outro_imovel.lower() == 'nao' else 'REURB-E'

        return jsonify({
            'mensagem': 'Cadastro atualizado com sucesso!',
            'tipo_reurb': tipo_reurb,
            **valores
        })

    if request.method == 'DELETE':
        for doc in cadastro.documentos:
            if os.path.exists(doc.path_arquivo):
                os.remove(doc.path_arquivo)
        
        db.session.delete(cadastro)
        db.session.commit()
        return jsonify({'mensagem': 'Cadastro deletado com sucesso!'})
# ------------------- GERENCIAMENTO DE USUÁRIOS (ADMIN) -------------------
@app.route('/api/usuarios', methods=['GET', 'POST'])
@token_required
@admin_required
def gerenciar_usuarios(current_user):
    if request.method == 'GET':
        usuarios = Usuario.query.all()
        output = [{'id': u.id, 'nome': u.nome, 'usuario': u.usuario, 'acesso': u.acesso} for u in usuarios]
        return jsonify({'usuarios': output})
    if request.method == 'POST':
        data = request.get_json()
        try:
            novo_usuario = Usuario(nome=data['nome'], usuario=data['usuario'], senha=data['senha'], acesso=data['acesso'])
            db.session.add(novo_usuario)
            db.session.commit()
            return jsonify({'mensagem': 'Usuário criado com sucesso!'}), 201
        except Exception as e:
            return jsonify({'mensagem': f'Erro ao criar usuário: {e}'}), 400

@app.route('/api/usuarios/<int:id>', methods=['GET', 'PUT', 'DELETE'])
@token_required
@admin_required
def gerenciar_usuario_por_id(current_user, id):
    usuario = Usuario.query.get_or_404(id)
    if request.method == 'GET':
        return jsonify({'id': usuario.id, 'nome': usuario.nome, 'usuario': usuario.usuario, 'acesso': usuario.acesso})
    if request.method == 'PUT':
        data = request.get_json()
        usuario.nome = data.get('nome', usuario.nome)
        usuario.usuario = data.get('usuario', usuario.usuario)
        usuario.acesso = data.get('acesso', usuario.acesso)
        if 'senha' in data and data['senha']:
            usuario.senha_hash = generate_password_hash(data['senha'], method="scrypt")
        db.session.commit()
        return jsonify({'mensagem': 'Usuário atualizado com sucesso!'})
    if request.method == 'DELETE':
        db.session.delete(usuario)
        db.session.commit()
        return jsonify({'mensagem': 'Usuário deletado com sucesso!'})


# ------------------- PLANTA GENÉRICA DE VALORES -------------------
@app.route('/api/planta_generica/<tipo>', methods=['GET', 'POST'])
@token_required
def pgv_geral(current_user, tipo):
    model_map = {'padroes': PadraoConstrutivo, 'logradouros': ValorLogradouro, 'aliquotas': AliquotaIPTU}
    if tipo not in model_map: return jsonify({'erro': 'Tipo inválido'}), 404
    Model = model_map[tipo]
    if request.method == 'POST':
        if current_user.acesso != 'Administrador': return jsonify({'erro': 'Acesso negado'}), 403
        data = request.get_json()
        try:
            novo_item = Model(**data)
            db.session.add(novo_item)
            db.session.commit()
            return jsonify({'sucesso': True, 'mensagem': f'{tipo.capitalize()} adicionado(a) com sucesso!'}), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({'erro': f'Erro ao adicionar: {str(e)}'}), 400
    items = Model.query.all()
    items_dict = [{c.name: getattr(item, c.name) for c in item.__table__.columns} for item in items]
    return jsonify(items_dict)

@app.route('/api/planta_generica/<tipo>/<int:id>', methods=['DELETE'])
@token_required
@admin_required
def delete_pgv_item(current_user, tipo, id):
    model_map = {'padroes': PadraoConstrutivo, 'logradouros': ValorLogradouro, 'aliquotas': AliquotaIPTU}
    if tipo not in model_map: return jsonify({'erro': 'Tipo inválido'}), 404
    Model = model_map[tipo]
    item = Model.query.get_or_404(id)
    db.session.delete(item)
    db.session.commit()
    return jsonify({'sucesso': True, 'mensagem': 'Item deletado com sucesso!'})

# =======================================================================
# ===== GUIAS IPTU E ESTATÍSTICAS =====
# =======================================================================

@app.route('/api/guias/por_cadastro/<int:cadastro_id>', methods=['GET'])
@token_required
def listar_guias_por_cadastro(current_user, cadastro_id):
    guias = GuiaIPTU.query.filter_by(cadastro_id=cadastro_id).order_by(GuiaIPTU.ano_exercicio.desc()).all()
    return jsonify([guia.to_dict() for guia in guias])

@app.route('/api/guias/emitir', methods=['POST'])
@token_required
def emitir_guia(current_user):
    data = request.get_json()
    cadastro_id = data.get('cadastro_id')
    ano_exercicio = data.get('ano_exercicio')

    if not cadastro_id or not ano_exercicio:
        return jsonify({'erro': 'ID do cadastro e ano de exercício são obrigatórios.'}), 400

    cadastro = CadastroReurb.query.get(cadastro_id)
    if not cadastro:
        return jsonify({'erro': 'Cadastro não encontrado.'}), 404
    
    guia_existente = GuiaIPTU.query.filter_by(cadastro_id=cadastro_id, ano_exercicio=ano_exercicio).first()
    if guia_existente:
        return jsonify({'erro': f'Já existe uma guia emitida para o ano {ano_exercicio}.'}), 409

    valores_tributarios = CalculoTributarioService.calcular_valores(cadastro)
    valor_iptu = valores_tributarios.get('iptu', 0)

    if valor_iptu <= 0:
        return jsonify({'erro': 'O valor do IPTU calculado é zero ou negativo. Guia não emitida.'}), 400

    nova_guia = GuiaIPTU(
        cadastro_id=cadastro_id,
        ano_exercicio=ano_exercicio,
        valor_emitido=valor_iptu,
        situacao='Em aberto'
    )
    db.session.add(nova_guia)
    db.session.commit()

    return jsonify({'mensagem': 'Guia de IPTU emitida com sucesso!', 'guia': nova_guia.to_dict()}), 201

@app.route('/api/guias/atualizar_situacao/<int:guia_id>', methods=['PUT'])
@token_required
def atualizar_situacao_guia(current_user, guia_id):
    data = request.get_json()
    nova_situacao = data.get('situacao')

    if not nova_situacao or nova_situacao not in ['Pago', 'Em aberto']:
        return jsonify({'erro': 'Situação inválida. Use "Pago" ou "Em aberto".'}), 400

    guia = GuiaIPTU.query.get(guia_id)
    if not guia:
        return jsonify({'erro': 'Guia não encontrada.'}), 404
    
    guia.situacao = nova_situacao
    db.session.commit()
    
    return jsonify({'mensagem': f'Situação da guia {guia_id} atualizada para "{nova_situacao}".'})


@app.route('/api/guias/<int:guia_id>', methods=['DELETE'])
@token_required
def excluir_guia(current_user, guia_id):
    guia = GuiaIPTU.query.get_or_404(guia_id)
    try:
        db.session.delete(guia)
        db.session.commit()
        return jsonify({'mensagem': 'Guia de IPTU excluída com sucesso!'})
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Erro ao excluir guia {guia_id}: {str(e)}")
        return jsonify({'erro': 'Ocorreu um erro ao tentar excluir a guia.'}), 500

@app.route('/api/guias/todas', methods=['GET'])
@token_required
def listar_todas_as_guias(current_user):
    try:
        guias_com_info = db.session.query(
            GuiaIPTU,
            CadastroReurb.req_nome,
            CadastroReurb.req_cpf,
            CadastroReurb.inscricao_imobiliaria
        ).join(CadastroReurb, GuiaIPTU.cadastro_id == CadastroReurb.id).order_by(GuiaIPTU.id.desc()).all()

        resultado = []
        for guia, nome, cpf, inscricao in guias_com_info:
            resultado.append({
                'id_guia': guia.id,
                'cadastro_id': guia.cadastro_id,
                'ano_exercicio': guia.ano_exercicio,
                'valor_emitido': guia.valor_emitido,
                'data_emissao': guia.data_emissao.strftime('%d/%m/%Y %H:%M:%S'),
                'situacao': guia.situacao,
                'nome_proprietario': nome,
                'cpf': cpf,
                'inscricao_imobiliaria': inscricao
            })
            
        return jsonify(resultado)

    except Exception as e:
        app.logger.error(f"Erro ao buscar todas as guias: {str(e)}")
        return jsonify({'erro': 'Erro interno ao buscar as guias.'}), 500

@app.route('/api/estatisticas/iptu', methods=['GET'])
@token_required
def get_estatisticas_iptu(current_user):
    total_arrecadado_query = db.session.query(func.sum(GuiaIPTU.valor_emitido)).filter(GuiaIPTU.situacao == 'Pago').scalar()
    total_arrecadado = total_arrecadado_query or 0.0

    total_em_aberto_query = db.session.query(func.sum(GuiaIPTU.valor_emitido)).filter(GuiaIPTU.situacao == 'Em aberto').scalar()
    total_em_aberto = total_em_aberto_query or 0.0

    return jsonify({
        'iptu_arrecadado': total_arrecadado,
        'iptu_em_aberto': total_em_aberto
    })

# ------------------- CÁLCULO E IMPORTAÇÃO/EXPORTAÇÃO -------------------
@app.route('/api/gerar_iptu/<inscricao_imobiliaria>', methods=['GET'])
@token_required
def gerar_iptu(current_user, inscricao_imobiliaria):
    cadastro = CadastroReurb.query.filter_by(inscricao_imobiliaria=inscricao_imobiliaria).first_or_404()
    valores = CalculoTributarioService.calcular_valores(cadastro)
    return jsonify(valores)


def _serializar_valor_backup(valor):
    if isinstance(valor, (datetime.datetime, datetime.date)):
        return valor.isoformat()
    return valor


def _colunas_modelo(modelo):
    return [col.name for col in modelo.__table__.columns]


def _json_backup(valor):
    return json.dumps(valor, ensure_ascii=False, separators=(',', ':'), default=_serializar_valor_backup)


def _dados_auxiliares_backup():
    return {
        'padroes_construtivos': [
            {'id': x.id, 'descricao': x.descricao, 'valor_m2': x.valor_m2}
            for x in PadraoConstrutivo.query.order_by(PadraoConstrutivo.id).all()
        ],
        'valores_logradouro': [
            {'id': x.id, 'logradouro': x.logradouro, 'valor_m2': x.valor_m2}
            for x in ValorLogradouro.query.order_by(ValorLogradouro.id).all()
        ],
        'aliquotas_iptu': [
            {'id': x.id, 'tipo': x.tipo, 'aliquota': x.aliquota}
            for x in AliquotaIPTU.query.order_by(AliquotaIPTU.id).all()
        ]
    }


@app.route('/api/exportar/campos', methods=['GET'])
@token_required
def listar_campos_exportacao(current_user):
    """Entrega à interface TODOS os campos reais do cadastro e campos relacionados."""
    campos = []
    for coluna in CadastroReurb.__table__.columns:
        campos.append({'id': coluna.name, 'nome': coluna.name.replace('_', ' ').capitalize(), 'grupo': 'Cadastro'})
    for chave, rotulo in [
        ('construcoes_json', 'Construções (atributos + geometrias)'),
        ('guias_iptu_json', 'Guias IPTU (histórico e situação)'),
        ('documentos_json', 'Documentos anexados (metadados e arquivos)'),
        ('vvt', 'VVT - Valor Venal do Terreno'),
        ('vvc', 'VVC - Valor Venal das Construções'),
        ('vvi', 'VVI - Valor Venal do Imóvel'),
        ('iptu', 'IPTU calculado'),
        ('tipo_reurb', 'Classificação REURB'),
        ('imovel_area_construida', 'Área construída total'),
    ]:
        campos.append({'id': chave, 'nome': rotulo, 'grupo': 'Dados complementares'})
    return jsonify(campos)


@app.route('/api/exportar', methods=['POST'])
@token_required
def exportar_dados(current_user):
    """
    Exporta CSV UTF-8 com separador ;. Cada cadastro ocupa uma linha.
    Os relacionamentos e GeoJSON são preservados em colunas JSON, permitindo
    restauração integral sem perder a estrutura relacional.
    """
    try:
        data = request.get_json(silent=True) or {}
        colunas_solicitadas = data.get('colunas') or []
        exportacao_completa = bool(data.get('backup_completo'))

        colunas_cadastro = _colunas_modelo(CadastroReurb)
        colunas_especiais = [
            'construcoes_json', 'guias_iptu_json', 'documentos_json',
            'vvt', 'vvc', 'vvi', 'iptu', 'tipo_reurb',
            'imovel_area_construida', '__backup_metadata'
        ]
        permitidas = set(colunas_cadastro + colunas_especiais)
        if exportacao_completa:
            colunas = colunas_cadastro + colunas_especiais
        else:
            colunas = [c for c in colunas_solicitadas if c in permitidas and c != '__backup_metadata']
            if not colunas:
                return jsonify({'erro': 'Selecione pelo menos um campo para exportar.'}), 400

        cadastros = CadastroReurb.query.order_by(CadastroReurb.id).all()
        metadata = _dados_auxiliares_backup()
        metadata_json = _json_backup({
            'formato': 'CM_REURB_FULL_V1',
            'exportado_em': datetime.datetime.utcnow().isoformat() + 'Z',
            **metadata
        })

        output = io.StringIO(newline='')
        writer = csv.DictWriter(output, fieldnames=colunas, delimiter=';', quotechar='"',
                                quoting=csv.QUOTE_MINIMAL, lineterminator='\r\n')
        writer.writeheader()

        # Mesmo sem cadastros, o arquivo completo leva as tabelas auxiliares.
        if not cadastros and exportacao_completa:
            writer.writerow({'__backup_metadata': metadata_json})

        for cadastro in cadastros:
            linha = {col.name: _serializar_valor_backup(getattr(cadastro, col.name))
                     for col in CadastroReurb.__table__.columns}

            construcoes = []
            for item in sorted(cadastro.construcoes, key=lambda x: x.id):
                construcoes.append({col.name: _serializar_valor_backup(getattr(item, col.name))
                                    for col in Construcao.__table__.columns})

            guias = []
            for item in sorted(cadastro.guias_iptu, key=lambda x: x.id):
                guias.append({col.name: _serializar_valor_backup(getattr(item, col.name))
                              for col in GuiaIPTU.__table__.columns})

            documentos = []
            for item in sorted(cadastro.documentos, key=lambda x: x.id):
                doc = {col.name: _serializar_valor_backup(getattr(item, col.name))
                       for col in Documento.__table__.columns}
                caminho = item.path_arquivo
                if caminho and os.path.isfile(caminho):
                    try:
                        with open(caminho, 'rb') as arquivo:
                            doc['arquivo_base64'] = base64.b64encode(arquivo.read()).decode('ascii')
                    except OSError as erro:
                        app.logger.warning(f'Não foi possível incluir arquivo {caminho} no backup: {erro}')
                documentos.append(doc)

            valores = CalculoTributarioService.calcular_valores(cadastro)
            renda = cadastro.reurb_renda_familiar or 0
            outro_imovel = (cadastro.reurb_outro_imovel or '').strip().lower()
            linha.update({
                'construcoes_json': _json_backup(construcoes),
                'guias_iptu_json': _json_backup(guias),
                'documentos_json': _json_backup(documentos),
                'vvt': valores.get('vvt', 0),
                'vvc': valores.get('vvc', 0),
                'vvi': valores.get('vvi', 0),
                'iptu': valores.get('iptu', 0),
                'tipo_reurb': 'REURB-S' if renda <= 7500 and outro_imovel in ('nao', 'não') else 'REURB-E',
                'imovel_area_construida': sum(x.area_construida or 0 for x in cadastro.construcoes),
                '__backup_metadata': metadata_json if exportacao_completa else ''
            })
            writer.writerow({campo: linha.get(campo, '') for campo in colunas})

        conteudo = '\ufeff' + output.getvalue()
        arquivo_saida = io.BytesIO(conteudo.encode('utf-8'))
        arquivo_saida.seek(0)
        nome = 'backup_completo_cm_reurb.csv' if exportacao_completa else 'exportacao_cadastros_reurb.csv'
        return send_file(arquivo_saida, mimetype='text/csv; charset=utf-8',
                         as_attachment=True, download_name=nome)
    except Exception as e:
        db.session.rollback()
        app.logger.exception('Erro ao exportar dados REURB')
        return jsonify({'erro': f'Erro ao exportar dados: {str(e)}'}), 500


def _converter_valor_coluna_backup(coluna, valor):
    if valor is None or valor == '':
        return None
    tipo = coluna.type
    try:
        if isinstance(tipo, db.Integer):
            return int(float(valor))
        if isinstance(tipo, db.Float):
            return float(str(valor).replace(',', '.'))
        if isinstance(tipo, db.DateTime):
            return datetime.datetime.fromisoformat(str(valor).replace('Z', '+00:00')).replace(tzinfo=None)
        if isinstance(tipo, db.Date):
            return datetime.date.fromisoformat(str(valor)[:10])
    except (ValueError, TypeError):
        raise ValueError(f'Valor inválido para o campo {coluna.name}: {valor}')
    return valor


@app.route('/api/importar', methods=['POST'])
@token_required
@admin_required
def importar_dados(current_user):
    if 'arquivo' not in request.files:
        return jsonify({'erro': 'Nenhum arquivo enviado.'}), 400
    arquivo = request.files['arquivo']
    if not arquivo.filename:
        return jsonify({'erro': 'Nome de arquivo vazio.'}), 400

    try:
        nome = arquivo.filename.lower()
        if nome.endswith('.csv'):
            texto = arquivo.stream.read().decode('utf-8-sig')
            amostra = texto[:8192]
            try:
                dialeto = csv.Sniffer().sniff(amostra, delimiters=';,\t')
                leitor = csv.DictReader(io.StringIO(texto), dialect=dialeto)
            except csv.Error:
                leitor = csv.DictReader(io.StringIO(texto), delimiter=';')
            linhas = list(leitor)
        else:
            df = pd.read_excel(arquivo, dtype=object)
            linhas = df.where(pd.notnull(df), None).to_dict(orient='records')

        if not linhas:
            return jsonify({'erro': 'O arquivo não contém registros.'}), 400

        primeira = linhas[0]
        metadados = None
        if primeira.get('__backup_metadata'):
            metadados = json.loads(primeira['__backup_metadata'])

        if metadados and metadados.get('formato') == 'CM_REURB_FULL_V1':
            # Restauração completa é destinada a banco vazio: evita duplicar ou sobrescrever
            # silenciosamente dados já existentes.
            tabelas_com_dados = [
                CadastroReurb.query.first(), Construcao.query.first(), GuiaIPTU.query.first(),
                Documento.query.first(), PadraoConstrutivo.query.first(),
                ValorLogradouro.query.first(), AliquotaIPTU.query.first()
            ]
            if any(tabelas_com_dados):
                return jsonify({'erro': 'A restauração integral exige um banco REURB vazio (cadastros, IPTU e PGV). Nenhum dado foi alterado.'}), 409

            colunas_por_nome = {c.name: c for c in CadastroReurb.__table__.columns}
            ids_cadastros = set()
            for numero_linha, linha in enumerate(linhas, start=2):
                if not any(linha.get(campo) not in (None, '') for campo in colunas_por_nome):
                    continue
                dados_cadastro = {
                    nome_campo: _converter_valor_coluna_backup(coluna, linha.get(nome_campo))
                    for nome_campo, coluna in colunas_por_nome.items()
                    if nome_campo in linha and linha.get(nome_campo) not in (None, '')
                }
                cadastro_id = dados_cadastro.get('id')
                if cadastro_id is None:
                    raise ValueError(f'Linha {numero_linha}: backup integral sem ID do cadastro.')
                if cadastro_id in ids_cadastros:
                    raise ValueError(f'Linha {numero_linha}: ID de cadastro duplicado ({cadastro_id}).')
                ids_cadastros.add(cadastro_id)
                cadastro = CadastroReurb(**dados_cadastro)
                db.session.add(cadastro)
                db.session.flush()

                for campo_json, modelo in [('construcoes_json', Construcao), ('guias_iptu_json', GuiaIPTU)]:
                    bruto = linha.get(campo_json)
                    itens = json.loads(bruto) if bruto else []
                    colunas_rel = {c.name: c for c in modelo.__table__.columns}
                    for item in itens:
                        dados_rel = {
                            nome_campo: _converter_valor_coluna_backup(coluna, item.get(nome_campo))
                            for nome_campo, coluna in colunas_rel.items()
                            if nome_campo in item and item.get(nome_campo) not in (None, '')
                        }
                        dados_rel['cadastro_id'] = cadastro.id
                        db.session.add(modelo(**dados_rel))

                docs = json.loads(linha.get('documentos_json') or '[]')
                for doc in docs:
                    conteudo_b64 = doc.pop('arquivo_base64', None)
                    nome_arquivo = secure_filename(doc.get('nome_arquivo') or f'documento_{cadastro.id}')
                    nome_destino = f"backup_{cadastro.id}_{doc.get('id', 'doc')}_{nome_arquivo}"
                    caminho = os.path.join(app.config['UPLOAD_FOLDER'], nome_destino)
                    if conteudo_b64:
                        with open(caminho, 'wb') as destino:
                            destino.write(base64.b64decode(conteudo_b64))
                        doc['path_arquivo'] = caminho
                    elif doc.get('path_arquivo'):
                        # Caminho antigo não é válido no novo servidor; mantém referência como metadado.
                        doc['path_arquivo'] = os.path.join(app.config['UPLOAD_FOLDER'], nome_destino)
                    colunas_doc = {c.name: c for c in Documento.__table__.columns}
                    dados_doc = {
                        nome_campo: _converter_valor_coluna_backup(coluna, doc.get(nome_campo))
                        for nome_campo, coluna in colunas_doc.items()
                        if nome_campo in doc and doc.get(nome_campo) not in (None, '')
                    }
                    dados_doc['cadastro_id'] = cadastro.id
                    if not dados_doc.get('path_arquivo'):
                        dados_doc['path_arquivo'] = os.path.join(app.config['UPLOAD_FOLDER'], nome_destino)
                    db.session.add(Documento(**dados_doc))

            # Recria as tabelas de referência que alimentam VVT/VVC/IPTU.
            for item in metadados.get('padroes_construtivos', []):
                db.session.add(PadraoConstrutivo(**item))
            for item in metadados.get('valores_logradouro', []):
                db.session.add(ValorLogradouro(**item))
            for item in metadados.get('aliquotas_iptu', []):
                db.session.add(AliquotaIPTU(**item))

            db.session.commit()
            # Sincroniza sequências PostgreSQL após restaurar IDs explícitos.
            if db.engine.dialect.name == 'postgresql':
                for tabela in ('cadastros_reurb', 'construcoes', 'guias_iptu', 'documentos',
                               'padroes_construtivos', 'valores_logradouro', 'aliquotas_iptu'):
                    db.session.execute(sql_text(
                        "SELECT setval(pg_get_serial_sequence(:tabela, 'id'), "
                        "COALESCE((SELECT MAX(id) FROM " + tabela + "), 1), "
                        "(SELECT MAX(id) IS NOT NULL FROM " + tabela + "))"
                    ), {'tabela': tabela})
                db.session.commit()
            return jsonify({'mensagem': 'Backup integral restaurado com sucesso!', 'cadastros': len(ids_cadastros)}), 200

        # Importação convencional de planilhas continua disponível e mantém o fluxo antigo.
        column_mapping = {
            'Nome do Requerente': 'req_nome', 'CPF do Requerente': 'req_cpf',
            'Inscrição Imobiliária': 'inscricao_imobiliaria',
            'Área Total do Lote (m²)': 'imovel_area_total',
            'Renda Familiar (R$)': 'reurb_renda_familiar'
        }
        for linha in linhas:
            linha = {column_mapping.get(k, k): v for k, v in linha.items()}
            valid_data = {k: v for k, v in linha.items()
                          if k in CadastroReurb.__table__.columns.keys()}
            db.session.add(CadastroReurb(**valid_data))
        db.session.commit()
        return jsonify({'mensagem': f'{len(linhas)} registros importados com sucesso.'}), 200
    except Exception as e:
        db.session.rollback()
        app.logger.exception('Erro ao importar arquivo REURB')
        return jsonify({'erro': f'Erro ao importar dados: {str(e)}'}), 500

# ------------------- UPLOAD DE DOCUMENTOS -------------------
@app.route('/api/upload_documento/<int:id>', methods=['POST'])
@token_required
def upload_documento(current_user, id):
    cadastro = CadastroReurb.query.get_or_404(id)
    if 'file' not in request.files: return jsonify({'mensagem': 'Nenhum arquivo enviado'}), 400
    file = request.files['file']
    if file.filename == '': return jsonify({'mensagem': 'Nome de arquivo vazio'}), 400
    if file:
        filename_base, file_extension = os.path.splitext(file.filename)
        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        filename = secure_filename(f"{filename_base}_{timestamp}{file_extension}")
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        novo_documento = Documento(
            cadastro_id=cadastro.id, nome_arquivo=filename, path_arquivo=filepath,
            tipo_documento=request.form.get('tipo_documento', 'Não especificado'))
        db.session.add(novo_documento)
        db.session.commit()
        return jsonify({'mensagem': 'Documento enviado com sucesso!', 'nome_arquivo': filename}), 201

@app.route('/api/documento/<int:documento_id>', methods=['DELETE'])
@token_required
def deletar_documento(current_user, documento_id):
    try:
        doc = Documento.query.get_or_404(documento_id)
        filepath = doc.path_arquivo
        db.session.delete(doc)
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
        db.session.commit()
        return jsonify({'mensagem': 'Documento removido com sucesso!'})
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Erro ao excluir documento {documento_id}: {str(e)}")
        return jsonify({'erro': 'Ocorreu um erro ao tentar remover o documento.'}), 500

@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# =======================================================================
# ROTA PARA SETUP INICIAL (USAR COM CUIDADO)
# =======================================================================
@app.route('/setup')
def setup_database():
    try:
        with app.app_context():
            db.create_all()
            garantir_colunas_geometrias()
            if not Usuario.query.filter_by(usuario="admin").first():
                admin_user = Usuario(nome="Administrador", usuario="admin", senha="admin", acesso="Administrador")
                db.session.add(admin_user)
                db.session.commit()
                return "Banco de dados e usuário admin criados! Login: admin / Senha: admin"
            return "Tabelas já criadas e usuário 'admin' já existe!"
    except Exception as e:
        return f"Erro ao configurar o banco de dados: {str(e)}"

# Inicia a rotina quando o módulo é carregado pelo Gunicorn/Flask.
start_keep_alive()

# =======================================================================
# INICIALIZAÇÃO
# =======================================================================
if __name__ == '__main__':
    with app.app_context():
        print("Executando em modo de desenvolvimento local...")
    app.run(host='0.0.0.0', port=5000, debug=True)
