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
from functools import wraps
import jwt  # PyJWT
import io  # Necessário para a função de exportar

import pandas as pd
from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func  # Importação para uso em estatísticas e cálculos
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

# Se não encontrar a variável de ambiente, usa a string de conexão diretamente
if not DATABASE_URI:
    DATABASE_URI = 'postgresql://reurb_user:D0O9OAg8B0921t0C9RHhk42Ft9noVGXr@dpg-d39l3q0dl3ps73aavla0-a.oregon-postgres.render.com/reurb_apk_zr6m'
    print("AVISO: Usando banco de dados de produção para desenvolvimento local.")


UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')

app.config['SECRET_KEY'] = SECRET_KEY
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
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
    
    # Relacionamentos
    construcoes = db.relationship("Construcao", backref="cadastro", lazy=True, cascade="all, delete-orphan")
    guias_iptu = db.relationship("GuiaIPTU", backref="cadastro", lazy=True, cascade="all, delete-orphan")

# ---> COLE AQUI A NOVA FUNÇÃO <---
    def to_dict(self):
        # 1. Copia automaticamente todas as colunas do cadastro
        dados = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        
        # 2. Formata as datas para não quebrar a tela
        if self.data_criacao: 
            dados['data_criacao'] = self.data_criacao.strftime('%d/%m/%Y %H:%M')
        if self.data_atualizacao: 
            dados['data_atualizacao'] = self.data_atualizacao.strftime('%d/%m/%Y %H:%M')
            
        # 3. Adiciona as construções (se existirem)
        if hasattr(self, 'construcoes') and self.construcoes:
            try: dados['construcoes'] = [c.to_dict() for c in self.construcoes]
            except: pass
            
        # 4. A CORREÇÃO: Puxar os valores da última guia gerada!
        guia = self.guias_iptu[-1] if self.guias_iptu else None
        
        dados['vvi'] = round(getattr(guia, 'vvi', 0.0), 2) if guia else 0.0
        dados['vvt'] = round(getattr(guia, 'vvt', 0.0), 2) if guia else 0.0
        dados['vvc'] = round(getattr(guia, 'vvc', 0.0), 2) if guia else 0.0
        
        valor_iptu = getattr(guia, 'iptu', getattr(guia, 'valor_iptu', getattr(guia, 'valor', 0.0))) if guia else 0.0
        dados['iptu'] = round(valor_iptu, 2)
        
        return dados

class Construcao(db.Model):
    __tablename__ = 'construcoes'
    id = db.Column(db.Integer, primary_key=True)
    cadastro_id = db.Column(db.Integer, db.ForeignKey('cadastros_reurb.id'), nullable=False)
    
    nome = db.Column(db.String(150), nullable=False)
    area_construida = db.Column(db.Float)
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
            tipo_uso = getattr(cadastro, 'const_uso', None) or \
                       getattr(cadastro, 'uso_principal', None) or \
                       getattr(cadastro, 'uso_imovel', None)

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
        cadastro_data = {key: getattr(cadastro, key) for key in CadastroReurb.__table__.columns.keys()}
        
        valores = CalculoTributarioService.calcular_valores(cadastro)
        renda, outro_imovel = cadastro.reurb_renda_familiar or 0, cadastro.reurb_outro_imovel or ''
        tipo_reurb = 'REURB-S' if renda <= 7500 and outro_imovel.lower() == 'nao' else 'REURB-E'
        cadastro_data['tipo_reurb'] = tipo_reurb
        cadastro_data.update(valores)

        cadastro_data['documentos'] = [{'id': d.id, 'nome_arquivo': d.nome_arquivo, 'tipo_documento': d.tipo_documento} for d in cadastro.documentos]
        cadastro_data['construcoes'] = [{col.name: getattr(construcao, col.name) for col in construcao.__table__.columns} for construcao in cadastro.construcoes]
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
        return jsonify({'mensagem': 'Cadastro atualizado com sucesso!'})

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

@app.route('/api/importar', methods=['POST'])
@token_required
@admin_required
def importar_dados(current_user):
    if 'arquivo' not in request.files: return jsonify({'erro': 'Nenhum arquivo enviado'}), 400
    file = request.files['arquivo']
    if file.filename == '': return jsonify({'erro': 'Nome de arquivo vazio'}), 400
    if file:
        try:
            df = pd.read_csv(file) if file.filename.endswith('.csv') else pd.read_excel(file)
            column_mapping = {
                'Nome do Requerente': 'req_nome', 'CPF do Requerente': 'req_cpf',
                'Inscrição Imobiliária': 'inscricao_imobiliaria', 'Área Total do Lote (m²)': 'imovel_area_total',
                'Renda Familiar (R$)': 'reurb_renda_familiar'}
            df.rename(columns=column_mapping, inplace=True)
            float_cols = ['imovel_area_total', 'reurb_renda_familiar', 'latitude', 'longitude']
            for col in float_cols:
                if col in df.columns: df[col] = pd.to_numeric(df[col], errors='coerce')
            df = df.where(pd.notnull(df), None)
            for _, row in df.iterrows():
                valid_data = {k: v for k, v in row.to_dict().items() if k in CadastroReurb.__table__.columns.keys()}
                db.session.add(CadastroReurb(**valid_data))
            db.session.commit()
            return jsonify({'mensagem': 'Dados importados com sucesso!'}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'erro': f'Erro ao importar dados: {e}'}), 500
    return jsonify({'erro': 'Tipo de arquivo não suportado'}), 400

@app.route('/api/exportar', methods=['POST'])
@token_required
def exportar_dados(current_user):
    try:
        data = request.get_json()
        colunas_selecionadas = data.get('colunas')
        if not colunas_selecionadas:
            return jsonify({'erro': 'Nenhuma coluna selecionada.'}), 400

        cadastros_db = CadastroReurb.query.options(joinedload(CadastroReurb.construcoes)).all()
        if not cadastros_db:
            return jsonify({'erro': 'Não há dados para exportar.'}), 404

        dados_para_exportar = []
        for c in cadastros_db:
            cadastro_data = {col.name: getattr(c, col.name) for col in c.__table__.columns}

            valores = CalculoTributarioService.calcular_valores(c)
            renda = c.reurb_renda_familiar or 0
            outro_imovel = c.reurb_outro_imovel or ''
            tipo_reurb = 'REURB-S' if renda <= 7500 and outro_imovel.lower() == 'nao' else 'REURB-E'

            cadastro_data['tipo_reurb'] = tipo_reurb
            cadastro_data.update(valores)

            total_area_construida = sum(construcao.area_construida or 0 for construcao in c.construcoes)
            cadastro_data['imovel_area_construida'] = total_area_construida
            
            for key, value in cadastro_data.items():
                if isinstance(value, (datetime.datetime, datetime.date)):
                    cadastro_data[key] = value.strftime('%d/%m/%Y')
            
            dados_para_exportar.append(cadastro_data)
        
        df = pd.DataFrame(dados_para_exportar)
        colunas_validas = [col for col in colunas_selecionadas if col in df.columns]
        
        df = df[colunas_validas]

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Cadastros')
        output.seek(0)
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheet.sheet',
            as_attachment=True,
            download_name='cadastros_reurb.xlsx'
        )
    except Exception as e:
        app.logger.error(f"Erro ao exportar dados: {str(e)}")
        return jsonify({'erro': f'Erro inesperado: {e}'}), 500

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
            if not Usuario.query.filter_by(usuario="admin").first():
                admin_user = Usuario(nome="Administrador", usuario="admin", senha="admin", acesso="Administrador")
                db.session.add(admin_user)
                db.session.commit()
                return "Banco de dados e usuário admin criados! Login: admin / Senha: admin"
            return "Tabelas já criadas e usuário 'admin' já existe!"
    except Exception as e:
        return f"Erro ao configurar o banco de dados: {str(e)}"

# =======================================================================
# INICIALIZAÇÃO
# =======================================================================
if __name__ == '__main__':
    with app.app_context():
        print("Executando em modo de desenvolvimento local...")
    app.run(host='0.0.0.0', port=5000, debug=True)