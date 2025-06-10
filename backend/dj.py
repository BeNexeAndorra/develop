import os
import sys
import tempfile
import time
import json
import logging
import urllib.parse
from flask import Blueprint, jsonify, request, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename
import shutil # Import shutil if you plan to use shutil.rmtree for directory removal, otherwise os.unlink and os.rmdir are used

# Configuración del logger para este Blueprint
dj_logger = logging.getLogger('dj_blueprint')
dj_logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler(sys.stderr)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
dj_logger.addHandler(handler)


# Agrega el directorio actual (donde está dj.py y los otros módulos) a sys.path.
# Esto asegura que Python pueda encontrar audio_analysis, playlist_generation, etc.
# Si app.py y dj.py están en el mismo directorio raíz, esta es la configuración correcta.
project_root = os.path.abspath(os.path.dirname(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
    dj_logger.debug(f"Añadido '{project_root}' a sys.path para importación de módulos.")


# Importar nuestros módulos personalizados
CUSTOM_MODULES_LOADED = False
try:
    import audio_analysis
    import playlist_generation
    import mixing_engine
    import apple_music_integration
    CUSTOM_MODULES_LOADED = True
    dj_logger.info("Módulos personalizados cargados con éxito.")
except ImportError as e:
    dj_logger.error(f"ERROR: No se pudieron cargar uno o más módulos personalizados: {e}")
    # No salir aquí para permitir que la aplicación se inicie, pero las funcionalidades
    # que dependen de estos módulos fallarán. Esto es útil para depuración.


# Directorios temporales para las cargas y las mezclas
# Es importante que estos directorios sean accesibles tanto para la carga como para el análisis
UPLOAD_FOLDER = os.path.join(tempfile.gettempdir(), 'dj_uploads')
MIX_OUTPUT_FOLDER = os.path.join(tempfile.gettempdir(), 'dj_mixes')

# Asegurarse de que los directorios existen
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(MIX_OUTPUT_FOLDER, exist_ok=True)
dj_logger.info(f"Directorio de subidas: {UPLOAD_FOLDER}")
dj_logger.info(f"Directorio de mezclas: {MIX_OUTPUT_FOLDER}")


# Almacén de archivos subidos y sus análisis
# { 'unique_id': {'filename': '...', 'filepath': '...', 'bpm': '...', ...}}
uploaded_files_analysis = {}

# Estado global para el progreso de la mezcla
mix_status = {
    'status': 'idle', # 'idle', 'processing', 'completed', 'error'
    'progress': 0,    # 0-100
    'message': 'Listo para comenzar.',
    'output_file': None,
    'error_details': None
}

# Crear un Blueprint
dj_bp = Blueprint('dj', __name__)
CORS(dj_bp) # Habilita CORS para este Blueprint

# Función para limpiar archivos temporales
def clear_temp_files():
    global uploaded_files_analysis, mix_status
    
    success = True
    messages = []

    # Limpiar directorio de subidas
    if os.path.exists(UPLOAD_FOLDER):
        try:
            shutil.rmtree(UPLOAD_FOLDER)
            os.makedirs(UPLOAD_FOLDER, exist_ok=True) # Volver a crear el directorio vacío
            messages.append(f"Directorio de subidas '{UPLOAD_FOLDER}' limpiado.")
            dj_logger.info(messages[-1])
        except Exception as e:
            messages.append(f"Error al limpiar directorio de subidas '{UPLOAD_FOLDER}': {e}")
            dj_logger.error(messages[-1], exc_info=True)
            success = False

    # Limpiar directorio de mezclas
    if os.path.exists(MIX_OUTPUT_FOLDER):
        try:
            shutil.rmtree(MIX_OUTPUT_FOLDER)
            os.makedirs(MIX_OUTPUT_FOLDER, exist_ok=True) # Volver a crear el directorio vacío
            messages.append(f"Directorio de mezclas '{MIX_OUTPUT_FOLDER}' limpiado.")
            dj_logger.info(messages[-1])
        except Exception as e:
            messages.append(f"Error al limpiar directorio de mezclas '{MIX_OUTPUT_FOLDER}': {e}")
            dj_logger.error(messages[-1], exc_info=True)
            success = False

    # Resetear el estado de la aplicación
    uploaded_files_analysis = {}
    mix_status = {
        'status': 'idle',
        'progress': 0,
        'message': 'Listo para comenzar.',
        'output_file': None,
        'error_details': None
    }
    messages.append("Estado de la aplicación reseteado.")
    dj_logger.info(messages[-1])

    return success, messages


@dj_bp.route('/api/upload', methods=['POST'])
def upload_file():
    if 'audio_file' not in request.files:
        return jsonify({'error': 'No se encontró el archivo en la solicitud'}), 400

    file = request.files['audio_file']
    if file.filename == '':
        return jsonify({'error': 'No se seleccionó ningún archivo'}), 400

    if file:
        filename = secure_filename(file.filename)
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)
        dj_logger.info(f"Archivo MP3 '{filename}' guardado en {filepath}")

        # Generar un ID único para el archivo subido
        unique_id = str(time.time()).replace('.', '') # Timestamp como ID único
        
        try:
            # Pasa el UPLOAD_FOLDER como temp_dir para analyze_audio
            analysis_result = audio_analysis.analyze_audio(filepath, UPLOAD_FOLDER)
            
            if analysis_result['error_message']:
                raise RuntimeError(f"Error en el análisis de audio: {analysis_result['error_message']}")

            # Almacenar el resultado del análisis con el ID único
            uploaded_files_analysis[unique_id] = {
                'id': unique_id,
                'filename': filename,
                'filepath': filepath, # Guarda la ruta completa aquí
                'bpm': analysis_result['bpm'],
                'key': analysis_result['key'],
                'camelot_key': analysis_result['camelot_key'],
                'energy': analysis_result['energy'],
                'duration': analysis_result['duration']
            }
            dj_logger.info(f"Análisis completado para '{filename}': {uploaded_files_analysis[unique_id]}")
            return jsonify({
                'message': 'Archivo cargado y analizado con éxito',
                'file_id': unique_id,
                'analysis': uploaded_files_analysis[unique_id]
            }), 200
        except Exception as e:
            dj_logger.error(f"Error al procesar el archivo '{filename}': {e}", exc_info=True)
            # Limpiar el archivo subido si falla el análisis
            if os.path.exists(filepath):
                os.remove(filepath)
                dj_logger.warning(f"Archivo '{filename}' eliminado debido a error de procesamiento.")
            return jsonify({'error': f'Error al procesar el archivo: {e}'}), 500

@dj_bp.route('/api/upload-xml', methods=['POST'])
def upload_xml():
    if 'xml_file' not in request.files:
        return jsonify({'error': 'No se encontró el archivo XML en la solicitud'}), 400

    file = request.files['xml_file']
    if file.filename == '':
        return jsonify({'error': 'No se seleccionó ningún archivo XML'}), 400

    if file:
        filename = secure_filename(file.filename)
        filepath = os.path.join(UPLOAD_FOLDER, filename) # Guardar XML en el mismo directorio temporal
        file.save(filepath)
        dj_logger.info(f"Archivo XML '{filename}' guardado en {filepath}")

        try:
            # Parsear el XML y extraer las pistas
            # Asume que normalize_filepath maneja la decodificación correctamente
            itunes_tracks = apple_music_integration.parse_itunes_xml(filepath)
            
            if not itunes_tracks:
                raise ValueError("No se encontraron pistas en el archivo XML o el formato es incorrecto.")

            newly_processed_tracks = []
            for track_data in itunes_tracks:
                track_path = track_data.get('filepath')
                if track_path and os.path.exists(track_path):
                    # Generar un ID único para la pista
                    unique_id = str(time.time()) + "_" + secure_filename(track_data['filename']) # Combinar timestamp y nombre de archivo para evitar colisiones simples
                    # Normalizar el ID eliminando caracteres no deseados si existen del secure_filename
                    unique_id = urllib.parse.quote_plus(unique_id).replace('.', '') # Asegura que sea seguro para usar como clave y elimina puntos

                    # Realizar análisis de audio para cada pista
                    dj_logger.info(f"Analizando pista de iTunes: {track_data.get('filename')} desde {track_path}")
                    
                    # Pasa el UPLOAD_FOLDER como temp_dir para analyze_audio
                    analysis_result = audio_analysis.analyze_audio(track_path, UPLOAD_FOLDER)

                    if analysis_result['error_message']:
                        dj_logger.warning(f"Análisis fallido para {track_data.get('filename')}: {analysis_result['error_message']}")
                        continue # Saltar esta pista si falla el análisis

                    # Actualizar los datos de la pista con los resultados del análisis
                    track_data.update({
                        'id': unique_id,
                        'bpm': analysis_result['bpm'],
                        'key': analysis_result['key'],
                        'camelot_key': analysis_result['camelot_key'],
                        'energy': analysis_result['energy'],
                        'duration': analysis_result['duration']
                    })
                    uploaded_files_analysis[unique_id] = track_data
                    newly_processed_tracks.append(track_data)
                    dj_logger.info(f"Pista de iTunes analizada y añadida: {track_data.get('filename')} (ID: {unique_id})")
                else:
                    dj_logger.warning(f"Ruta de archivo no encontrada o inválida para la pista: {track_data.get('filename')} en {track_path}")

            # Limpiar el archivo XML después de procesarlo
            if os.path.exists(filepath):
                os.remove(filepath)
                dj_logger.info(f"Archivo XML temporal '{filepath}' limpiado.")

            return jsonify({
                'message': f'{len(newly_processed_tracks)} pistas importadas y analizadas con éxito desde el XML.',
                'imported_tracks': newly_processed_tracks
            }), 200

        except Exception as e:
            dj_logger.error(f"Error al procesar el archivo XML '{filename}': {e}", exc_info=True)
            # Limpiar el archivo XML si falla el procesamiento
            if os.path.exists(filepath):
                os.remove(filepath)
                dj_logger.warning(f"Archivo XML '{filename}' eliminado debido a error de procesamiento.")
            return jsonify({'error': f'Error al procesar el archivo XML: {e}'}), 500


@dj_bp.route('/api/files', methods=['GET'])
def get_files():
    # Devuelve la lista de archivos cargados y analizados
    # Convertimos el diccionario a una lista de sus valores para facilitar el manejo en el frontend
    files_list = list(uploaded_files_analysis.values())
    dj_logger.debug(f"Sirviendo {len(files_list)} archivos analizados.")
    return jsonify(files_list), 200

@dj_bp.route('/api/generate-playlist', methods=['POST'])
def generate_playlist_route():
    data = request.get_json()
    track_ids = data.get('track_ids', [])

    if not track_ids:
        return jsonify({'error': 'No se proporcionaron IDs de pistas para generar la lista de reproducción.'}), 400

    selected_tracks_for_playlist = []
    for track_id in track_ids:
        track_info = uploaded_files_analysis.get(track_id)
        if track_info:
            selected_tracks_for_playlist.append(track_info)
        else:
            dj_logger.warning(f"ID de pista no encontrado: {track_id}")

    if not selected_tracks_for_playlist:
        return jsonify({'error': 'Ninguna de las pistas seleccionadas se encontró o analizó previamente.'}), 400

    try:
        dj_logger.info(f"Generando lista de reproducción con {len(selected_tracks_for_playlist)} pistas seleccionadas.")
        generated_playlist = playlist_generation.generate_playlist(selected_tracks_for_playlist)
        dj_logger.info("Lista de reproducción generada con éxito.")
        return jsonify(generated_playlist), 200
    except Exception as e:
        dj_logger.error(f"Error al generar la lista de reproducción: {e}", exc_info=True)
        return jsonify({'error': f'Error al generar la lista de reproducción: {e}'}), 500

@dj_bp.route('/api/generate-mix', methods=['POST'])
def generate_mix():
    global mix_status
    data = request.get_json()
    playlist_tracks = data.get('playlist', [])

    if not playlist_tracks:
        return jsonify({'error': 'No se proporcionaron pistas para generar la mezcla.'}), 400

    # Validar que todas las pistas en la playlist tienen 'filepath'
    for track in playlist_tracks:
        if 'filepath' not in track or not os.path.exists(track['filepath']):
            return jsonify({'error': f"La pista '{track.get('filename', 'Unknown')}' no tiene una ruta de archivo válida o el archivo no existe."}), 400

    # Iniciar la generación de la mezcla en un hilo o proceso separado
    # Esto es crucial para no bloquear la solicitud HTTP
    mix_status = {
        'status': 'processing',
        'progress': 0,
        'message': 'Iniciando la generación de la mezcla...',
        'output_file': None,
        'error_details': None
    }
    
    # Para simplificar, aquí no se usa un hilo separado. En una aplicación real,
    # se usaría `threading.Thread` o `multiprocessing.Process` para esto.
    # Por ahora, la mezcla se ejecutará de forma síncrona.
    dj_logger.info("Iniciando proceso de mezcla (síncrono por simplicidad).")
    try:
        # Pasa MIX_OUTPUT_FOLDER al mixing_engine
        output_mix_filename = mixing_engine.create_full_mix(playlist_tracks, MIX_OUTPUT_FOLDER, mix_status)
        
        mix_status['status'] = 'completed'
        mix_status['progress'] = 100
        mix_status['message'] = 'Mezcla generada con éxito.'
        mix_status['output_file'] = output_mix_filename
        dj_logger.info(f"Mezcla completada: {output_mix_filename}")
        return jsonify(mix_status), 200

    except ValueError as ve:
        mix_status['status'] = 'error'
        mix_status['message'] = f'Error de validación: {str(ve)}'
        dj_logger.error(f"Error de validación al generar la mezcla: {ve}")
        return jsonify(mix_status), 400
    except Exception as e:
        mix_status['status'] = 'error'
        mix_status['message'] = f'Error inesperado durante la mezcla: {str(e)}'
        dj_logger.error(f"Error inesperado al generar la mezcla: {e}", exc_info=True)
        return jsonify(mix_status), 500

@dj_bp.route('/api/mix-status', methods=['GET'])
def get_mix_status():
    """Devuelve el estado actual de la generación de la mezcla."""
    dj_logger.debug(f"Verificando estado de la mezcla: {mix_status['status']}")
    return jsonify(mix_status), 200

@dj_bp.route('/api/download-mix/<filename>', methods=['GET'])
def download_mix(filename):
    """Permite la descarga del archivo de mezcla generado."""
    output_mix_dir = MIX_OUTPUT_FOLDER
    full_path = os.path.join(output_mix_dir, filename)

    if os.path.exists(full_path) and os.path.isfile(full_path):
        dj_logger.info(f"Sirviendo archivo de mezcla: {full_path}")
        # Usar as_attachment=True para forzar la descarga en el navegador
        return send_file(full_path, as_attachment=True, download_name=filename, mimetype='audio/mp3')
    else:
        dj_logger.warning(f"Intento de descarga de archivo no encontrado: {full_path}")
        return jsonify({'error': 'Archivo de mezcla no encontrado.'}), 404

@dj_bp.route('/api/clear-files', methods=['POST'])
def clear_files_route():
    """
    API endpoint to clear all temporary files.
    """
    global uploaded_files_analysis
    dj_logger.info("Recibida solicitud para limpiar todos los archivos temporales.")
    result = clear_temp_files()
    if result[0]: # result[0] es el booleano de éxito
        return jsonify({'message': 'Archivos temporales y estado limpiados con éxito.', 'details': result[1]}), 200
    else:
        return jsonify({'error': 'Algunos archivos no pudieron ser limpiados.', 'details': result[1]}), 500