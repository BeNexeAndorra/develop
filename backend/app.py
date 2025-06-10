from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
import os
import sys
import logging
import tempfile
import shutil # Para borrar directorios completos

# Configura el logger para la aplicación principal de Flask
# Configuración global para todos los loggers
logging.basicConfig(stream=sys.stderr, level=logging.DEBUG,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

app_logger = logging.getLogger('flask_app_main')
app_logger.setLevel(logging.DEBUG)

# --- INICIO DE LA MODIFICACIÓN (Mantener esta sección para asegurar el PATH) ---
# Añadir la ruta de Homebrew de FFmpeg al PATH del entorno
# Esto es específico para macOS y Homebrew. Ajusta si tu FFmpeg está en otro lugar.
ffmpeg_path = "/usr/local/bin"
if ffmpeg_path not in os.environ["PATH"]:
    os.environ["PATH"] += os.pathsep + ffmpeg_path
    app_logger.debug(f"Añadido {ffmpeg_path} al PATH del entorno de la aplicación.")
# --- FIN DE LA MODIFICACIÓN ---

# Importa el Blueprint dj_bp desde tu módulo dj.py
try:
    # Importar las variables de carpeta desde dj.py para que app.py también las conozca
    from dj import dj_bp, UPLOAD_FOLDER, MIX_OUTPUT_FOLDER, clear_temp_files
    app_logger.info("Blueprint 'dj' importado con éxito.")
except ImportError as e:
    app_logger.critical(f"ERROR CRÍTICO: No se pudo importar el Blueprint 'dj' de dj.py: {e}")
    sys.exit(1) # Salir si no se puede importar el Blueprint principal

# Creación de la instancia de la aplicación Flask
app = Flask(__name__)
CORS(app) # Habilita CORS para toda la aplicación

# ====================================================================
# Configuración de Blueprints
# ====================================================================
# Registra el Blueprint 'dj_bp'.
# Como las rutas en dj.py ya son '/api/upload', '/api/generate-mix', etc.,
# NO necesitamos un 'url_prefix' aquí.
app.register_blueprint(dj_bp)
app_logger.info("Blueprint 'dj_bp' registrado.")


# ====================================================================
# Ruta para servir el frontend (index.html)
# ====================================================================
@app.route('/')
def index():
    # Sirve el archivo index.html desde el mismo directorio donde reside app.py
    # send_from_directory sirve el archivo desde ese directorio.
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'index.html')

# ====================================================================
# Manejadores de errores globales para toda la aplicación
# ====================================================================
@app.errorhandler(404)
def not_found_error(error):
    app_logger.error(f"404 Not Found: {request.url}")
    return jsonify({'error': 'URL no encontrada'}), 404

@app.errorhandler(405)
def method_not_allowed_error(error):
    app_logger.error(f"405 Method Not Allowed: Método {request.method} no permitido para {request.url}")
    return jsonify({'error': f'Método {request.method} no permitido para esta URL.'}), 405

@app.errorhandler(Exception)
def handle_unexpected_error(error):
    error_message = f"Un error inesperado ha ocurrido en la aplicación principal: {str(error)}"
    app_logger.exception("Error Inesperado en la aplicación principal:") # Imprimirá el traceback completo
    return jsonify({'error': error_message}), 500


if __name__ == '__main__':
    # Configuración de host y puerto
    host = os.environ.get('FLASK_HOST', '0.0.0.0')
    port = int(os.environ.get('FLASK_PORT', 5001)) # Puerto por defecto 5001

    # Asegúrate de que los directorios temporales existen al inicio
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(MIX_OUTPUT_FOLDER, exist_ok=True)

    app_logger.info(f"Iniciando la aplicación Flask en http://{host}:{port}")
    # Usar debug=True solo en desarrollo. En producción, desactívalo.
    # El modo debug permite el auto-reloader y proporciona un depurador interactivo.
    app.run(debug=True, host=host, port=port, use_reloader=False) # use_reloader=False para evitar doble ejecución con logging.basicConfig