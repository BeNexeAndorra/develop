import librosa
import librosa.display
import numpy as np
from pydub import AudioSegment
import os
import tempfile
import warnings
import sys
import logging
import soundfile as sf # Para leer/escribir archivos de audio con librosa

# --- INICIO DE LA MODIFICACIÓN (Añadir estas líneas) ---
# Establecer la ruta a los ejecutables de FFmpeg para pydub
# Esto es específico para macOS y Homebrew. Ajusta si tu FFmpeg está en otro lugar.
AudioSegment.converter = "/usr/local/bin/ffmpeg"
AudioSegment.ffmpeg = "/usr/local/bin/ffmpeg"
AudioSegment.ffprobe = "/usr/local/bin/ffprobe"
# --- FIN DE LA MODIFICACIÓN ---

# Configuración del logger para audio_analysis
audio_logger = logging.getLogger('audio_analysis_module')
audio_logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler(sys.stderr)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
audio_logger.addHandler(handler)

# Suprimir advertencias de Librosa que no afectan el análisis
warnings.filterwarnings('ignore', category=UserWarning, module='librosa')
warnings.filterwarnings('ignore', message='Deprecated API: librosa.core.frames_to_time', module='librosa')

# Mapeo de claves musicales a notación Camelot
# Esto debe ser consistente con playlist_generation.py
KEY_TO_CAMELOT = {
    'C': '8B', 'C#': '3B', 'Db': '3B', 'D': '10B', 'D#': '5B', 'Eb': '5B',
    'E': '12B', 'F': '7B', 'F#': '2B', 'Gb': '2B', 'G': '9B', 'G#': '4B',
    'Ab': '4B', 'A': '11B', 'A#': '6B', 'Bb': '6B', 'B': '1B',

    'Cm': '5A', 'C#m': '12A', 'Dbm': '12A', 'Dm': '7A', 'D#m': '2A', 'Ebm': '2A',
    'Em': '9A', 'Fm': '4A', 'F#m': '11A', 'Gbm': '11A', 'Gm': '6A', 'G#m': '1A',
    'Abm': '1A', 'Am': '8A', 'A#m': '3A', 'Bbm': '3A', 'Bm': '10A'
}

def analyze_audio(filepath: str, temp_dir: str) -> dict:
    """
    Analiza un archivo de audio para extraer BPM, clave musical, energía y duración.
    filepath: Ruta al archivo de audio.
    temp_dir: Directorio temporal para guardar archivos intermedios si es necesario.
    Retorna un diccionario con los resultados del análisis.
    """
    audio_logger.info(f"Iniciando análisis de audio para: {filepath}")
    audio_logger.debug(f"PATH de la aplicación: {os.environ.get('PATH')}") # <-- Línea de depuración para PATH
    audio_logger.debug(f"Sys executable: {sys.executable}") # <-- Línea de depuración para ejecutable

    analysis_results = {
        'filename': os.path.basename(filepath),
        'filepath': filepath,
        'bpm': None,
        'key': None,
        'camelot_key': None,
        'energy': None,
        'duration': None,
        'error_message': None
    }

    if not os.path.exists(filepath):
        error_msg = f"Error: El archivo no existe en la ruta especificada: {filepath}"
        audio_logger.error(error_msg)
        analysis_results['error_message'] = error_msg
        return analysis_results

    try:
        # pydub requiere ffmpeg o avconv en el PATH, o especificado directamente como arriba
        audio = AudioSegment.from_file(filepath)
        analysis_results['duration'] = len(audio) / 1000.0  # Duración en segundos

        # pydub.AudioSegment.get_array_of_samples() devuelve un array de enteros,
        # para librosa se necesita float.
        y = np.array(audio.get_array_of_samples()).astype(np.float32)
        
        # Si el audio es estéreo y pydub lo devuelve entrelazado, hay que separarlo
        if audio.channels == 2:
            # Dividir en canales izquierdo y derecho
            y_left = y[0::2]
            y_right = y[1::2]
            # Podríamos promediar o usar un solo canal para el análisis
            y = (y_left + y_right) / 2.0
        
        sr = audio.frame_rate

        # 1. Análisis de BPM (Tempo)
        onset_env = librosa.onset.onset_detect(y=y, sr=sr)
        tempo, _ = librosa.beat.beat_track(onset_env=onset_env, sr=sr)
        analysis_results['bpm'] = round(float(tempo), 2)

        # 2. Análisis de Clave Musical
        # Utilizar armónicos y percusivos para un mejor análisis de clave
        y_harmonic, y_percussive = librosa.effects.hpss(y)
        chroma = librosa.feature.chroma_cqt(y=y_harmonic, sr=sr)
        key_mode = librosa.feature.key_mode(chroma=chroma, sr=sr)
        
        # librosa 0.10.0+ devuelve 'key' y 'mode' por separado
        # key_mode[0] es la clave (ej. 'C'), key_mode[1] es el modo (0 para menor, 1 para mayor)
        detected_key = librosa.key_to_note(key_mode[0], major=key_mode[1] == 1)
        analysis_results['key'] = detected_key
        analysis_results['camelot_key'] = KEY_TO_CAMELOT.get(detected_key, None)

        # 3. Análisis de Energía (RMS)
        # RMS (Root Mean Square) es una buena medida de la energía o volumen del audio
        rms = librosa.feature.rms(y=y)[0]
        energy = np.mean(rms)
        analysis_results['energy'] = round(float(energy), 4)

        audio_logger.info(f"Análisis completado para {os.path.basename(filepath)}: BPM={analysis_results['bpm']}, Key={analysis_results['key']} ({analysis_results['camelot_key']}), Energy={analysis_results['energy']}, Duration={analysis_results['duration']}s")

    except Exception as e:
        error_msg = f"Error al analizar el archivo de audio '{os.path.basename(filepath)}': {e}"
        audio_logger.error(error_msg, exc_info=True)
        analysis_results['error_message'] = error_msg

    return analysis_results

if __name__ == '__main__':
    # --- PRUEBA DE FUNCIONALIDAD ---
    audio_logger.info("Iniciando prueba de audio_analysis.py")

    # Crear un archivo MP3 dummy para la prueba
    test_output_dir = os.path.join(tempfile.gettempdir(), 'dj_temp_test')
    os.makedirs(test_output_dir, exist_ok=True)
    test_mp3_path = os.path.join(test_output_dir, 'test_audio.mp3')

    try:
        # Generar un AudioSegment simple (silencio de 5 segundos)
        # Esto sirve para probar que pydub y ffmpeg pueden inicializarse.
        # Para un análisis más significativo, usarías un archivo MP3 real.
        silent_audio = AudioSegment.silent(duration=5000, frame_rate=44100) # 5 segundos de silencio a 44.1kHz
        silent_audio.export(test_mp3_path, format="mp3")
        audio_logger.info(f"Archivo MP3 de prueba generado en: {test_mp3_path}")

        results = analyze_audio(test_mp3_path, test_output_dir)
        audio_logger.info("\n--- Resultados del Análisis del Archivo de Prueba ---")
        for key, value in results.items():
            audio_logger.info(f"{key}: {value}")

        if results['bpm'] is None or results['key'] is None or results['energy'] is None or results['duration'] is None:
            audio_logger.warning("\nADVERTENCIA: El análisis del archivo de prueba ha fallado en una o más propiedades.")
            audio_logger.warning("Esto indica un problema subyacente con la instalación de FFmpeg o dependencias de audio.")
            if results['error_message']:
                audio_logger.warning(f"Mensaje de error específico: {results['error_message']}")
        else:
            audio_logger.info("\nAnálisis de prueba completado con éxito.")

    except Exception as e:
        audio_logger.critical(f"Error crítico durante la prueba de audio_analysis.py: {e}", exc_info=True)
        audio_logger.critical("Asegúrate de que FFmpeg está instalado y en tu PATH.")

    finally:
        # Limpiar archivo dummy
        if os.path.exists(test_mp3_path):
            try:
                os.remove(test_mp3_path)
                audio_logger.info("Archivo de prueba limpiado.")
            except Exception as e:
                audio_logger.warning(f"No se pudo eliminar el archivo de prueba {test_mp3_path}: {e}")

        # Limpiar directorio temporal
        if os.path.exists(test_output_dir):
            try:
                # Solo se borrará si está vacío, si no, es porque algún proceso dejó algo.
                os.rmdir(test_output_dir) 
                audio_logger.info(f"Directorio de salida de prueba limpiado: {test_output_dir}")
            except OSError as e:
                audio_logger.warning(f"El directorio de prueba de salida {test_output_dir} no está vacío o no se pudo borrar: {e}")

    audio_logger.info("Prueba de audio_analysis.py finalizada.")